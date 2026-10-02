from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.catalog import load_levels
from backend.app.config import ROOT
from backend.app.engine import (
    GameError,
    apply_action,
    board_matrix,
    legal_arrows,
    new_session,
    observation,
    solution,
)
from backend.app.models import Level


@pytest.fixture
def level():
    return Level.model_validate(
        {
            "id": "test-board",
            "number": 1,
            "name": "Two arrows",
            "difficulty": "easy",
            "matrix": [[0, 0, 0, 0, 0], [1, 1, 0, 2, 0], [0, 0, 0, 2, 0]],
            "arrows": [{"id": 1, "path": [[1, 0], [1, 1]]}, {"id": 2, "path": [[1, 3], [2, 3]]}],
        }
    )


@pytest.fixture
def session(level):
    return new_session("test-session", "test-player", level, "human", None)


def tap(session, arrow_id, **kwargs):
    return apply_action(
        session,
        arrow_id=arrow_id,
        action_id=str(uuid4()),
        expected_revision=session["revision"],
        **kwargs,
    )


def test_blocked_arrow_loses_a_life_without_changing_board(session):
    updated, outcome = tap(session, 1)
    assert outcome == {"result": "blocked", "arrow_id": 1, "blocked_by": 2, "reward": -1}
    assert updated["lives_remaining"] == 2
    assert updated["removed_ids"] == []
    assert updated["moves"] == updated["mistakes"] == 1
    assert session["lives_remaining"] == 3  # engine never mutates its input


def test_removing_blocker_opens_lane_and_wins(level, session):
    assert legal_arrows(level, []) == [2]
    first, outcome = tap(session, 2)
    assert outcome["result"] == "cleared"
    assert legal_arrows(level, first["removed_ids"]) == [1]
    final, outcome = tap(first, 1)
    assert final["status"] == "won" and outcome["reward"] == 10
    assert final["completed_at"] is not None
    assert observation(final)["remaining_count"] == 0
    assert all(value <= 0 for row in board_matrix(level, final["removed_ids"]) for value in row)


def test_three_mistakes_end_attempt(session):
    for _ in range(3):
        session, _ = tap(session, 1)
    assert session["status"] == "lost" and session["lives_remaining"] == 0
    with pytest.raises(GameError, match="attempt has ended"):
        tap(session, 2)


def test_idempotent_retry_does_not_charge_again(session):
    action_id = str(uuid4())
    first, outcome = apply_action(session, arrow_id=1, action_id=action_id, expected_revision=0)
    duplicate, second_outcome = apply_action(
        first, arrow_id=1, action_id=action_id, expected_revision=0
    )
    assert duplicate == first and second_outcome == outcome
    assert duplicate["lives_remaining"] == 2
    with pytest.raises(GameError, match="different action"):
        apply_action(first, arrow_id=2, action_id=action_id, expected_revision=0)


def test_stale_revision_and_bad_ids_do_not_change_state(session):
    original = deepcopy(session)
    with pytest.raises(GameError, match="Stale revision"):
        apply_action(session, arrow_id=2, action_id=str(uuid4()), expected_revision=99)
    with pytest.raises(GameError, match="Unknown arrow"):
        tap(session, 900)
    assert session == original
    updated, _ = tap(session, 2)
    with pytest.raises(GameError, match="already left"):
        tap(updated, 2)


def test_hints_are_safe_and_limited(session):
    for _ in range(3):
        session, outcome = apply_action(
            session, hint=True, action_id=str(uuid4()), expected_revision=session["revision"]
        )
        assert outcome["arrow_id"] == 2
    assert session["hints_used"] == 3 and session["lives_remaining"] == 3
    assert session["moves"] == 0
    with pytest.raises(GameError, match="No hints remaining"):
        apply_action(
            session, hint=True, action_id=str(uuid4()), expected_revision=session["revision"]
        )


def test_own_body_does_not_block_forward_lane():
    level = Level.model_validate(
        {
            "id": "self-ray",
            "number": 1,
            "name": "Bend",
            "difficulty": "easy",
            "matrix": [[1, 1, 1, 1], [1, 1, 0, 1], [-1, -1, -1, -1]],
            "arrows": [{"id": 1, "path": [[1, 3], [0, 3], [0, 2], [0, 1], [0, 0], [1, 0], [1, 1]]}],
        }
    )
    assert legal_arrows(level, []) == [1]
    assert board_matrix(level, [1])[-1] == [-1, -1, -1, -1]


def test_cyclic_dependencies_are_rejected():
    level = Level.model_validate(
        {
            "id": "cycle",
            "number": 1,
            "name": "Cycle",
            "difficulty": "easy",
            "matrix": [[1, 1, 2, 2], [0, 0, 0, 0]],
            "arrows": [{"id": 1, "path": [[0, 0], [0, 1]]}, {"id": 2, "path": [[0, 3], [0, 2]]}],
        }
    )
    with pytest.raises(ValueError, match="unsolvable"):
        solution(level)


def test_outside_silhouette_does_not_hide_a_blocker(level):
    data = level.model_dump(mode="json")
    data["matrix"][1][2] = -1
    shaped = Level.model_validate(data)
    assert legal_arrows(shaped, []) == [2]
    assert board_matrix(shaped, [2])[1][2] == -1


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["matrix"][0].pop(),
        lambda d: d["matrix"][1].__setitem__(0, 99),
        lambda d: d["arrows"][0]["path"].__setitem__(1, [0, 4]),
        lambda d: d["arrows"][1].__setitem__("id", 1),
        lambda d: d["arrows"][0]["path"].append([1, 0]),
    ],
)
def test_invalid_level_rejected(level, change):
    data = level.model_dump(mode="json")
    change(data)
    with pytest.raises(ValidationError):
        Level.model_validate(data)


def test_entire_catalog_solvable_and_varied():
    levels = load_levels(ROOT / "data" / "levels")
    assert len(levels) == 1000
    assert {level.number for level in levels} == set(range(1, 1001))
    assert {level.id for level in levels} == {f"level-{n:03d}" for n in range(1, 1001)}
    assert len({(level.height, level.width) for level in levels}) > 20
