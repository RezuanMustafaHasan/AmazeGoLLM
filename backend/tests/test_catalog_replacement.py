import json
from uuid import uuid4

import pytest

from backend.app.catalog import load_levels
from backend.app.engine import GameError, new_session
from backend.app.models import Level
from backend.app.repository import MemoryRepository, session_document


def board(name="Original"):
    return Level.model_validate(
        {
            "id": "level-001",
            "number": 1,
            "name": name,
            "difficulty": "easy",
            "matrix": [[1, 1], [0, 0]],
            "arrows": [{"id": 1, "path": [[0, 0], [0, 1]]}],
        }
    )


def test_stored_attempt_contains_only_a_level_reference_and_hydrates_from_git():
    store = MemoryRepository()
    level = board()
    store.set_catalog([level])
    player_id, token = store.create_player()
    session = new_session(str(uuid4()), player_id, level, "human", None)
    store.create_session(session)
    data = store.sessions[session["id"]]
    assert data["level_id"] == level.id and data["level_hash"] == level.fingerprint
    assert not {"level", "matrix", "arrows"} & data.keys()
    firestore_data = json.loads(session_document(session)["payload"])
    assert firestore_data == data
    hydrated = store.get_session(session["id"], player_id)
    assert hydrated["level"] == level.model_dump(mode="json")
    assert store.get_player(token)["active_session_id"] == session["id"]


def test_a_changed_git_board_cannot_resume_an_attempt_for_a_different_version():
    store = MemoryRepository()
    level = board()
    store.set_catalog([level])
    player_id, _ = store.create_player()
    session = new_session(str(uuid4()), player_id, level, "human", None)
    store.create_session(session)
    store.set_catalog([board("Changed")])
    with pytest.raises(GameError, match="changed") as error:
        store.get_session(session["id"], player_id)
    assert error.value.code == 404
    assert session["id"] in store.sessions


def test_missing_lfs_objects_report_how_to_download_the_catalog(tmp_path):
    (tmp_path / "level-001.json").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 123\n"
    )
    with pytest.raises(ValueError, match="git lfs pull"):
        load_levels(tmp_path)
