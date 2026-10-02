"""Paginated, self-contained JSON performance reports for settled evaluations."""

import json
from collections import Counter

from backend.app.agent_store import public_run
from backend.app.engine import now_iso, observation


def empty_metrics():
    return {
        "requests": 0,
        "cleared": 0,
        "blocked": 0,
        "invalid_responses": 0,
        "provider_errors": 0,
        "cancelled": 0,
        "charged_invalid_actions": 0,
        "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0},
        "token_usage_counts": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
        },
        "requests_with_usage": 0,
        "total_latency_ms": 0,
        "measured_requests": 0,
        "errors_by_code": {},
    }


def add_turn(metrics, turn):
    metrics["requests"] += 1
    outcome = turn.get("outcome") or {}
    result = outcome.get("result")
    if result in {"cleared", "blocked"}:
        metrics[result] += 1
    elif result == "invalid":
        metrics["invalid_responses"] += 1
        if outcome.get("reward", 0) < 0:
            metrics["charged_invalid_actions"] += 1
    elif turn["status"] == "error":
        metrics["provider_errors"] += 1
    elif turn["status"] == "cancelled":
        metrics["cancelled"] += 1
    usage = turn.get("usage") or {}
    if any(type(v) is int for v in usage.values()):
        metrics["requests_with_usage"] += 1
    for key in metrics["tokens"]:
        value = usage.get(key)
        if type(value) is int and value >= 0:
            metrics["tokens"][key] += value
            metrics["token_usage_counts"][key] += 1
    latency = turn.get("latency_ms")
    if type(latency) is int and latency >= 0:
        metrics["total_latency_ms"] += latency
        metrics["measured_requests"] += 1
    diagnostic = turn.get("diagnostic")
    if diagnostic:
        code = diagnostic["code"]
        metrics["errors_by_code"][code] = metrics["errors_by_code"].get(code, 0) + 1


def finish_metrics(metrics):
    for key, count in metrics["token_usage_counts"].items():
        if count == 0:
            metrics["tokens"][key] = None
    measured = metrics["measured_requests"]
    metrics["mean_latency_ms"] = (
        round(metrics["total_latency_ms"] / measured, 2) if measured else None
    )
    actions = metrics["cleared"] + metrics["blocked"]
    metrics["tap_success_rate"] = metrics["cleared"] / actions if actions else None
    return metrics


def performance_report(store, run):
    """Stream every transcript page; report memory does not grow with turn count."""

    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    yield '{"schema_version":1,"exported_at":' + encode(now_iso())
    yield ',"turn_order":"newest_first","run":' + encode(public_run(run))
    yield ',"turns":['
    summary = empty_metrics()
    per_level = {}
    session_ids = {run["current_session_id"]} | {r["session_id"] for r in run["results"]}
    before, first = None, True
    while True:
        page = store.list_agent_turns(run["id"], limit=100, before=before)
        for turn in page["items"]:
            session_ids.add(turn["session_id"])
            add_turn(summary, turn)
            add_turn(per_level.setdefault(turn["level_id"], empty_metrics()), turn)
            yield ("" if first else ",") + encode(turn)
            first = False
        before = page["next_before"]
        if before is None:
            break
    yield '],"sessions":['
    first = True
    statuses = Counter()
    level_ids = set()
    for session_id in sorted(session_ids):
        session = store.get_session(session_id, run["player_id"])
        state = observation(session)
        state.pop("legal_arrow_ids")
        level_ids.add(session["level_id"])
        statuses[session["status"]] += 1
        item = {
            "session_id": session_id,
            "level_id": session["level_id"],
            "level_hash": session["level_hash"],
            "state": state,
            "action_history": session["history"],
        }
        yield ("" if first else ",") + encode(item)
        first = False
    yield '],"levels":['
    for index, level_id in enumerate(sorted(level_ids)):
        level = store.get_level(level_id)
        yield ("," if index else "") + encode(
            {"level_hash": level.fingerprint, "board": level.model_dump(mode="json")}
        )
    summary.update(level_statuses=dict(statuses), levels_attempted=len(session_ids))
    ended = statuses["won"] + statuses["lost"]
    summary["level_win_rate"] = statuses["won"] / ended if ended else None
    yield '],"summary":' + encode(finish_metrics(summary))
    yield ',"per_level":' + encode({key: finish_metrics(value) for key, value in per_level.items()})
    yield (
        ',"notes":'
        + encode(
            [
                "Provider errors and invalid responses are separate from blocked puzzle taps.",
                "Historical life penalties are preserved; charged_invalid_actions identifies them.",
                "Token totals include only reported usage; failed requests may have unknown usage.",
                "Original boards and removed_ids reconstruct each turn image offline.",
            ]
        )
        + "}"
    )
