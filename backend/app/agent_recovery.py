"""Bounded recovery for unscored provider and response failures."""

import math
import time
from datetime import UTC
from email.utils import parsedate_to_datetime

from backend.app.ufl import safe_error

MAX_CONSECUTIVE_FAILURES = 3
MAX_OUTPUT_TOKENS = 16384


def provider_diagnostic(error):
    diagnostic = safe_error(error)
    status = diagnostic.get("http_status")
    diagnostic["retryable"] = (
        diagnostic["code"] in {"timeout", "connection_error", "invalid_response"}
        or status in {408, 409, 429}
        or (isinstance(status, int) and status >= 500)
    )
    # Read only the standard delay, never serialize arbitrary headers or bodies.
    headers = getattr(getattr(error, "response", None), "headers", {})
    value = headers.get("retry-after")
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            delay = (
                date.replace(tzinfo=UTC).timestamp() - time.time()
                if not date.tzinfo
                else (date.timestamp() - time.time())
            )
        except (TypeError, ValueError, OverflowError):
            delay = 0
    if math.isfinite(delay) and delay > 0:
        diagnostic["retry_after_seconds"] = math.ceil(delay)
    return diagnostic


def response_diagnostic(turn, token_limit, validation_error=None):
    if turn.get("finish_reason") == "length" or (
        not (turn.get("raw_response") or "").strip()
        and (turn.get("usage", {}).get("output_tokens") or 0) >= token_limit
    ):
        return {
            "code": "output_limit",
            "message": "The response exhausted its output token budget before a complete action "
            "was available. The next request will use a larger budget, up to 16384 tokens.",
            "retryable": True,
        }
    if turn.get("refused") or turn.get("finish_reason") == "content_filter":
        return {
            "code": "refusal",
            "message": "The provider declined this request. Check model input support.",
            "retryable": False,
        }
    if turn.get("response_truncated"):
        return {
            "code": "response_too_large",
            "message": "The response exceeded the transcript limit. Return a brief JSON action.",
            "retryable": True,
        }
    if not (turn.get("raw_response") or "").strip():
        return {
            "code": "empty_response",
            "message": "The provider returned no visible action.",
            "retryable": True,
        }
    if validation_error:
        return {"code": "invalid_action", "message": validation_error, "retryable": True}
    return None


def retry_delay(failures, diagnostic):
    return max(
        5 * 2 ** (min(failures, MAX_CONSECUTIVE_FAILURES) - 1),
        diagnostic.get("retry_after_seconds", 0),
    )


def retry_message(diagnostic):
    return diagnostic["message"] + " No life was lost. The board and move count are unchanged."
