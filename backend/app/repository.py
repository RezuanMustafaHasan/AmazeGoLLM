"""Git supplies boards; Firestore persists only player identities and attempt data."""

import hashlib
import hmac
import json
import os
import secrets
from collections import Counter
from copy import deepcopy
from threading import RLock
from uuid import uuid4

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.app.config import Settings
from backend.app.engine import GameError, now_iso, update_progress
from backend.app.models import Level


def player_credentials():
    player_id = str(uuid4())
    token = f"{player_id}.{secrets.token_urlsafe(32)}"
    return token, {
        "id": player_id,
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "created_at": now_iso(),
        "progress": {},
        "active_session_id": None,
    }


def authenticate(player: dict | None, token: str):
    if not player or not hmac.compare_digest(
        player["token_hash"], hashlib.sha256(token.encode()).hexdigest()
    ):
        raise GameError("Invalid player credentials", 401)
    return player


def public_player(player):
    return {key: deepcopy(value) for key, value in player.items() if key != "token_hash"}


def stored_session(session):
    # The engine uses an in-memory board hydrated from Git. It never reaches Firestore.
    return {key: deepcopy(value) for key, value in session.items() if key != "level"}


def document_data(data):
    return {"payload": json.dumps(data, separators=(",", ":"))}


def player_document(player):
    return {
        **document_data(player),
        "id": player["id"],
        "created_at": player["created_at"],
    }


def session_document(session):
    data = stored_session(session)
    return {
        **document_data(data),
        **{key: data[key] for key in ["id", "player_id", "level_id", "status", "created_at"]},
    }


def read_document(snapshot):
    return json.loads(snapshot.to_dict()["payload"]) if snapshot.exists else None


def progress_after_delete(player, deleted, remaining):
    player = deepcopy(player)
    if player["active_session_id"] == deleted["id"]:
        player["active_session_id"] = None
    if deleted["status"] == "won":
        player["progress"].pop(deleted["level_id"], None)
        for session in sorted(remaining, key=lambda item: item["created_at"]):
            if session["level_id"] == deleted["level_id"] and session["status"] == "won":
                player = update_progress(player, session)
    return player


def page(items, limit, cursor):
    items = sorted(
        (item for item in items if not cursor or item["id"] > cursor), key=lambda item: item["id"]
    )
    return {
        "items": items[:limit],
        "next_cursor": items[limit - 1]["id"] if len(items) > limit else None,
    }


class GitCatalog:
    def set_catalog(self, levels: list[Level]):
        self.catalog = {level.id: level for level in levels}

    def add_levels(self, levels: list[Level]):
        self.catalog.update({level.id: level for level in levels})

    def all_levels(self):
        return sorted(self.catalog.values(), key=lambda level: level.number)

    def level_summaries(self):
        return [level.summary() for level in self.all_levels()]

    def get_level(self, level_id):
        level = self.catalog.get(level_id)
        if level is None:
            raise GameError("Level not found", 404)
        return level

    def hydrate_session(self, session):
        level = self.get_level(session["level_id"])
        if session.get("level_hash") != level.fingerprint:
            raise GameError("The level for this attempt has changed. Start a new attempt.", 404)
        return {**deepcopy(session), "level": level.model_dump(mode="json")}

    def session_summary(self, session):
        keys = [
            "id",
            "player_id",
            "level_id",
            "actor_type",
            "actor_name",
            "status",
            "lives_remaining",
            "moves",
            "mistakes",
            "hints_used",
            "revision",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        return {**{key: session[key] for key in keys}, "action_count": len(session["history"])}


class MemoryRepository(GitCatalog):
    """Explicit opt-in for tests; uses the same board-free stored session format."""

    mode = "memory"

    def __init__(self):
        self.catalog: dict[str, Level] = {}
        self.players: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.lock = RLock()

    def create_player(self):
        token, player = player_credentials()
        with self.lock:
            self.players[player["id"]] = player
        return player["id"], token

    def get_player(self, token):
        with self.lock:
            return deepcopy(authenticate(self.players.get(token.split(".")[0]), token))

    def create_session(self, session):
        with self.lock:
            if session["player_id"] not in self.players:
                raise GameError("Player not found", 401)
            self.sessions[session["id"]] = stored_session(session)
            self.players[session["player_id"]]["active_session_id"] = session["id"]

    def get_session(self, session_id, player_id):
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session["player_id"] != player_id:
                raise GameError("Session not found", 404)
            return self.hydrate_session(session)

    def mutate_session(self, session_id, player_id, operation):
        with self.lock:
            if player_id not in self.players:
                raise GameError("Player not found", 401)
            current = self.get_session(session_id, player_id)
            updated, outcome = operation(current)
            if updated["revision"] != current["revision"]:
                self.sessions[session_id] = stored_session(updated)
                if updated["status"] == "won":
                    self.players[player_id] = update_progress(self.players[player_id], updated)
            return deepcopy(updated), outcome

    def admin_stats(self):
        with self.lock:
            return {
                "players": len(self.players),
                "sessions": len(self.sessions),
                "statuses": dict(Counter(s["status"] for s in self.sessions.values())),
                "levels": len(self.catalog),
            }

    def admin_players(self, limit=100, cursor=None):
        with self.lock:
            return page([public_player(p) for p in self.players.values()], limit, cursor)

    def admin_player(self, player_id):
        with self.lock:
            player = self.players.get(player_id)
            if not player:
                raise GameError("Player not found", 404)
            return public_player(player)

    def admin_sessions(self, limit=100, cursor=None, player_id=None):
        with self.lock:
            return page(
                [
                    self.session_summary(s)
                    for s in self.sessions.values()
                    if not player_id or s["player_id"] == player_id
                ],
                limit,
                cursor,
            )

    def admin_session(self, session_id):
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise GameError("Session not found", 404)
            return deepcopy({key: value for key, value in session.items() if key != "receipts"})

    def delete_session(self, session_id):
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise GameError("Session not found", 404)
            player = self.players.get(session["player_id"])
            if player:
                remaining = [
                    s
                    for key, s in self.sessions.items()
                    if key != session_id and s["player_id"] == session["player_id"]
                ]
                self.players[player["id"]] = progress_after_delete(player, session, remaining)
            del self.sessions[session_id]
            return {"deleted_sessions": 1}

    def delete_player(self, player_id):
        with self.lock:
            if player_id not in self.players:
                raise GameError("Player not found", 404)
            sessions = [key for key, s in self.sessions.items() if s["player_id"] == player_id]
            for key in sessions:
                del self.sessions[key]
            del self.players[player_id]
            return {"deleted_players": 1, "deleted_sessions": len(sessions)}

    def close(self):
        pass


class FirestoreRepository(GitCatalog):
    mode = "firestore"

    def __init__(self, settings: Settings):
        self.catalog: dict[str, Level] = {}
        if settings.google_application_credentials:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
        if settings.firestore_emulator_host:
            os.environ["FIRESTORE_EMULATOR_HOST"] = settings.firestore_emulator_host
        elif settings.firebase_project_id.startswith("demo-"):
            raise RuntimeError("Set FIRESTORE_EMULATOR_HOST or a real FIREBASE_PROJECT_ID in .env")
        credential = None
        if settings.firebase_service_account_json:
            credential = credentials.Certificate(
                json.loads(settings.firebase_service_account_json.get_secret_value())
            )
        self.app = firebase_admin.initialize_app(
            credential=credential,
            options={"projectId": settings.firebase_project_id},
            name=f"amaze-{uuid4()}",
        )
        self.db = firestore.client(app=self.app)
        self.root = self.db.collection("amaze_go").document("v1")
        self.players = self.root.collection("players")
        self.sessions = self.root.collection("sessions")

    def create_player(self):
        token, player = player_credentials()
        self.players.document(player["id"]).create(player_document(player), timeout=15)
        return player["id"], token

    def get_player(self, token):
        player_id = token.split(".")[0]
        if len(player_id) != 36 or "/" in player_id:
            raise GameError("Invalid player credentials", 401)
        return authenticate(read_document(self.players.document(player_id).get(timeout=15)), token)

    def create_session(self, session):
        player_ref = self.players.document(session["player_id"])

        @firestore.transactional
        def create(transaction):
            player = read_document(player_ref.get(transaction=transaction, timeout=15))
            if player is None:
                raise GameError("Player not found", 401)
            player["active_session_id"] = session["id"]
            transaction.create(self.sessions.document(session["id"]), session_document(session))
            transaction.set(player_ref, player_document(player))

        create(self.db.transaction())

    def get_session(self, session_id, player_id):
        session = read_document(self.sessions.document(session_id).get(timeout=15))
        if not session or session["player_id"] != player_id:
            raise GameError("Session not found", 404)
        return self.hydrate_session(session)

    def mutate_session(self, session_id, player_id, operation):
        session_ref = self.sessions.document(session_id)
        player_ref = self.players.document(player_id)

        @firestore.transactional
        def mutate(transaction):
            current = read_document(session_ref.get(transaction=transaction, timeout=15))
            player = read_document(player_ref.get(transaction=transaction, timeout=15))
            if not player:
                raise GameError("Player not found", 401)
            if not current or current["player_id"] != player_id:
                raise GameError("Session not found", 404)
            current = self.hydrate_session(current)
            updated, outcome = operation(current)
            if updated["revision"] != current["revision"]:
                transaction.set(session_ref, session_document(updated))
                if updated["status"] == "won":
                    transaction.set(player_ref, player_document(update_progress(player, updated)))
            return updated, outcome

        return mutate(self.db.transaction())

    def admin_stats(self):
        statuses = Counter(s.to_dict()["status"] for s in self.sessions.select(["status"]).stream())
        return {
            "players": len(list(self.players.select([]).stream(timeout=60))),
            "sessions": sum(statuses.values()),
            "statuses": dict(statuses),
            "levels": len(self.catalog),
        }

    def _page(self, collection, transform, limit, cursor, player_id=None):
        query = collection.order_by("__name__")
        if player_id:
            query = query.where(filter=FieldFilter("player_id", "==", player_id))
        if cursor:
            query = query.start_after({"__name__": collection.document(cursor)})
        docs = list(query.limit(limit + 1).stream(timeout=60))
        return {
            "items": [transform(read_document(s)) for s in docs[:limit]],
            "next_cursor": docs[limit - 1].id if len(docs) > limit else None,
        }

    def admin_players(self, limit=100, cursor=None):
        return self._page(self.players, public_player, limit, cursor)

    def admin_player(self, player_id):
        player = read_document(self.players.document(player_id).get(timeout=15))
        if not player:
            raise GameError("Player not found", 404)
        return public_player(player)

    def admin_sessions(self, limit=100, cursor=None, player_id=None):
        return self._page(self.sessions, self.session_summary, limit, cursor, player_id)

    def admin_session(self, session_id):
        session = read_document(self.sessions.document(session_id).get(timeout=15))
        if not session:
            raise GameError("Session not found", 404)
        return {key: value for key, value in session.items() if key != "receipts"}

    def delete_session(self, session_id):
        ref = self.sessions.document(session_id)

        @firestore.transactional
        def delete(transaction):
            session = read_document(ref.get(transaction=transaction, timeout=15))
            if not session:
                raise GameError("Session not found", 404)
            player_ref = self.players.document(session["player_id"])
            player = read_document(player_ref.get(transaction=transaction, timeout=15))
            remaining = []
            if player and session["status"] == "won":
                query = self.sessions.where(filter=FieldFilter("player_id", "==", player["id"]))
                remaining = [
                    read_document(s)
                    for s in query.get(transaction=transaction)
                    if s.id != session_id
                ]
            transaction.delete(ref)
            if player:
                transaction.set(
                    player_ref, player_document(progress_after_delete(player, session, remaining))
                )

        delete(self.db.transaction())
        return {"deleted_sessions": 1}

    def delete_player(self, player_id):
        ref = self.players.document(player_id)

        @firestore.transactional
        def delete(transaction):
            player = read_document(ref.get(transaction=transaction, timeout=15))
            if not player:
                raise GameError("Player not found", 404)
            transaction.delete(ref)

        # Deleting the identity first prevents concurrent attempts from creating or
        # updating orphan sessions. Session transactions also read the player document.
        delete(self.db.transaction())
        refs = [
            s.reference
            for s in self.sessions.where(filter=FieldFilter("player_id", "==", player_id))
            .select([])
            .stream(timeout=60)
        ]
        for offset in range(0, len(refs), 400):
            batch = self.db.batch()
            for session_ref in refs[offset : offset + 400]:
                batch.delete(session_ref)
            batch.commit(timeout=60)
        return {"deleted_players": 1, "deleted_sessions": len(refs)}

    def migrate_git_catalog(self):
        """Explicit one-time migration; validate all legacy boards before any write."""
        legacy = self.root.collection("levels")
        refs = [s.reference for s in legacy.select([]).stream(timeout=60)]
        writes = []
        for snapshot in self.sessions.stream(timeout=60):
            session = read_document(snapshot)
            level = self.get_level(session["level_id"])
            if "level" in session:
                if Level.model_validate(session["level"]).fingerprint != level.fingerprint:
                    raise ValueError(
                        f"Attempt {snapshot.id} uses a different board; migration stopped"
                    )
                session.pop("level")
                session["level_hash"] = level.fingerprint
            elif "level_hash" not in session:
                raise ValueError(f"Attempt {snapshot.id} has no level fingerprint")
            writes.append((snapshot.reference, session_document(session)))
        for snapshot in self.players.stream(timeout=60):
            writes.append((snapshot.reference, player_document(read_document(snapshot))))
        for offset in range(0, len(writes), 400):
            batch = self.db.batch()
            for reference, data in writes[offset : offset + 400]:
                batch.set(reference, data)
            batch.commit(timeout=60)
        for offset in range(0, len(refs), 400):
            batch = self.db.batch()
            for reference in refs[offset : offset + 400]:
                batch.delete(reference)
            batch.commit(timeout=60)
        return {"removed_level_documents": len(refs), "updated_records": len(writes)}

    def close(self):
        self.db.close()
        firebase_admin.delete_app(self.app)


def build_repository(settings: Settings):
    return (
        MemoryRepository()
        if settings.storage_backend == "memory"
        else FirestoreRepository(settings)
    )
