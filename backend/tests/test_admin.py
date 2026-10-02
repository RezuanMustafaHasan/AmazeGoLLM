import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.admin import COOKIE, hash_password
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.repository import MemoryRepository

PASSWORD = "Test-only-admin-password!"


@pytest.fixture
def admin_client(tmp_path):
    (tmp_path / "level-001.json").write_text(
        json.dumps(
            {
                "id": "level-001",
                "number": 1,
                "name": "First",
                "difficulty": "easy",
                "matrix": [[1, 1], [0, 0]],
                "arrows": [{"id": 1, "path": [[0, 0], [0, 1]]}],
            }
        )
    )
    settings = Settings(
        storage_backend="memory",
        levels_dir=tmp_path,
        admin_username="admin",
        admin_password_hash=SecretStr(hash_password(PASSWORD)),
        admin_session_secret=SecretStr("test-only-secret-" * 4),
    )
    store = MemoryRepository()
    with TestClient(create_app(settings, store)) as client:
        yield client, store


def login(client):
    response = client.post("/api/admin/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200
    return {"X-Admin-CSRF": response.json()["csrf_token"]}


def attempt(client):
    credentials = client.post("/api/v1/players").json()
    headers = {"Authorization": f"Bearer {credentials['access_token']}"}
    session = client.post(
        "/api/v1/sessions", headers=headers, json={"level_id": "level-001"}
    ).json()
    return credentials, headers, session


def win(client, headers, state):
    return client.post(
        f"/api/v1/sessions/{state['session_id']}/actions",
        headers=headers,
        json={"arrow_id": 1, "expected_revision": 0, "action_id": str(uuid4())},
    ).json()["state"]


def test_player_credentials_cannot_access_or_delete_admin_records(admin_client):
    client, store = admin_client
    player, headers, state = attempt(client)
    for path in [
        "/stats",
        "/players",
        "/sessions",
        f"/players/{player['player_id']}",
        f"/sessions/{state['session_id']}",
    ]:
        assert client.get(f"/api/admin{path}", headers=headers).status_code == 401
    assert (
        client.delete(f"/api/admin/players/{player['player_id']}", headers=headers).status_code
        == 401
    )
    assert (
        client.delete(f"/api/admin/sessions/{state['session_id']}", headers=headers).status_code
        == 401
    )
    assert len(store.players) == len(store.sessions) == 1


def test_login_cookie_and_csrf_protect_admin_deletion(admin_client):
    client, store = admin_client
    player, _, state = attempt(client)
    assert (
        client.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).status_code
        == 401
    )
    response = client.post("/api/admin/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    csrf = {"X-Admin-CSRF": response.json()["csrf_token"]}
    assert client.get("/api/admin/auth").json()["username"] == "admin"
    path = f"/api/admin/sessions/{state['session_id']}"
    assert client.delete(path).status_code == 403
    assert state["session_id"] in store.sessions
    assert client.delete(path, headers=csrf).status_code == 200
    assert store.players[player["player_id"]]["active_session_id"] is None
    assert client.post("/api/admin/logout", headers=csrf).status_code == 200
    assert client.get("/api/admin/stats").status_code == 401


def test_admin_inspection_pagination_and_cascading_player_delete(admin_client):
    client, store = admin_client
    for _ in range(3):
        attempt(client)
    csrf = login(client)
    response = client.get("/api/admin/players?limit=2").json()
    assert len(response["items"]) == 2 and response["next_cursor"]
    assert all("token_hash" not in item for item in response["items"])
    remaining = client.get(f"/api/admin/players?limit=2&cursor={response['next_cursor']}").json()
    assert len(remaining["items"]) == 1 and remaining["next_cursor"] is None
    player = response["items"][0]
    sessions = client.get(f"/api/admin/sessions?player_id={player['id']}").json()["items"]
    assert len(sessions) == 1
    detail = client.get(f"/api/admin/sessions/{sessions[0]['id']}").json()
    assert detail["history"] == [] and "level" not in detail and "receipts" not in detail
    result = client.delete(f"/api/admin/players/{player['id']}", headers=csrf)
    assert result.json() == {"deleted_players": 1, "deleted_sessions": 1}
    assert player["id"] not in store.players and sessions[0]["id"] not in store.sessions
    assert client.get("/api/admin/stats").json()["players"] == 2
    assert client.get("/api/admin/players?limit=101").status_code == 422


def test_deleting_a_win_recalculates_progress_and_keeps_other_attempts(admin_client):
    client, store = admin_client
    player, headers, first = attempt(client)
    win(client, headers, first)
    second = client.post("/api/v1/sessions", headers=headers, json={"level_id": "level-001"}).json()
    win(client, headers, second)
    assert store.players[player["player_id"]]["progress"]["level-001"]["completions"] == 2
    csrf = login(client)
    assert (
        client.delete(f"/api/admin/sessions/{first['session_id']}", headers=csrf).status_code == 200
    )
    assert store.players[player["player_id"]]["progress"]["level-001"]["completions"] == 1
    assert store.players[player["player_id"]]["active_session_id"] == second["session_id"]
    assert (
        client.delete(f"/api/admin/sessions/{second['session_id']}", headers=csrf).status_code
        == 200
    )
    assert store.players[player["player_id"]]["progress"] == {}
    assert store.players[player["player_id"]]["active_session_id"] is None


def test_login_is_rate_limited_and_tampered_cookies_are_rejected(admin_client):
    client, _ = admin_client
    for _ in range(5):
        assert (
            client.post(
                "/api/admin/login", json={"username": "admin", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/admin/login", json={"username": "admin", "password": PASSWORD}
        ).status_code
        == 429
    )
    client.cookies.set(COOKIE, "invalid.signature", path="/api/admin")
    assert client.get("/api/admin/stats").status_code == 401


def test_admin_pages_are_served_and_https_cookie_is_secure(admin_client):
    client, _ = admin_client
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/").status_code == 200
    with TestClient(client.app, base_url="https://testserver") as https:
        response = https.post("/api/admin/login", json={"username": "admin", "password": PASSWORD})
        assert "Secure" in response.headers["set-cookie"]
