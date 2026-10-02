"""One OpenAI-compatible UFL gateway for every model family."""

import base64
from typing import Annotated
from urllib.parse import urlsplit

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import StringConstraints

from backend.app.engine import GameError

ModelId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]

MODEL_PRESETS = [
    {"id": "gpt-6-luna", "label": "GPT-6 Luna"},
    {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
    {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
    {"id": "opus-5", "label": "Opus 5"},
    {"id": "claude-opus-5.5", "label": "Opus 5.5"},
    {"id": "fable-5.1", "label": "Fable 5.1"},
    {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash"},
]


class InvalidCompletionError(ValueError):
    """The gateway did not return a usable Chat Completions response."""


def gateway_key(settings):
    value = settings.ufl_api_key
    return value.get_secret_value().strip() if value else ""


def gateway_url(settings):
    value = (settings.ufl_base_url or "").strip()
    if not value:
        raise GameError("Set UFL_BASE_URL in the server environment.", 503)
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in {"https", "http"}
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
        )
        # Validate an optional port without including the supplied URL in an error.
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise GameError("UFL_BASE_URL must be an HTTP(S) URL without credentials or a query.", 503)
    # Keep the configured path. The sample gateway does not require an added /v1.
    return value.rstrip("/")


def image_content(prompt, png):
    return [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
        },
    ]


def complete(*, base_url, api_key, model, messages, timeout_seconds, max_output_tokens=None):
    options = {"model": model, "messages": messages}
    if max_output_tokens is not None:
        options["max_tokens"] = max_output_tokens
    with OpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
    ) as client:
        return client.chat.completions.create(**options)


def completion_text(response, *, allow_empty=False):
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise InvalidCompletionError("The response has no completion choices.")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if allow_empty and message is not None and content is None:
        content = ""
    if not isinstance(content, str) or (not allow_empty and not content.strip()):
        raise InvalidCompletionError("The response has no non-empty message content.")
    if getattr(message, "refusal", None):
        raise InvalidCompletionError("The model refused the test request.")
    return content


def completion_usage(response):
    usage = getattr(response, "usage", None)
    if not usage:
        return {}
    values = {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)
    if reasoning_tokens is not None:
        values["reasoning_tokens"] = reasoning_tokens
    return {
        name: value if type(value) is int and value >= 0 else None for name, value in values.items()
    }


def safe_error(error):
    """Return useful diagnostics without exposing upstream bodies or headers."""
    if isinstance(error, APITimeoutError):
        return {"code": "timeout", "message": "UFL did not respond before the timeout."}
    if isinstance(error, APIConnectionError):
        diagnostic = {
            "code": "connection_error",
            "message": "The UFL request failed at the HTTP transport layer. "
            "Retry this model or check gateway connectivity.",
        }
        # Only expose a known category, never the cause's message, body, or headers.
        transport_errors = {
            "ConnectError": "connect_error",
            "ReadError": "read_error",
            "WriteError": "write_error",
            "RemoteProtocolError": "remote_protocol_error",
            "LocalProtocolError": "local_protocol_error",
            "DecodingError": "decoding_error",
            "ProxyError": "proxy_error",
            "UnsupportedProtocol": "unsupported_protocol",
        }
        transport_error = transport_errors.get(type(error.__cause__).__name__)
        if transport_error:
            diagnostic["transport_error"] = transport_error
        return diagnostic
    if isinstance(error, APIStatusError):
        status = error.status_code
        code, message = {
            400: ("bad_request", "UFL rejected the request. Check the model ID and input support."),
            401: ("authentication_error", "UFL rejected the API key. Check UFL_API_KEY."),
            403: (
                "permission_denied",
                "UFL rejected this model ID for the API key. Discover model IDs to check "
                "the exact alias and available models, or confirm access with UFL.",
            ),
            404: ("not_found", "Model or endpoint not found. Check the model ID and UFL_BASE_URL."),
            429: ("rate_limited", "UFL rate limit or quota exceeded. Retry later or check quota."),
        }.get(status, ("upstream_error", "UFL returned an unsuccessful HTTP response."))
        return {"code": code, "message": message, "http_status": status}
    if isinstance(error, InvalidCompletionError):
        return {"code": "invalid_response", "message": str(error)}
    return {
        "code": "invalid_response",
        "message": "UFL did not return a usable OpenAI-compatible response.",
    }
