from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.repository import MemoryRepository


@pytest.fixture
def client():
    with TestClient(create_app(Settings(storage_backend="memory"), MemoryRepository())) as client:
        yield client


def player_headers(client):
    response = client.post("/api/v1/players")
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def start(client, headers, actor="human"):
    response = client.post(
        "/api/v1/sessions", headers=headers, json={"level_id": "level-001", "actor_type": actor}
    )
    assert response.status_code == 201
    return response.json()


def action_body(state, arrow_id=None):
    return {
        "arrow_id": arrow_id or state["legal_arrow_ids"][0],
        "expected_revision": state["revision"],
        "action_id": str(uuid4()),
    }


def test_api_catalog_contains_only_the_new_thousand_levels(client):
    response = client.get("/api/v1/levels")
    assert response.status_code == 200
    levels = response.json()["levels"]
    assert len(levels) == 1000
    assert [level["number"] for level in levels] == list(range(1, 1001))
    assert [level["id"] for level in levels] == [f"level-{n:03d}" for n in range(1, 1001)]
    assert levels[0]["name"] == "Level 1" and levels[-1]["name"] == "Level 1000"
    assert "matrix" not in levels[0] and "payload" not in levels[0]
    assert client.get("/api/v1/levels/level-1001").status_code == 404


def test_api_win_progress_resume_and_history(client):
    headers = player_headers(client)
    state = start(client, headers)
    assert (
        client.get("/api/v1/players/me", headers=headers).json()["active_session_id"]
        == state["session_id"]
    )
    while state["status"] == "active":
        response = client.post(
            f"/api/v1/sessions/{state['session_id']}/actions",
            headers=headers,
            json=action_body(state),
        )
        assert response.status_code == 200
        state = response.json()["state"]
    assert state["status"] == "won" and state["lives_remaining"] == 3
    progress = client.get("/api/v1/players/me", headers=headers).json()["progress"]["level-001"]
    assert progress["stars"] == 3 and progress["completions"] == 1
    assert progress["best_moves"] == state["level"]["arrow_count"]
    history = client.get(f"/api/v1/sessions/{state['session_id']}/history", headers=headers).json()
    assert len(history["history"]) == state["moves"]
    saved = client.get(f"/api/v1/sessions/{state['session_id']}", headers=headers).json()
    assert saved == state


def test_sessions_require_ownership_and_agents_are_isolated(client):
    human, agent = player_headers(client), player_headers(client)
    h_state, a_state = start(client, human), start(client, agent, "agent")
    path = f"/api/v1/sessions/{h_state['session_id']}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=agent).status_code == 404
    assert (
        client.post(f"{path}/actions", headers=agent, json=action_body(h_state)).status_code == 404
    )
    response = client.post(f"{path}/actions", headers=human, json=action_body(h_state))
    assert response.json()["state"]["revision"] == 1
    assert (
        client.get(f"/api/v1/sessions/{a_state['session_id']}", headers=agent).json()["revision"]
        == 0
    )


def test_duplicate_and_concurrent_actions_are_atomic(client):
    headers = player_headers(client)
    state = start(client, headers)
    path = f"/api/v1/sessions/{state['session_id']}/actions"
    body = action_body(state)
    first = client.post(path, headers=headers, json=body)
    retry = client.post(path, headers=headers, json=body)
    assert first.json() == retry.json()
    state = first.json()["state"]
    bodies = [action_body(state, arrow_id) for arrow_id in state["legal_arrow_ids"][:2]]
    # Both target revision 1. Only one action may commit.
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda b: client.post(path, headers=headers, json=b), bodies))
    assert sorted(response.status_code for response in responses) == [200, 409]


def test_bad_actions_and_auth_are_rejected(client):
    headers = player_headers(client)
    state = start(client, headers)
    path = f"/api/v1/sessions/{state['session_id']}/actions"
    body = action_body(state)
    body["arrow_id"] = True
    assert client.post(path, headers=headers, json=body).status_code == 422
    body["arrow_id"] = 99999
    assert client.post(path, headers=headers, json=body).status_code == 422
    assert (
        client.get("/api/v1/players/me", headers={"Authorization": "Bearer garbage"}).status_code
        == 401
    )
    assert client.get("/api/docs").status_code == 200


def test_api_hint_limits_and_restart(client):
    headers = player_headers(client)
    state = start(client, headers)
    path = f"/api/v1/sessions/{state['session_id']}/hints"
    for _ in range(3):
        response = client.post(
            path,
            headers=headers,
            json={"expected_revision": state["revision"], "action_id": str(uuid4())},
        )
        assert response.json()["outcome"]["arrow_id"] in state["legal_arrow_ids"]
        state = response.json()["state"]
    assert state["hints_remaining"] == 0
    assert (
        client.post(
            path,
            headers=headers,
            json={"expected_revision": state["revision"], "action_id": str(uuid4())},
        ).status_code
        == 409
    )
    restart = start(client, headers)
    assert restart["session_id"] != state["session_id"]
    assert restart["hints_remaining"] == 3 and restart["revision"] == 0
