"""Durable agent runs and bounded, separately stored turn transcripts."""

from copy import deepcopy

from firebase_admin import firestore

from backend.app.engine import GameError


def public_run(run):
    return {k: deepcopy(v) for k, v in run.items() if k not in {"credential", "pending"}}


class MemoryAgentStore:
    def agent_runs_for_player(self, player_id):
        with self.lock:
            return [deepcopy(r) for r in self.agent_runs.values() if r["player_id"] == player_id]

    def remove_agent_records_for_player(self, player_id):
        with self.lock:
            ids = {r["id"] for r in self.agent_runs_for_player(player_id)}
            for run_id in ids:
                del self.agent_runs[run_id]
            self.agent_turns = {k: v for k, v in self.agent_turns.items() if k[0] not in ids}

    def create_agent_run(self, run):
        with self.lock:
            self.agent_runs[run["id"]] = deepcopy(run)

    def get_agent_run(self, run_id):
        with self.lock:
            if run_id not in self.agent_runs:
                raise GameError("Agent session not found", 404)
            return deepcopy(self.agent_runs[run_id])

    def mutate_agent_run(self, run_id, operation):
        with self.lock:
            updated, turn = operation(self.get_agent_run(run_id))
            self.agent_runs[run_id] = deepcopy(updated)
            if turn:
                self.agent_turns[run_id, turn["id"]] = deepcopy(turn)
            return deepcopy(updated)

    def list_agent_runs(self, limit=100, cursor=None):
        from backend.app.repository import page

        with self.lock:
            return page([public_run(r) for r in self.agent_runs.values()], limit, cursor)

    def get_agent_turn(self, run_id, turn_id):
        with self.lock:
            return deepcopy(self.agent_turns.get((run_id, turn_id)))

    def list_agent_turns(self, run_id, limit=50, before=None):
        with self.lock:
            turns = sorted(
                (
                    t
                    for (r, _), t in self.agent_turns.items()
                    if r == run_id and (before is None or t["number"] < before)
                ),
                key=lambda t: t["number"],
                reverse=True,
            )
            return {
                "items": deepcopy(turns[:limit]),
                "next_before": turns[limit - 1]["number"] if len(turns) > limit else None,
            }


class FirestoreAgentStore:
    def agent_runs_for_player(self, player_id):
        from google.cloud.firestore_v1.base_query import FieldFilter

        from backend.app.repository import read_document

        return [
            read_document(s)
            for s in self.agent_runs.where(filter=FieldFilter("player_id", "==", player_id)).stream(
                timeout=60
            )
        ]

    def remove_agent_records_for_player(self, player_id):
        for run in self.agent_runs_for_player(player_id):
            self.db.recursive_delete(self.agent_runs.document(run["id"]))

    def create_agent_run(self, run):
        from backend.app.repository import document_data

        self.agent_runs.document(run["id"]).create(
            {**document_data(run), "player_id": run["player_id"]}, timeout=15
        )

    def get_agent_run(self, run_id):
        from backend.app.repository import read_document

        run = read_document(self.agent_runs.document(run_id).get(timeout=15))
        if not run:
            raise GameError("Agent session not found", 404)
        return run

    def mutate_agent_run(self, run_id, operation):
        from backend.app.repository import document_data, read_document

        ref = self.agent_runs.document(run_id)

        @firestore.transactional
        def mutate(transaction):
            run = read_document(ref.get(transaction=transaction, timeout=15))
            if not run:
                raise GameError("Agent session not found", 404)
            updated, turn = operation(run)
            transaction.set(ref, {**document_data(updated), "player_id": updated["player_id"]})
            if turn:
                transaction.set(
                    ref.collection("turns").document(turn["id"]),
                    {
                        **document_data(turn),
                        "number": turn["number"],
                    },
                )
            return updated

        return mutate(self.db.transaction())

    def list_agent_runs(self, limit=100, cursor=None):
        return self._page(self.agent_runs, public_run, limit, cursor)

    def get_agent_turn(self, run_id, turn_id):
        from backend.app.repository import read_document

        return read_document(
            self.agent_runs.document(run_id).collection("turns").document(turn_id).get(timeout=15)
        )

    def list_agent_turns(self, run_id, limit=50, before=None):
        from backend.app.repository import read_document

        query = (
            self.agent_runs.document(run_id)
            .collection("turns")
            .order_by("number", direction=firestore.Query.DESCENDING)
        )
        if before is not None:
            query = query.start_after({"number": before})
        turns = [read_document(s) for s in query.limit(limit + 1).stream(timeout=60)]
        return {
            "items": turns[:limit],
            "next_before": turns[limit - 1]["number"] if len(turns) > limit else None,
        }
