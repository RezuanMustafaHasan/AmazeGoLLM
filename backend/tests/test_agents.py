import json
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from backend.app.admin import hash_password
from backend.app.agent_llm import RULES, invoke_model, parse_decision
from backend.app.agents import CreateAgentRun
from backend.app.config import Settings
from backend.app.engine import GameError
from backend.app.main import create_app
from backend.app.repository import MemoryRepository


@pytest.fixture
def evaluation(tmp_path):
    for number in [1, 2]:
        (tmp_path / f"level-{number:03}.json").write_text(
            json.dumps(
                {
                    "id": f"level-{number:03}",
                    "number": number,
                    "name": "Two arrows",
                    "difficulty": "easy",
                    "matrix": [[0, 0, 0, 0, 0], [1, 1, 0, 2, 0], [0, 0, 0, 2, 0]],
                    "arrows": [
                        {"id": 1, "path": [[1, 0], [1, 1]]},
                        {"id": 2, "path": [[1, 3], [2, 3]]},
                    ],
                }
            )
        )
    settings = Settings(
        _env_file=None,
        storage_backend="memory",
        levels_dir=tmp_path,
        admin_password_hash=SecretStr(hash_password("test-password")),
        admin_session_secret=SecretStr("test-secret-" * 5),
        ufl_api_key=None,
        ufl_base_url="https://ufl.test",
    )
    store = MemoryRepository()
    with TestClient(create_app(settings, store)) as client:
        yield client, client.app.state.agents, store


def login(client):
    response = client.post(
        "/api/admin/login", json={"username": "admin", "password": "test-password"}
    )
    return {"X-Admin-CSRF": response.json()["csrf_token"]}


def config(**overrides):
    return {
        "name": "Test agent",
        "provider": "ufl",
        "model": "gpt-6-luna",
        "start_level": 1,
        "end_level": 2,
        "lives": 3,
        "api_key": "private-test-key",
        **overrides,
    }


def respond(raw):
    return {
        "raw_response": raw,
        "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    }


def step(client, headers, run, request_id=None):
    return client.post(
        f"/api/admin/agents/{run['id']}/step",
        headers=headers,
        json={"request_id": request_id or str(uuid4())},
    )


def test_admin_authorization_csrf_key_privacy_and_observation(evaluation):
    client, runner, store = evaluation
    assert client.get("/api/admin/agents").status_code == 401
    assert client.post("/api/admin/agents", json=config()).status_code == 401
    csrf = login(client)
    assert client.post("/api/admin/agents", json=config()).status_code == 403
    response = client.post("/api/admin/agents", json=config(), headers=csrf)
    assert response.status_code == 201
    run = response.json()
    assert run["state"]["max_lives"] == 3
    assert "legal_arrow_ids" not in run["state"]
    for path in [
        "/api/admin/agents",
        f"/api/admin/agents/{run['id']}",
        f"/api/admin/agents/{run['id']}/turns",
        "/api/admin/agents/providers",
    ]:
        text = client.get(path).text
        assert "private-test-key" not in text and '"credential"' not in text
    encrypted = store.get_agent_run(run["id"])["credential"]
    assert encrypted != "private-test-key"
    assert runner.cipher().decrypt(encrypted.encode()) == b"private-test-key"
    assert "legal_arrow_ids" not in run["feedback"]
    assert store.admin_session(run["current_session_id"])["actor_type"] == "agent"
    assert client.get(f"/api/admin/agents/{run['id']}/image").headers["content-type"] == "image/png"
    client.post("/api/admin/logout", headers=csrf)
    assert client.get(f"/api/admin/agents/{run['id']}/image").status_code == 401


def test_blocked_feedback_exact_image_retries_and_life_reset(evaluation):
    client, runner, store = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", json=config(lives=5), headers=csrf).json()
    calls = []
    decisions = iter([1, 2, 1, 2])

    def model(config, key, prompt, png):
        calls.append((prompt, png))
        assert key == "private-test-key"
        assert "legal_arrow_ids" not in prompt
        assert "matrix" not in prompt
        return respond(json.dumps({"arrow_id": next(decisions), "explanation": "A visible lane."}))

    runner.invoke = model
    before = client.get(f"/api/admin/agents/{run['id']}/image").content
    request_id = str(uuid4())
    blocked = step(client, csrf, run, request_id).json()
    assert blocked["state"]["lives_remaining"] == 4 and blocked["state"]["mistakes"] == 1
    assert calls[0][1] == before
    assert "arrow 1" in blocked["feedback"] and "arrow 2" in blocked["feedback"]
    assert "lost one life" in blocked["feedback"] and "4 lives remaining" in blocked["feedback"]
    assert step(client, csrf, run, request_id).json()["state"]["moves"] == 1
    assert len(calls) == 1
    step(client, csrf, run)
    won = step(client, csrf, run).json()
    assert won["state"]["status"] == "won" and won["results"][0]["status"] == "won"
    next_level = step(client, csrf, run).json()
    assert next_level["state"]["level"]["number"] == 2
    assert next_level["state"]["lives_remaining"] == next_level["state"]["max_lives"] == 5
    assert "fresh level" in calls[3][0]
    assert len(store.sessions) == 2
    turns = client.get(f"/api/admin/agents/{run['id']}/turns?limit=2").json()
    assert [t["number"] for t in turns["items"]] == [4, 3] and turns["next_before"] == 3
    older = client.get(f"/api/admin/agents/{run['id']}/turns?before=3").json()["items"]
    assert [t["number"] for t in older] == [2, 1]
    old_png = client.get(f"/api/admin/agents/{run['id']}/image?turn_id={request_id}").content
    assert old_png == before
    with Image.open(BytesIO(old_png)) as image:
        assert image.format == "PNG" and image.width > 100


@pytest.mark.parametrize(
    "raw",
    [
        '{"arrow_id": 999}',
        '{"arrow_id": true}',
        '{"arrow_id": "2"}',
        "hello",
        '{"arrow_id": 0}',
        '[{"arrow_id": 2}]',
    ],
)
def test_invalid_actions_cost_lives_and_end_attempt(evaluation, raw):
    client, runner, _ = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", headers=csrf, json=config(lives=1, end_level=1)).json()
    runner.invoke = lambda *_: respond(raw)
    result = step(client, csrf, run).json()
    assert result["state"]["lives_remaining"] == 0
    assert result["state"]["status"] == "lost" and result["status"] == "completed"
    assert result["state"]["removed_ids"] == []
    turn = client.get(f"/api/admin/agents/{run['id']}/turns").json()["items"][0]
    assert turn["raw_response"] == raw and turn["outcome"]["result"] == "invalid"
    assert turn["after"]["lives_remaining"] == 0


def test_provider_errors_pause_without_scoring_and_do_not_leak_keys(evaluation):
    client, runner, _ = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", headers=csrf, json=config()).json()

    def fail(*_):
        raise ValueError("invalid private-test-key")

    runner.invoke = fail
    request_id = str(uuid4())
    result = step(client, csrf, run, request_id).json()
    assert result["status"] == "error" and result["state"]["lives_remaining"] == 3
    assert result["state"]["moves"] == 0 and "No life was lost" in result["error"]
    assert "private-test-key" not in json.dumps(result)
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    # A retry of the failed request is read-only. A new request is intentional.
    assert step(client, csrf, run, request_id).json()["state"]["moves"] == 0
    assert step(client, csrf, run).json()["state"]["moves"] == 1


def test_concurrent_steps_and_pause_stop_controls(evaluation):
    client, runner, _ = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", headers=csrf, json=config()).json()
    entered, release = threading.Event(), threading.Event()

    def model(*_):
        entered.set()
        assert release.wait(5)
        return respond('{"arrow_id": 2}')

    runner.invoke = model
    with ThreadPoolExecutor() as pool:
        first = pool.submit(runner.step, run["id"], str(uuid4()))
        assert entered.wait(5)
        with pytest.raises(GameError, match="already in progress"):
            runner.step(run["id"], str(uuid4()))
        runner.control(run["id"], "pause")
        release.set()
        result = first.result(timeout=5)
    assert result["status"] == "paused" and result["state"]["moves"] == 1
    runner.control(run["id"], "stop")
    with pytest.raises(GameError, match="ended"):
        runner.step(run["id"], str(uuid4()))


def test_stop_during_call_discards_pending_action(evaluation):
    _, runner, _ = evaluation
    run = runner.create(CreateAgentRun(**config()))

    def stop(*_):
        runner.control(run["id"], "stop")
        return respond('{"arrow_id": 2}')

    runner.invoke = stop
    result = runner.step(run["id"], str(uuid4()))
    assert result["status"] == "stopped" and result["state"]["moves"] == 0
    assert runner.store.list_agent_turns(run["id"])["items"][0]["status"] == "cancelled"


def test_recover_saved_response_after_game_commit_without_recalling_model(evaluation, monkeypatch):
    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config()))
    calls = []
    runner.invoke = lambda *_: calls.append(1) or respond('{"arrow_id": 2}')
    original = runner._finish
    monkeypatch.setattr(runner, "_finish", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    request_id = str(uuid4())
    with pytest.raises(RuntimeError, match="crash"):
        runner.step(run["id"], request_id)
    assert runner.detail(run["id"])["state"]["moves"] == 1
    monkeypatch.setattr(runner, "_finish", original)
    recovered = runner.step(run["id"], request_id)
    assert recovered["state"]["moves"] == recovered["turn_count"] == 1
    assert len(calls) == 1 and not store.get_agent_run(run["id"])["pending"]


def test_range_validation_state_mode_and_turn_limit(evaluation):
    client, runner, _ = evaluation
    csrf = login(client)
    assert (
        client.post("/api/admin/agents", json=config(end_level=3), headers=csrf).status_code == 422
    )
    assert (
        client.post("/api/admin/agents", json=config(end_level=0), headers=csrf).status_code == 422
    )
    assert (
        client.post("/api/admin/agents", json=config(api_key=None), headers=csrf).status_code == 422
    )
    run = runner.create(
        CreateAgentRun(**config(max_turns_per_level=1, observation_mode="image_and_state"))
    )
    assert '"matrix"' in run["feedback"] and '"arrows"' in run["feedback"]
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    result = runner.step(run["id"], str(uuid4()))
    assert result["status"] == "paused" and "turn limit" in result["error"]
    with pytest.raises(GameError, match="turn limit"):
        runner.step(run["id"], str(uuid4()))


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-6-luna",
        "gpt-6.1-sol",
        "gpt-6-astra",
        "opus-5",
        "opus-5.5",
        "fable-5.1",
        "gemini-3.8-flash",
    ],
)
def test_ufl_adapter_sends_chat_completions_rules_and_image(model_id, monkeypatch):
    from openai import OpenAI

    requests, clients = [], []

    def transport(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test-response",
                "object": "chat.completion",
                "created": 1,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"arrow_id": 2}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def client(**kwargs):
        clients.append(kwargs)
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

    monkeypatch.setattr("backend.app.ufl.OpenAI", client)
    data = config(model=model_id, max_output_tokens=4096, timeout_seconds=10)
    response = invoke_model(
        data, "dummy-test-key", "visible state", b"png-test-bytes", base_url="https://ufl.test"
    )
    assert response["raw_response"] == '{"arrow_id": 2}'
    assert response["usage"] == {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ufl.test/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer dummy-test-key"
    body = json.loads(requests[0].content)
    assert body["model"] == model_id and body["max_tokens"] == 4096
    assert body["messages"][0] == {"role": "system", "content": RULES}
    assert body["messages"][1]["content"][0]["text"] == "visible state"
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert clients[0]["max_retries"] == 0 and clients[0]["timeout"] == 10


def test_runs_use_ufl_server_key_and_normalize_legacy_provider(evaluation):
    _, runner, store = evaluation
    runner.settings.ufl_api_key = SecretStr("server-ufl-key")
    run = runner.create(
        CreateAgentRun(**config(provider="anthropic", api_key=None, model="opus-5"))
    )
    assert run["config"]["provider"] == "ufl" and run["config"]["model"] == "opus-5"
    assert (
        runner.cipher().decrypt(store.get_agent_run(run["id"])["credential"].encode())
        == b"server-ufl-key"
    )
    assert "server-ufl-key" not in json.dumps(run)
    assert runner.providers()[0]["configured"] is True


def test_fenced_json_is_accepted_but_prose_is_not():
    assert parse_decision('```json\n{"arrow_id": 2}\n```', [2])["arrow_id"] == 2
    with pytest.raises(ValueError):
        parse_decision('I choose {"arrow_id": 2}', [2])


def test_deleting_agent_player_removes_credentials_attempts_and_transcripts(evaluation):
    client, runner, store = evaluation
    csrf = login(client)
    run = runner.create(CreateAgentRun(**config()))
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    runner.step(run["id"], str(uuid4()))
    assert (
        client.delete(f"/api/admin/sessions/{run['current_session_id']}", headers=csrf).status_code
        == 409
    )
    response = client.delete(f"/api/admin/players/{run['player_id']}", headers=csrf)
    assert response.status_code == 200
    assert not store.agent_runs and not store.agent_turns and not store.sessions
    assert client.get(f"/api/admin/agents/{run['id']}").status_code == 404
