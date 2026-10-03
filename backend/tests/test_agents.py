import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIStatusError, APITimeoutError
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


@pytest.fixture
def retry_clock(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("backend.app.agents.time.time", lambda: clock[0])
    return clock


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
def test_invalid_actions_are_unscored_and_idempotent(evaluation, raw):
    client, runner, _ = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", headers=csrf, json=config(lives=1, end_level=1)).json()
    runner.invoke = lambda *_: respond(raw)
    request_id = str(uuid4())
    result = step(client, csrf, run, request_id).json()
    assert result["state"]["lives_remaining"] == 1
    assert result["state"]["status"] == "active" and result["status"] == "paused"
    assert result["state"]["moves"] == result["state"]["mistakes"] == 0
    assert result["state"]["revision"] == 0 and result["results"] == []
    assert result["state"]["removed_ids"] == []
    turn = client.get(f"/api/admin/agents/{run['id']}/turns").json()["items"][0]
    assert turn["raw_response"] == raw and turn["outcome"]["result"] == "invalid"
    assert turn["after"] == turn["before"] and turn["outcome"]["reward"] == 0
    assert "No life was lost" in result["feedback"]
    assert step(client, csrf, run, request_id).json()["turn_count"] == 1


def test_provider_errors_pause_without_scoring_and_do_not_leak_keys(evaluation, retry_clock):
    client, runner, _ = evaluation
    csrf = login(client)
    run = client.post("/api/admin/agents", headers=csrf, json=config()).json()

    def fail(*_):
        raise ValueError("invalid private-test-key")

    runner.invoke = fail
    request_id = str(uuid4())
    result = step(client, csrf, run, request_id).json()
    assert result["status"] == "paused" and result["state"]["lives_remaining"] == 3
    assert result["state"]["moves"] == 0 and "No life was lost" in result["error"]
    assert "private-test-key" not in json.dumps(result)
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    # A retry of the failed request is read-only. A new request is intentional.
    assert step(client, csrf, run, request_id).json()["state"]["moves"] == 0
    retry_clock[0] += 6
    assert step(client, csrf, run).json()["state"]["moves"] == 1


@pytest.mark.parametrize(
    "status,retryable",
    [(400, False), (401, False), (403, False), (404, False), (408, True), (429, True), (503, True)],
)
def test_provider_status_diagnostics_retry_without_limit_and_preserve_progress(
    evaluation, retry_clock, status, retryable
):
    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config()))
    runner.control(run["id"], "resume")
    calls = []

    def fail(*_):
        calls.append(1)
        response = httpx.Response(
            status, request=httpx.Request("POST", "https://ufl.test"), headers={"Retry-After": "30"}
        )
        raise APIStatusError(
            "private-test-key", response=response, body={"error": "private-test-key"}
        )

    runner.invoke = fail
    first_id = str(uuid4())
    result = runner.step(run["id"], first_id)
    assert result["diagnostic"]["http_status"] == status
    assert result["diagnostic"]["retryable"] is retryable
    assert "private-test-key" not in json.dumps(result)
    assert result["state"]["lives_remaining"] == 3 and result["state"]["moves"] == 0
    assert result["status"] == "running"
    assert runner.step(run["id"], first_id)["turn_count"] == 1
    assert result["retry_at"] == retry_clock[0] + (30 if retryable else 300)
    assert runner.step(run["id"], str(uuid4()))["turn_count"] == 1
    assert len(calls) == 1
    for expected in range(2, 13):
        retry_clock[0] = result["retry_at"] + 1
        result = runner.step(run["id"], str(uuid4()))
        assert result["consecutive_failures"] == expected
        assert result["status"] == "running"
        assert result["state"]["moves"] == result["state"]["mistakes"] == 0
    assert len(calls) == 12
    assert result["retry_at"] - retry_clock[0] == (60 if retryable else 300)
    assert "retries stopped" not in result["error"]
    assert store.get_session(run["current_session_id"], run["player_id"])["history"] == []
    retry_clock[0] = result["retry_at"] + 1
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    recovered = runner.step(run["id"], str(uuid4()))
    assert recovered["state"]["moves"] == 1 and recovered["state"]["lives_remaining"] == 3
    assert recovered["consecutive_failures"] == 0 and recovered["retry_at"] is None


def test_output_exhaustion_increases_budget_and_preserves_state(evaluation, retry_clock):
    _, runner, _ = evaluation
    run = runner.create(CreateAgentRun(**config(thinking_effort="max")))
    runner.control(run["id"], "resume")
    budgets = []
    efforts = []

    def response(config, *_):
        budgets.append(config["max_output_tokens"])
        efforts.append(config["thinking_effort"])
        return {
            **respond(""),
            "finish_reason": "length",
            "usage": {"output_tokens": config["max_output_tokens"]},
        }

    runner.invoke = response
    for expected in [8192, 16384, 32768, 64000, 64000, 64000]:
        result = runner.step(run["id"], str(uuid4()))
        assert result["request_max_output_tokens"] == expected
        assert result["state"]["revision"] == result["state"]["mistakes"] == 0
        assert result["state"]["lives_remaining"] == 3
        assert f"{expected:,}" in result["error"]
        retry_clock[0] = result["retry_at"] + 1
    assert budgets == [4096, 8192, 16384, 32768, 64000, 64000]
    assert result["status"] == "running" and efforts == ["max"] * 6
    assert "keeps the 64,000-token budget" in result["error"]
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    recovered = runner.step(run["id"], str(uuid4()))
    assert recovered["state"]["moves"] == 1 and recovered["consecutive_failures"] == 0


@pytest.mark.parametrize("raw", ["", '{"arrow_id":', '{"arrow_id": 999}'])
def test_length_stops_without_a_valid_move_retry_unscored(evaluation, retry_clock, raw):
    _, runner, _ = evaluation
    run = runner.create(
        CreateAgentRun(
            **config(model="claude-opus-5.5", thinking_effort="high", max_output_tokens=16384)
        )
    )
    runner.control(run["id"], "resume")
    runner.invoke = lambda *_: {**respond(raw), "finish_reason": "length"}
    result = runner.step(run["id"], str(uuid4()))
    assert result["diagnostic"]["code"] == "output_limit"
    assert result["status"] == "running" and result["request_max_output_tokens"] == 32768
    assert (
        result["state"]["moves"] == result["state"]["mistakes"] == result["state"]["revision"] == 0
    )
    assert result["state"]["lives_remaining"] == 3
    assert result["config"]["thinking_effort"] == "high"


def test_agent_allows_ufl_opus_output_ceiling_and_rejects_larger_requests(evaluation):
    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config(model="claude-opus-5.5", max_output_tokens=64000)))
    assert run["request_max_output_tokens"] == 64000
    with pytest.raises(ValueError):
        CreateAgentRun(**config(max_output_tokens=64001))
    assert len(store.agent_runs) == 1


def test_timeout_then_pause_and_resume_after_runner_restart(evaluation, retry_clock):
    from backend.app.agents import AgentRunner

    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config()))
    runner.control(run["id"], "resume")

    def timeout(*_):
        runner.control(run["id"], "pause")
        raise APITimeoutError(request=httpx.Request("POST", "https://ufl.test"))

    runner.invoke = timeout
    result = runner.step(run["id"], str(uuid4()))
    assert result["status"] == "paused" and result["diagnostic"]["code"] == "timeout"
    later = AgentRunner(store, runner.settings)
    later.control(run["id"], "resume")
    later.invoke = lambda *_: respond('{"arrow_id": 2}')
    retry_clock[0] += 6
    resumed = later.step(run["id"], str(uuid4()))
    assert resumed["state"]["session_id"] == run["current_session_id"]
    assert resumed["state"]["lives_remaining"] == 3 and resumed["state"]["moves"] == 1


def test_timeouts_increase_allowance_and_eventually_finish_without_admin_resume(
    evaluation, retry_clock
):
    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config(end_level=1, timeout_seconds=90)))
    runner.control(run["id"], "resume")
    timeouts = []

    def timeout(config, *_):
        timeouts.append(config["timeout_seconds"])
        raise APITimeoutError(request=httpx.Request("POST", "https://ufl.test"))

    runner.invoke = timeout
    for failure in range(1, 9):
        result = runner.step(run["id"], str(uuid4()))
        assert result["status"] == "running" and result["consecutive_failures"] == failure
        assert result["state"]["lives_remaining"] == 3 and result["state"]["revision"] == 0
        assert result["request_timeout_seconds"] == 180
        retry_clock[0] = result["retry_at"] + 1
    assert timeouts == [90, *([180] * 7)]
    assert store.list_agent_turns(run["id"])["items"][0]["timeout_seconds"] == 180
    decisions = iter([2, 1])
    runner.invoke = lambda *_: respond(json.dumps({"arrow_id": next(decisions)}))
    recovered = runner.step(run["id"], str(uuid4()))
    assert recovered["status"] == "running" and recovered["state"]["moves"] == 1
    assert recovered["consecutive_failures"] == 0 and recovered["retry_at"] is None
    completed = runner.step(run["id"], str(uuid4()))
    assert completed["status"] == "completed" and completed["state"]["status"] == "won"


@pytest.mark.parametrize("action", ["pause", "stop"])
def test_admin_control_during_repeated_failures_is_not_overwritten(evaluation, retry_clock, action):
    _, runner, _ = evaluation
    run = runner.create(CreateAgentRun(**config()))
    runner.control(run["id"], "resume")

    def timeout(*_):
        raise APITimeoutError(request=httpx.Request("POST", "https://ufl.test"))

    runner.invoke = timeout
    for _ in range(5):
        result = runner.step(run["id"], str(uuid4()))
        retry_clock[0] = result["retry_at"] + 1

    def controlled_timeout(*args):
        runner.control(run["id"], action)
        timeout(*args)

    runner.invoke = controlled_timeout
    result = runner.step(run["id"], str(uuid4()))
    assert result["status"] == ("paused" if action == "pause" else "stopped")
    assert result["state"]["moves"] == 0 and result["state"]["lives_remaining"] == 3


def test_refused_responses_keep_running_with_slow_rechecks(evaluation, retry_clock):
    _, runner, _ = evaluation
    run = runner.create(CreateAgentRun(**config()))
    runner.control(run["id"], "resume")
    runner.invoke = lambda *_: {**respond(""), "refused": True}
    for failure in range(1, 6):
        result = runner.step(run["id"], str(uuid4()))
        assert result["status"] == "running" and result["consecutive_failures"] == failure
        assert result["diagnostic"]["code"] == "refusal"
        assert result["retry_at"] == retry_clock[0] + 300
        assert result["state"]["moves"] == result["state"]["mistakes"] == 0
        retry_clock[0] = result["retry_at"] + 1


def test_stopped_session_can_resume_existing_board(evaluation):
    _, runner, _ = evaluation
    run = runner.create(CreateAgentRun(**config(end_level=1)))
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    runner.step(run["id"], str(uuid4()))
    runner.control(run["id"], "stop")
    resumed = runner.control(run["id"], "resume")
    assert resumed["state"]["removed_ids"] == [2]
    assert resumed["state"]["session_id"] == run["current_session_id"]
    runner.invoke = lambda *_: respond('{"arrow_id": 1}')
    completed = runner.step(run["id"], str(uuid4()))
    assert completed["status"] == "completed"
    with pytest.raises(GameError, match="completed"):
        runner.control(run["id"], "resume")


def test_detailed_download_includes_every_page_and_excludes_credentials(evaluation, retry_clock):
    client, runner, store = evaluation
    csrf = login(client)
    run = runner.create(CreateAgentRun(**config()))
    runner.invoke = lambda *_: respond("invalid JSON")
    for _ in range(105):
        result = runner.step(run["id"], str(uuid4()))
        retry_clock[0] = result["retry_at"] + 1
    runner.invoke = lambda *_: respond('{"arrow_id": 2}')
    runner.step(run["id"], str(uuid4()))
    runner.control(run["id"], "stop")
    response = client.get(f"/api/admin/agents/{run['id']}/history/download")
    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert [t["number"] for t in report["turns"]] == list(range(106, 0, -1))
    assert report["summary"]["invalid_responses"] == 105
    assert report["summary"]["cleared"] == 1
    assert report["summary"]["tokens"]["total_tokens"] == 106 * 110
    assert report["per_level"]["level-001"]["requests"] == 106
    assert report["sessions"][0]["action_history"][0]["result"] == "cleared"
    assert report["levels"][0]["board"]["matrix"]
    encrypted = store.get_agent_run(run["id"])["credential"]
    assert encrypted not in response.text and "private-test-key" not in response.text
    assert "credential" not in report["run"] and "pending" not in report["run"]
    runner.control(run["id"], "resume")
    assert client.get(f"/api/admin/agents/{run['id']}/history/download").status_code == 409
    client.post("/api/admin/logout", headers=csrf)
    assert client.get(f"/api/admin/agents/{run['id']}/history/download").status_code == 401


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


def test_recover_saved_response_after_game_commit_without_recalling_model(
    evaluation, monkeypatch, retry_clock
):
    _, runner, store = evaluation
    run = runner.create(CreateAgentRun(**config()))
    runner.control(run["id"], "resume")
    calls = []
    runner.invoke = lambda *_: calls.append(1) or respond('{"arrow_id": 2}')
    original = runner._finish
    monkeypatch.setattr(runner, "_finish", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    request_id = str(uuid4())
    for failure in range(1, 6):
        with pytest.raises(RuntimeError, match="crash"):
            runner.step(run["id"], request_id if failure == 1 else str(uuid4()))
        interrupted = runner.detail(run["id"])
        assert interrupted["state"]["moves"] == 1 and interrupted["status"] == "running"
        assert interrupted["consecutive_failures"] == failure
        assert interrupted["diagnostic"]["code"] == "infrastructure_error"
        waiting = runner.step(run["id"], request_id)
        assert waiting["turn_count"] == 0 and len(calls) == 1
        retry_clock[0] = interrupted["retry_at"] + 1
    monkeypatch.setattr(runner, "_finish", original)
    recovered = runner.step(run["id"], request_id)
    assert recovered["state"]["moves"] == recovered["turn_count"] == 1
    assert recovered["status"] == "running" and recovered["consecutive_failures"] == 0
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


@pytest.fixture
def category_evaluation(evaluation):
    client, runner, store = evaluation
    template = store.get_level("level-001")
    entries = [
        *[(number, "easy") for number in range(2, 36, 3)],
        (1, "medium"),
        (7, "expert"),
        (40, "expert"),
        (90, "expert"),
        (41, "hard"),
    ]
    store.set_catalog(
        [
            template.model_copy(
                update={
                    "id": f"level-{number:03}",
                    "number": number,
                    "difficulty": difficulty,
                }
            )
            for number, difficulty in reversed(entries)
        ]
    )
    return client, runner, store


def category_config(**overrides):
    return config(
        selection_mode="category",
        start_level=None,
        end_level=None,
        difficulty="easy",
        **overrides,
    )


def test_category_first_ten_uses_sorted_matches_and_saves_exact_selection(category_evaluation):
    client, _, store = category_evaluation
    csrf = login(client)
    response = client.post(
        "/api/admin/agents",
        headers=csrf,
        json=category_config(level_limit=10, thinking_effort="max"),
    )
    assert response.status_code == 201
    run = response.json()
    assert run["level_ids"] == [f"level-{number:03}" for number in range(2, 30, 3)]
    assert run["state"]["level"]["number"] == 2
    assert run["config"]["selection_mode"] == "category"
    assert run["config"]["difficulty"] == "easy" and run["config"]["level_limit"] == 10
    assert run["config"]["start_level"] is run["config"]["end_level"] is None
    assert store.get_agent_run(run["id"])["level_ids"] == run["level_ids"]
    report = client.get(f"/api/admin/agents/{run['id']}/history/download").json()
    assert report["run"]["level_ids"] == run["level_ids"]
    assert report["run"]["config"]["difficulty"] == "easy"
    assert report["run"]["config"]["level_limit"] == 10


def test_all_expert_advances_across_gaps_and_completes_only_selected_levels(category_evaluation):
    client, runner, _ = category_evaluation
    csrf = login(client)
    response = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={**category_config(), "difficulty": "expert"},
    )
    assert response.status_code == 201
    run = response.json()
    assert run["level_ids"] == ["level-007", "level-040", "level-090"]
    assert run["config"]["level_limit"] is None
    decisions = iter([2, 1] * 3)
    runner.invoke = lambda *_: respond(json.dumps({"arrow_id": next(decisions)}))
    observed = []
    for _ in range(6):
        result = step(client, csrf, run).json()
        observed.append(result["state"]["level"]["number"])
    assert observed == [7, 7, 40, 40, 90, 90]
    assert result["status"] == "completed" and len(result["results"]) == 3
    assert [entry["level_id"] for entry in result["results"]] == run["level_ids"]
    assert all(entry["status"] == "won" for entry in result["results"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"level_limit": 13},  # Only twelve easy problems exist.
        {"level_limit": 0},
        {"level_limit": -1},
        {"level_limit": 1001},
        {"level_limit": 1.5},
        {"level_limit": True},
        {"difficulty": None},
        {"difficulty": "unknown"},
        {"start_level": 1},
        {"end_level": 2},
        {"selection_mode": "range"},
    ],
)
def test_invalid_category_selections_have_no_side_effects(category_evaluation, overrides):
    client, _, store = category_evaluation
    response = client.post(
        "/api/admin/agents",
        headers=login(client),
        json={**category_config(), **overrides},
    )
    assert response.status_code == 422
    assert store.players == {} and store.sessions == {} and store.agent_runs == {}


def test_empty_category_is_rejected_before_creating_a_player(evaluation):
    client, _, store = evaluation
    response = client.post(
        "/api/admin/agents",
        headers=login(client),
        json={**category_config(), "difficulty": "expert"},
    )
    assert response.status_code == 422
    assert "No expert problems" in response.json()["detail"]
    assert store.players == {} and store.sessions == {} and store.agent_runs == {}


def test_category_all_enforces_the_existing_per_run_limit(evaluation):
    client, _, store = evaluation
    template = store.get_level("level-001")
    store.set_catalog(
        [
            template.model_copy(update={"id": f"level-{number:04}", "number": number})
            for number in range(1, 1002)
        ]
    )
    csrf = login(client)
    assert (
        client.post(
            "/api/admin/agents",
            headers=csrf,
            json=category_config(),
        ).status_code
        == 422
    )
    assert store.players == {} and store.sessions == {} and store.agent_runs == {}
    response = client.post(
        "/api/admin/agents",
        headers=csrf,
        json=category_config(level_limit=1000),
    )
    assert response.status_code == 201 and len(response.json()["level_ids"]) == 1000


def test_range_clients_keep_their_selection_and_reject_ambiguous_category_fields(evaluation):
    client, _, _ = evaluation
    csrf = login(client)
    response = client.post("/api/admin/agents", headers=csrf, json=config())
    assert response.status_code == 201
    assert response.json()["level_ids"] == ["level-001", "level-002"]
    assert response.json()["config"]["selection_mode"] == "range"
    assert (
        client.post(
            "/api/admin/agents",
            headers=csrf,
            json=config(difficulty="easy"),
        ).status_code
        == 422
    )


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
