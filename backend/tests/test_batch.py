from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Level
from backend.app.repository import MemoryRepository


@pytest.fixture
def game():
    store = MemoryRepository()
    with TestClient(create_app(Settings(storage_backend="memory"), store)) as client:
        level = Level.model_validate(
            {
                "id": "batch-test",
                "number": 100,
                "name": "Batch",
                "difficulty": "easy",
                "matrix": [[0, 0, 0, 0, 0], [1, 1, -1, 2, 0], [0, 0, 0, 2, 0]],
                "arrows": [
                    {"id": 1, "path": [[1, 0], [1, 1]]},
                    {"id": 2, "path": [[1, 3], [2, 3]]},
                ],
            }
        )
        store.add_levels([level])
        token = client.post("/api/v1/players").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        state = client.post("/api/v1/sessions", headers=headers, json={"level_id": level.id}).json()
        yield client, headers, state


def tap(arrow_id):
    return {"type": "tap", "arrow_id": arrow_id, "action_id": str(uuid4())}


def test_batch_keeps_order_and_retries_do_not_repeat_a_win(game):
    client, headers, state = game
    path = f"/api/v1/sessions/{state['session_id']}/actions/batch"
    body = {"expected_revision": 0, "actions": [tap(1), tap(2), tap(1)]}
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200
    result = first.json()
    assert [o["result"] for o in result["outcomes"]] == ["blocked", "cleared", "cleared"]
    assert result["state"]["status"] == "won"
    assert result["state"]["lives_remaining"] == 2
    assert result["state"]["revision"] == 3
    assert result["state"]["processed_action_ids"] == [a["action_id"] for a in body["actions"]]
    assert client.post(path, headers=headers, json=body).json() == result
    progress = client.get("/api/v1/players/me", headers=headers).json()["progress"]["batch-test"]
    assert progress["completions"] == 1 and progress["stars"] == 2


def test_committed_prefix_can_be_retried_with_new_remaining_moves(game):
    client, headers, state = game
    path = f"/api/v1/sessions/{state['session_id']}/actions"
    first = tap(2)
    client.post(
        path,
        headers=headers,
        json={
            "arrow_id": 2,
            "action_id": first["action_id"],
            "expected_revision": 0,
        },
    )
    result = client.post(
        f"{path}/batch", headers=headers, json={"expected_revision": 0, "actions": [first, tap(1)]}
    )
    assert result.status_code == 200
    assert result.json()["state"]["status"] == "won"
    assert result.json()["state"]["moves"] == 2


def test_invalid_batch_rolls_back_all_of_its_moves(game):
    client, headers, state = game
    path = f"/api/v1/sessions/{state['session_id']}"
    response = client.post(
        f"{path}/actions/batch",
        headers=headers,
        json={"expected_revision": 0, "actions": [tap(2), tap(999)]},
    )
    assert response.status_code == 422
    assert client.get(path, headers=headers).json() == state


def test_hints_and_life_loss_share_the_same_ordered_batch(game):
    client, headers, state = game
    response = client.post(
        f"/api/v1/sessions/{state['session_id']}/actions/batch",
        headers=headers,
        json={
            "expected_revision": 0,
            "actions": [
                {"type": "hint", "action_id": str(uuid4())},
                tap(1),
                tap(1),
                tap(1),
            ],
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["outcomes"][0]["arrow_id"] == 2
    assert result["state"]["hints_remaining"] == 2
    assert result["state"]["status"] == "lost"
    assert result["state"]["lives_remaining"] == 0


def test_batch_still_requires_revision_and_ownership(game):
    client, headers, state = game
    path = f"/api/v1/sessions/{state['session_id']}/actions/batch"
    body = {"expected_revision": 10, "actions": [tap(2)]}
    assert client.post(path, headers=headers, json=body).status_code == 409
    token = client.post("/api/v1/players").json()["access_token"]
    assert (
        client.post(path, headers={"Authorization": f"Bearer {token}"}, json=body).status_code
        == 404
    )
    body["expected_revision"] = 0
    body["actions"].append(body["actions"][0])
    assert client.post(path, headers=headers, json=body).status_code == 422
