import base64
import json
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from PIL import Image
from pydantic import SecretStr

from backend.app.admin import hash_password
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.repository import MemoryRepository
from backend.app.ufl import MODEL_PRESETS


@pytest.fixture
def health_app(tmp_path, monkeypatch):
    (tmp_path / "level.json").write_text(
        json.dumps(
            {
                "id": "level-001",
                "number": 1,
                "name": "One arrow",
                "difficulty": "easy",
                "matrix": [[1, 1], [0, 0]],
                "arrows": [{"id": 1, "path": [[0, 0], [0, 1]]}],
            }
        )
    )
    settings = Settings(
        _env_file=None,
        storage_backend="memory",
        levels_dir=tmp_path,
        admin_password_hash=SecretStr(hash_password("test-password")),
        admin_session_secret=SecretStr("test-secret-" * 5),
        ufl_api_key=SecretStr("private-ufl-test-key"),
        ufl_base_url="https://ufl.test/proxy/",
    )
    requests = []
    reply = {"status": 200, "body": completion()}

    def transport(request):
        requests.append(request)
        if reply.get("exception"):
            raise reply["exception"]
        return httpx.Response(reply["status"], json=reply["body"])

    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

    monkeypatch.setattr("backend.app.ufl.OpenAI", client)
    monkeypatch.setattr("backend.app.api_health.OpenAI", client)
    store = MemoryRepository()
    with TestClient(create_app(settings, store)) as client:
        yield client, settings, requests, reply, store


def completion(content="UFL_OK", finish_reason="stop", **overrides):
    return {
        "id": "health-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gateway-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        **overrides,
    }


def login(client):
    result = client.post(
        "/api/admin/login", json={"username": "admin", "password": "test-password"}
    )
    assert result.status_code == 200
    return {"X-Admin-CSRF": result.json()["csrf_token"]}


def check(client, csrf, **overrides):
    return client.post(
        "/api/admin/api-health/check", headers=csrf, json={"model": "gpt-6-luna", **overrides}
    )


def test_checks_require_admin_and_csrf_and_configuration_is_read_only(health_app):
    client, settings, requests, _, _ = health_app
    assert client.get("/api/admin/api-health").status_code == 401
    assert check(client, {}).status_code == 401
    assert client.post("/api/admin/api-health/models").status_code == 401
    csrf = login(client)
    assert check(client, {}).status_code == 403
    assert client.post("/api/admin/api-health/models").status_code == 403
    result = client.get("/api/admin/api-health")
    assert result.status_code == 200
    assert result.json()["configured"] is True
    assert result.json()["models"] == MODEL_PRESETS
    assert result.json()["base_url"] == "https://ufl.test/proxy"
    assert "private-ufl-test-key" not in result.text
    assert "private-ufl-test-key" not in settings.model_dump_json()
    assert requests == []
    assert check(client, csrf).json()["status"] == "healthy"


@pytest.mark.parametrize("model", [preset["id"] for preset in MODEL_PRESETS])
def test_real_sdk_request_format_and_health_results(health_app, model):
    client, _, requests, _, store = health_app
    result = check(client, login(client), model=model).json()
    assert result["status"] == "healthy"
    assert result["response"] == "UFL_OK" and result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
    assert result["latency_ms"] >= 0 and result["response_model"] == "gateway-model"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ufl.test/proxy/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer private-ufl-test-key"
    body = json.loads(requests[0].content)
    assert set(body) == {"model", "messages"}
    assert body["model"] == model and body["messages"][0]["role"] == "user"
    assert "UFL_OK" in body["messages"][0]["content"]
    assert store.players == {} and store.sessions == {} and store.agent_runs == {}


def test_image_check_sends_valid_png_and_checks_vision_reply(health_app):
    client, _, requests, reply, _ = health_app
    reply["body"] = completion("blue")
    result = check(client, login(client), mode="image").json()
    assert result["status"] == "healthy" and result["expected_response"] == "blue"
    content = json.loads(requests[0].content)["messages"][0]["content"]
    assert content[0]["type"] == "text"
    encoded = content[1]["image_url"]["url"].split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert image.format == "PNG" and image.getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize(
    "model,effort,parameters",
    [
        ("gpt-6-luna", "none", {"reasoning_effort": "none"}),
        ("gpt-6-luna", "max", {"reasoning_effort": "max"}),
        ("gpt-6.1-sol", "xhigh", {"reasoning_effort": "xhigh"}),
        ("gpt-6-astra", "max", {"reasoning_effort": "max"}),
        ("gemini-3.8-flash", "low", {"reasoning_effort": "low"}),
        ("gemini-3.8-flash", "high", {"reasoning_effort": "high"}),
        (
            "claude-opus-5.5",
            "max",
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}},
        ),
        (
            "opus-5",
            "xhigh",
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "xhigh"}},
        ),
        (
            "fable-5.1",
            "low",
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        ),
        (
            "anthropic/claude-opus-5-5-20260901",
            "max",
            {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}},
        ),
    ],
)
def test_selected_thinking_reaches_gateway_with_native_parameters(
    health_app, model, effort, parameters
):
    client, _, requests, _, _ = health_app
    response = check(
        client,
        login(client),
        model=model,
        thinking_effort=effort,
        max_output_tokens=8192,
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "healthy"
    assert result["thinking_effort"] == effort
    assert result["thinking_parameters"] == parameters
    body = json.loads(requests[0].content)
    assert body == {
        "model": model,
        "messages": body["messages"],
        "max_tokens": 8192,
        **parameters,
    }


@pytest.mark.parametrize(
    "model,effort",
    [
        ("gemini-3.8-flash", "max"),
        ("gemini-3.8-flash", "none"),
        ("gpt-6.1-sol", "none"),
        ("gpt-6-astra", "none"),
        ("claude-opus-5.5", "none"),
        ("unverified-custom-model", "high"),
        ("gpt-6-luna", "very-high"),
    ],
)
def test_unsupported_thinking_is_rejected_before_gateway_or_run_creation(health_app, model, effort):
    client, _, requests, _, store = health_app
    csrf = login(client)
    assert check(client, csrf, model=model, thinking_effort=effort).status_code == 422
    response = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={
            "name": "Invalid thinking",
            "model": model,
            "thinking_effort": effort,
            "start_level": 1,
            "end_level": 1,
        },
    )
    assert response.status_code == 422
    assert requests == []
    assert store.players == {} and store.sessions == {} and store.agent_runs == {}


def test_default_thinking_still_accepts_custom_models_and_sends_no_overrides(health_app):
    client, _, requests, _, _ = health_app
    result = check(
        client,
        login(client),
        model="unverified-custom-model",
        thinking_effort=None,
    ).json()
    assert result["status"] == "healthy" and result["thinking_parameters"] == {}
    assert set(json.loads(requests[0].content)) == {"model", "messages"}


def test_catalog_and_selected_thinking_are_saved_in_runs_transcripts_and_exports(health_app):
    client, _, requests, reply, store = health_app
    csrf = login(client)
    catalog = client.get("/api/admin/agents/providers").json()["thinking_models"]
    assert catalog == client.get("/api/admin/api-health").json()["thinking_models"]
    assert "none" in catalog["gpt-6-luna"]["efforts"]
    assert "none" not in catalog["gpt-6-astra"]["efforts"]
    assert "max" in catalog["claude-opus-5.5"]["efforts"]
    assert "max" not in catalog["gemini-3.8-flash"]["efforts"]
    assert requests == []
    run = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={
            "name": "Max thinking",
            "model": "claude-opus-5.5",
            "thinking_effort": "max",
            "start_level": 1,
            "end_level": 1,
        },
    ).json()
    reply["body"] = completion('{"arrow_id": 1}')
    result = client.post(
        f"/api/admin/agents/{run['id']}/step",
        headers=csrf,
        json={"request_id": str(uuid4())},
    ).json()
    assert result["status"] == "completed" and result["config"]["thinking_effort"] == "max"
    parameters = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}}
    body = json.loads(requests[0].content)
    assert body["thinking"] == parameters["thinking"]
    assert body["output_config"] == parameters["output_config"]
    turn = store.list_agent_turns(run["id"])["items"][0]
    assert turn["thinking_effort"] == "max" and turn["thinking_parameters"] == parameters
    report = client.get(f"/api/admin/agents/{run['id']}/history/download").json()
    assert report["run"]["config"]["thinking_effort"] == "max"
    assert report["turns"][0]["thinking_parameters"] == parameters


@pytest.mark.parametrize(
    "body",
    [
        completion(None),
        completion(""),
        completion("   "),
        completion(choices=[]),
        completion(choices=None),
        {"detail": "private-ufl-test-key"},
        completion(choices=[{"index": 0, "message": None, "finish_reason": "stop"}]),
        completion(
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "no", "refusal": "refused"},
                    "finish_reason": "stop",
                }
            ]
        ),
    ],
)
def test_http_success_with_missing_or_empty_completion_fails(health_app, body):
    client, _, _, reply, _ = health_app
    reply["body"] = body
    result = check(client, login(client))
    assert result.status_code == 200 and result.json()["status"] == "error"
    assert result.json()["error"]["code"] == "invalid_response"
    assert "private-ufl-test-key" not in result.text


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "authentication_error"),
        (403, "permission_denied"),
        (404, "not_found"),
        (429, "rate_limited"),
        (503, "upstream_error"),
    ],
)
def test_gateway_http_errors_are_distinguished_without_secret_leaks(health_app, status, code):
    client, _, requests, reply, _ = health_app
    reply.update(status=status, body={"error": {"message": "private-ufl-test-key", "type": "test"}})
    result = check(client, login(client))
    assert result.json()["status"] == "error"
    assert result.json()["error"]["code"] == code
    assert result.json()["error"]["http_status"] == status
    if status == 403:
        assert "Discover model IDs" in result.json()["error"]["message"]
    assert "private-ufl-test-key" not in result.text
    assert len(requests) == 1  # Do not retry a billable check.


@pytest.mark.parametrize(
    ("exception", "code", "transport_error"),
    [
        (httpx.ReadTimeout("private-ufl-test-key"), "timeout", None),
        (httpx.ConnectError("private-ufl-test-key"), "connection_error", "connect_error"),
        (
            httpx.RemoteProtocolError("private-ufl-test-key"),
            "connection_error",
            "remote_protocol_error",
        ),
        (httpx.ReadError("private-ufl-test-key"), "connection_error", "read_error"),
        (httpx.DecodingError("private-ufl-test-key"), "connection_error", "decoding_error"),
    ],
)
def test_timeout_and_network_errors(health_app, exception, code, transport_error):
    client, _, requests, reply, _ = health_app
    reply["exception"] = exception
    result = check(client, login(client))
    assert result.json()["error"]["code"] == code
    assert result.json()["error"].get("transport_error") == transport_error
    assert "private-ufl-test-key" not in result.text and len(requests) == 1


@pytest.mark.parametrize(
    ("content", "finish_reason"), [("different answer", "stop"), ("UFL_OK", "length")]
)
def test_unexpected_or_truncated_reply_is_a_warning(health_app, content, finish_reason):
    client, _, _, reply, _ = health_app
    reply["body"] = completion(content, finish_reason)
    result = check(client, login(client)).json()
    assert result["status"] == "warning" and result["response"] == content


def test_model_discovery_and_optional_usage(health_app):
    client, _, requests, reply, _ = health_app
    csrf = login(client)
    reply["body"] = {
        "object": "list",
        "data": [
            {"id": "opus-5.5", "object": "model", "created": 1, "owned_by": "ufl"},
            {"id": "gpt-6-luna", "object": "model", "created": 1, "owned_by": "ufl"},
        ],
    }
    result = client.post("/api/admin/api-health/models", headers=csrf).json()
    assert result["models"] == ["gpt-6-luna", "opus-5.5"]
    assert str(requests[0].url) == "https://ufl.test/proxy/models"
    reply["body"] = completion(usage=None)
    assert check(client, csrf).json()["usage"] == {}
    reply.update(status=404, body={"error": "private-ufl-test-key"})
    result = client.post("/api/admin/api-health/models", headers=csrf)
    assert result.json()["error"]["code"] == "not_found"
    assert "private-ufl-test-key" not in result.text


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://user:private-ufl-test-key@ufl.test",
        "https://ufl.test?key=private-ufl-test-key",
        "not-a-url",
    ],
)
def test_missing_or_unsafe_url_does_not_call_gateway(health_app, url):
    client, settings, requests, _, _ = health_app
    settings.ufl_base_url = url
    csrf = login(client)
    result = client.get("/api/admin/api-health")
    assert result.json()["configured"] is False and result.json()["base_url"] is None
    assert "private-ufl-test-key" not in result.text
    assert check(client, csrf).status_code == 503 and requests == []


def test_missing_key_and_invalid_input_do_not_call_gateway(health_app):
    client, settings, requests, _, _ = health_app
    csrf = login(client)
    for data in [
        {"model": ""},
        {"model": "invalid\nmodel"},
        {"timeout_seconds": 121},
        {"mode": "invalid"},
        {"api_key": "key"},
    ]:
        assert check(client, csrf, **data).status_code == 422
    settings.ufl_api_key = SecretStr("  ")
    assert client.get("/api/admin/api-health").json()["key_configured"] is False
    assert check(client, csrf).status_code == 503
    assert requests == []


def test_gateway_aliases_with_spaces_are_sent_exactly(health_app):
    client, _, requests, _, _ = health_app
    result = check(client, login(client), model="gemini 3.8 flash").json()
    assert result["status"] == "healthy"
    assert json.loads(requests[0].content)["model"] == "gemini 3.8 flash"


def test_untrusted_response_fields_cannot_echo_the_server_key(health_app):
    client, _, _, reply, _ = health_app
    reply["body"] = completion(
        "private-ufl-test-key",
        "private-ufl-test-key",
        model="private-ufl-test-key",
        usage={"prompt_tokens": "private-ufl-test-key", "completion_tokens": -1, "total_tokens": 2},
    )
    result = check(client, login(client))
    assert "private-ufl-test-key" not in result.text
    assert result.json()["response"] == "[redacted]"
    assert result.json()["finish_reason"] == "unknown"
    assert result.json()["usage"]["input_tokens"] is None


def test_agent_step_uses_the_same_gateway_with_server_credentials(health_app):
    client, _, requests, reply, _ = health_app
    csrf = login(client)
    reply["body"] = completion('{"arrow_id": 1}')
    response = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={
            "name": "UFL agent",
            "model": "opus-5",
            "start_level": 1,
            "end_level": 1,
        },
    )
    assert response.status_code == 201
    run = response.json()
    assert run["config"]["provider"] == "ufl"
    result = client.post(
        f"/api/admin/agents/{run['id']}/step", headers=csrf, json={"request_id": str(uuid4())}
    )
    assert result.status_code == 200 and result.json()["state"]["status"] == "won"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://ufl.test/proxy/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer private-ufl-test-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "opus-5"
    assert body["messages"][1]["content"][1]["type"] == "image_url"


@pytest.mark.parametrize("content", ["", None, "   "])
def test_empty_completed_agent_response_is_unscored(health_app, content):
    client, _, _, reply, _ = health_app
    csrf = login(client)
    reply["body"] = completion(content)
    run = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={
            "name": "UFL agent",
            "model": "gpt-6-luna",
            "start_level": 1,
            "end_level": 1,
        },
    ).json()
    result = client.post(
        f"/api/admin/agents/{run['id']}/step", headers=csrf, json={"request_id": str(uuid4())}
    ).json()
    assert result["state"]["lives_remaining"] == 3 and result["state"]["mistakes"] == 0
    assert result["state"]["moves"] == 0 and result["diagnostic"]["code"] == "empty_response"
    assert result["state"]["removed_ids"] == []


@pytest.mark.parametrize("failure", ["length", "missing_choices", "refusal", "content_filter"])
def test_incomplete_gateway_completions_never_apply_an_action(health_app, failure):
    client, _, _, reply, _ = health_app
    csrf = login(client)
    body = completion('{"arrow_id": 1}')
    if failure == "missing_choices":
        body["choices"] = []
    elif failure == "refusal":
        body["choices"][0]["message"]["refusal"] = "private-ufl-test-key"
    else:
        body["choices"][0]["finish_reason"] = failure
    reply["body"] = body
    run = client.post(
        "/api/admin/agents",
        headers=csrf,
        json={
            "name": "Gateway failure test",
            "model": "claude-opus-5.5",
            "start_level": 1,
            "end_level": 1,
        },
    ).json()
    result = client.post(
        f"/api/admin/agents/{run['id']}/step",
        headers=csrf,
        json={"request_id": str(uuid4())},
    ).json()
    assert result["state"]["revision"] == result["state"]["moves"] == 0
    assert result["state"]["lives_remaining"] == 3 and result["results"] == []
    assert result["state"]["status"] == "active"
    turn = client.get(f"/api/admin/agents/{run['id']}/turns").json()["items"][0]
    assert "private-ufl-test-key" not in json.dumps(turn)
    assert turn["after"] == turn["before"]
    expected_code = {
        "length": "output_limit",
        "missing_choices": "invalid_response",
        "refusal": "refusal",
        "content_filter": "refusal",
    }[failure]
    assert turn["diagnostic"]["code"] == expected_code
