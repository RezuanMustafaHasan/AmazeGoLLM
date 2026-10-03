"""Admin-triggered checks of actual UFL Chat Completions responses."""

import time
from io import BytesIO
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter
from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, model_validator

from backend.app.agent_recovery import MAX_OUTPUT_TOKENS
from backend.app.engine import GameError, now_iso
from backend.app.model_thinking import (
    THINKING_MODELS,
    ThinkingEffort,
    thinking_parameters,
    validate_thinking,
)
from backend.app.ufl import (
    MODEL_PRESETS,
    ModelId,
    complete,
    completion_text,
    completion_usage,
    gateway_key,
    gateway_url,
    image_content,
    safe_error,
)


class HealthCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelId
    mode: Literal["text", "image"] = "text"
    timeout_seconds: StrictInt = Field(default=30, ge=5, le=120)
    max_output_tokens: StrictInt | None = Field(default=None, ge=256, le=MAX_OUTPUT_TOKENS)
    thinking_effort: ThinkingEffort | None = None

    @model_validator(mode="after")
    def thinking_settings(self):
        validate_thinking(self.model, self.thinking_effort)
        return self


def connection(settings):
    url = gateway_url(settings)
    key = gateway_key(settings)
    if not key:
        raise GameError("Set UFL_API_KEY in the server environment.", 503)
    return url, key


def api_health_router(settings, admin_dependency):
    router = APIRouter(
        prefix="/api-health", tags=["admin API health"], dependencies=[admin_dependency]
    )

    @router.get("")
    def configuration():
        error = None
        try:
            base_url = gateway_url(settings)
        except GameError as exc:
            base_url = None
            error = exc.message
        key_configured = bool(gateway_key(settings))
        if not key_configured:
            error = error or "Set UFL_API_KEY in the server environment."
        return {
            "configured": key_configured and base_url is not None,
            "key_configured": key_configured,
            "base_url_configured": base_url is not None,
            "base_url": base_url,
            "configuration_error": error,
            "models": MODEL_PRESETS,
            "thinking_models": THINKING_MODELS,
        }

    @router.post("/models")
    def discover_models():
        url, key = connection(settings)
        try:
            with OpenAI(api_key=key, base_url=url, timeout=20, max_retries=0) as client:
                models = client.models.list()
            ids = set()
            for item in models.data:
                if not isinstance(item.id, str) or key in item.id:
                    continue
                try:
                    ids.add(TypeAdapter(ModelId).validate_python(item.id))
                except ValueError:
                    continue
            ids = sorted(ids)
            return {"status": "ok", "models": ids, "error": None}
        except Exception as exc:
            return {"status": "error", "models": [], "error": safe_error(exc)}

    @router.post("/check")
    def check(body: HealthCheck):
        url, key = connection(settings)
        marker = str(uuid4())
        expected = "UFL_OK"
        content = f"API health check {marker}. Reply with exactly UFL_OK."
        if body.mode == "image":
            buffer = BytesIO()
            Image.new("RGB", (64, 64), "blue").save(buffer, format="PNG")
            expected = "blue"
            content = image_content(
                f"Image health check {marker}. What color is the attached square? "
                "Reply with only the color name.",
                buffer.getvalue(),
            )
        result = {
            "model": body.model,
            "mode": body.mode,
            "thinking_effort": body.thinking_effort,
            "thinking_parameters": thinking_parameters(body.model, body.thinking_effort),
            "max_output_tokens": body.max_output_tokens,
            "status": "error",
            "checked_at": now_iso(),
            "latency_ms": None,
            "response": None,
            "response_truncated": False,
            "response_model": None,
            "finish_reason": None,
            "usage": {},
            "expected_response": expected,
            "error": None,
        }
        started = time.monotonic()
        try:
            # Use the same adapter and thinking controls as puzzle evaluations.
            response = complete(
                base_url=url,
                api_key=key,
                model=body.model,
                messages=[{"role": "user", "content": content}],
                timeout_seconds=body.timeout_seconds,
                max_output_tokens=body.max_output_tokens,
                thinking_effort=body.thinking_effort,
            )
            text = completion_text(response)
            # Never return a key even if a gateway accidentally echoes it in text.
            text = text.replace(key, "[redacted]")
            finish_reason = response.choices[0].finish_reason
            if finish_reason not in {
                "stop",
                "length",
                "tool_calls",
                "function_call",
                "content_filter",
            }:
                finish_reason = "unknown"
            matched = text.strip().strip("` .\n").casefold() == expected.casefold()
            result.update(
                status="healthy" if matched and finish_reason == "stop" else "warning",
                response=text[:2000],
                response_truncated=len(text) > 2000,
                response_model=str(response.model).replace(key, "[redacted]")[:120],
                finish_reason=finish_reason,
                usage=completion_usage(response),
            )
            if result["status"] == "warning":
                result["error"] = {
                    "code": "unexpected_response",
                    "message": "The API returned text, but did not complete the expected reply. "
                    "Review the response and finish reason.",
                }
        except Exception as exc:
            result["error"] = safe_error(exc)
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result

    return router
