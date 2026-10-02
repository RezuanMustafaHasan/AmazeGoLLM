"""Deterministic game rules. No HTTP, Firebase, or frontend dependencies."""

from copy import deepcopy
from datetime import UTC, datetime

from backend.app.models import Level


class GameError(Exception):
    def __init__(self, message: str, code: int = 409):
        self.message = message
        self.code = code


def now_iso():
    return datetime.now(UTC).isoformat()


def board_matrix(level: Level, removed_ids: list[int]) -> list[list[int]]:
    removed = set(removed_ids)
    return [[0 if cell in removed else cell for cell in row] for row in level.matrix]


def blocking_arrow(level: Level, matrix: list[list[int]], arrow_id: int) -> int | None:
    arrow = next((a for a in level.arrows if a.id == arrow_id), None)
    if arrow is None:
        raise GameError("Unknown arrow ID", 422)
    row, col = arrow.path[-1]
    dr, dc = arrow.direction
    row, col = row + dr, col + dc
    while 0 <= row < level.height and 0 <= col < level.width:
        value = matrix[row][col]
        if value > 0 and value != arrow_id:
            return value
        row, col = row + dr, col + dc
    return None


def legal_arrows(level: Level, removed_ids: list[int]) -> list[int]:
    matrix = board_matrix(level, removed_ids)
    removed = set(removed_ids)
    return [
        a.id
        for a in level.arrows
        if a.id not in removed and blocking_arrow(level, matrix, a.id) is None
    ]


def solution(level: Level) -> list[int]:
    """All available arrows can be safely removed; removing never introduces blockers."""
    removed: list[int] = []
    while len(removed) < len(level.arrows):
        choices = legal_arrows(level, removed)
        if not choices:
            raise ValueError(f"Level {level.id} is unsolvable: cyclic blocking dependencies")
        removed.extend(choices)
    return removed


def new_session(
    session_id: str,
    player_id: str,
    level: Level,
    actor_type: str,
    actor_name: str | None,
    *,
    max_lives: int | None = None,
) -> dict:
    return {
        "id": session_id,
        "player_id": player_id,
        "level_id": level.id,
        "level_hash": level.fingerprint,
        "level": level.model_dump(mode="json"),
        "actor_type": actor_type,
        "actor_name": actor_name,
        "status": "active",
        "lives_remaining": max_lives if max_lives is not None else level.lives,
        "max_lives": max_lives if max_lives is not None else level.lives,
        "moves": 0,
        "mistakes": 0,
        "removed_ids": [],
        "hints_used": 0,
        "revision": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "completed_at": None,
        "history": [],
        "receipts": {},
    }


def observation(session: dict) -> dict:
    level = Level.model_validate(session["level"])
    removed = set(session["removed_ids"])
    return {
        "session_id": session["id"],
        "level_id": level.id,
        "level": level.summary(),
        "status": session["status"],
        "lives_remaining": session["lives_remaining"],
        "max_lives": session.get("max_lives", level.lives),
        "moves": session["moves"],
        "mistakes": session["mistakes"],
        "hints_used": session["hints_used"],
        "hints_remaining": 3 - session["hints_used"],
        "revision": session["revision"],
        "removed_ids": session["removed_ids"],
        "processed_action_ids": list(session["receipts"]),
        "remaining_count": len(level.arrows) - len(removed),
        "matrix": board_matrix(level, session["removed_ids"]),
        "arrows": [a.model_dump(mode="json") for a in level.arrows if a.id not in removed],
        "legal_arrow_ids": legal_arrows(level, session["removed_ids"])
        if session["status"] == "active"
        else [],
        "created_at": session["created_at"],
        "completed_at": session["completed_at"],
    }


def apply_action(
    session: dict,
    *,
    action_id: str,
    expected_revision: int,
    arrow_id: int | None = None,
    hint: bool = False,
) -> tuple[dict, dict]:
    action = {"type": "hint" if hint else "tap", "arrow_id": arrow_id}
    previous = session["receipts"].get(action_id)
    if previous:
        if previous["action"] != action:
            raise GameError("action_id was already used for a different action")
        return session, previous["outcome"]
    if expected_revision != session["revision"]:
        raise GameError("Stale revision. Fetch the session state before your next action.")
    if session["status"] != "active":
        raise GameError("This attempt has ended. Start a new session to play again.")
    updated = deepcopy(session)
    level = Level.model_validate(session["level"])
    if hint:
        if session["hints_used"] >= 3:
            raise GameError("No hints remaining")
        choices = legal_arrows(level, session["removed_ids"])
        if not choices:
            raise GameError("No available moves")
        updated["hints_used"] += 1
        outcome = {"result": "hint", "arrow_id": choices[0], "blocked_by": None, "reward": 0}
    else:
        if arrow_id in session["removed_ids"]:
            raise GameError("That arrow has already left the board", 422)
        blocker = blocking_arrow(level, board_matrix(level, session["removed_ids"]), arrow_id)
        updated["moves"] += 1
        outcome = {
            "result": "blocked" if blocker else "cleared",
            "arrow_id": arrow_id,
            "blocked_by": blocker,
            "reward": -1 if blocker else 1,
        }
        if blocker:
            updated["lives_remaining"] -= 1
            updated["mistakes"] += 1
            if updated["lives_remaining"] == 0:
                updated["status"] = "lost"
                updated["completed_at"] = now_iso()
        else:
            updated["removed_ids"].append(arrow_id)
            if len(updated["removed_ids"]) == len(level.arrows):
                updated["status"] = "won"
                updated["completed_at"] = now_iso()
                outcome["reward"] = 10
    updated["revision"] += 1
    updated["updated_at"] = now_iso()
    updated["history"].append(
        {
            "action_id": action_id,
            **action,
            **outcome,
            "revision": updated["revision"],
            "at": updated["updated_at"],
        }
    )
    updated["receipts"][action_id] = {"action": action, "outcome": outcome}
    return updated, outcome


def apply_agent_error(session, *, action_id, expected_revision, message):
    """A model-produced invalid action is a scored mistake, with retry receipts."""
    action = {"type": "invalid", "arrow_id": None, "message": message}
    previous = session["receipts"].get(action_id)
    if previous:
        if previous["action"] != action:
            raise GameError("action_id was already used for a different action")
        return session, previous["outcome"]
    if expected_revision != session["revision"] or session["status"] != "active":
        raise GameError("The agent state changed before its action was applied.")
    updated = deepcopy(session)
    updated["moves"] += 1
    updated["mistakes"] += 1
    updated["lives_remaining"] -= 1
    updated["revision"] += 1
    updated["updated_at"] = now_iso()
    if updated["lives_remaining"] == 0:
        updated["status"] = "lost"
        updated["completed_at"] = updated["updated_at"]
    outcome = {
        "result": "invalid",
        "arrow_id": None,
        "blocked_by": None,
        "reward": -1,
        "message": message,
    }
    updated["history"].append(
        {
            "action_id": action_id,
            **action,
            **outcome,
            "revision": updated["revision"],
            "at": updated["updated_at"],
        }
    )
    updated["receipts"][action_id] = {"action": action, "outcome": outcome}
    return updated, outcome


def update_progress(player: dict, session: dict) -> dict:
    player = deepcopy(player)
    if session["status"] != "won":
        return player
    old = player["progress"].get(session["level_id"], {})
    stars = max(1, 3 - session["mistakes"])
    elapsed = int(
        (
            datetime.fromisoformat(session["completed_at"])
            - datetime.fromisoformat(session["created_at"])
        ).total_seconds()
        * 1000
    )
    player["progress"][session["level_id"]] = {
        "stars": max(stars, old.get("stars", 0)),
        "best_moves": min(session["moves"], old.get("best_moves", session["moves"])),
        "best_time_ms": min(elapsed, old.get("best_time_ms", elapsed)),
        "completions": old.get("completions", 0) + 1,
        "completed_at": session["completed_at"],
    }
    return player
