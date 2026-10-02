"""Opt-in integration test against the configured cloud Firestore or emulator."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.engine import solution
from backend.app.main import create_app
from backend.app.models import Level

pytestmark = pytest.mark.skipif(
    os.environ.get("AMAZE_RUN_FIRESTORE_TESTS") != "1",
    reason="Set AMAZE_RUN_FIRESTORE_TESTS=1 to test the configured Firestore database",
)


def test_firestore_batch_saves_and_retries_a_complete_level():
    with TestClient(create_app(Settings(storage_backend="firestore"))) as client:
        token = client.post("/api/v1/players").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        state = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "level_id": "level-001",
                "actor_type": "agent",
                "actor_name": "batch-integration-test",
            },
        ).json()
        level = Level.model_validate(client.get("/api/v1/levels/level-001").json())
        body = {
            "expected_revision": 0,
            "actions": [
                {"type": "tap", "arrow_id": arrow_id, "action_id": str(uuid4())}
                for arrow_id in solution(level)
            ],
        }
        path = f"/api/v1/sessions/{state['session_id']}"
        response = client.post(f"{path}/actions/batch", headers=headers, json=body)
        assert response.status_code == 200
        assert response.json()["state"]["status"] == "won"
        assert (
            client.post(f"{path}/actions/batch", headers=headers, json=body).json()
            == response.json()
        )
        saved = client.get(path, headers=headers).json()
        assert saved == response.json()["state"]
        progress = client.get("/api/v1/players/me", headers=headers).json()["progress"]
        assert progress["level-001"]["completions"] == 1
        assert progress["level-001"]["best_moves"] == len(level.arrows)


def test_firestore_transactions_idempotency_and_persistence():
    settings = Settings(storage_backend="firestore")
    with TestClient(create_app(settings)) as client:
        credentials = client.post("/api/v1/players").json()
        headers = {"Authorization": f"Bearer {credentials['access_token']}"}
        state = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "level_id": "level-001",
                "actor_type": "agent",
                "actor_name": "firestore-integration-test",
            },
        ).json()
        assert "session_id" in state
        path = f"/api/v1/sessions/{state['session_id']}"

        def body(arrow_id):
            return {
                "arrow_id": arrow_id,
                "expected_revision": state["revision"],
                "action_id": str(uuid4()),
            }

        # Race distinct actions at the same revision. Firestore must commit only one.
        bodies = [body(arrow_id) for arrow_id in state["legal_arrow_ids"][:2]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda b: client.post(f"{path}/actions", headers=headers, json=b), bodies)
            )
        assert sorted(response.status_code for response in responses) == [200, 409]
        state = client.get(path, headers=headers).json()
        while state["status"] == "active":
            request_body = body(state["legal_arrow_ids"][0])
            response = client.post(f"{path}/actions", headers=headers, json=request_body)
            assert response.status_code == 200
            duplicate = client.post(f"{path}/actions", headers=headers, json=request_body)
            assert duplicate.json() == response.json()
            state = response.json()["state"]
        assert state["status"] == "won"
        progress = client.get("/api/v1/players/me", headers=headers).json()["progress"]
        assert progress["level-001"]["completions"] == 1
        session_id = state["session_id"]

    # A separate SDK/app instance must still load the saved session and progress.
    with TestClient(create_app(settings)) as fresh_client:
        saved = fresh_client.get(f"/api/v1/sessions/{session_id}", headers=headers)
        assert saved.json() == state
        person = fresh_client.get("/api/v1/players/me", headers=headers).json()
        assert person["progress"]["level-001"]["stars"] == 3
