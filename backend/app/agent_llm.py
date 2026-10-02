"""UFL Chat Completions adapter. Every invocation includes the current PNG."""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from backend.app.ufl import (
    MODEL_PRESETS,
    complete,
    completion_text,
    completion_usage,
    image_content,
)

PROVIDERS = {
    "ufl": {
        "label": "UFL · all models",
        "model": MODEL_PRESETS[0]["id"],
        "models": MODEL_PRESETS,
        "env": "UFL_API_KEY",
    },
}

RULES = """You are playing Amaze Go, an arrow-clearing puzzle.
GAME RULES (apply on every turn):
1. Clear every arrow to win. Submit exactly one tap of a remaining arrow per request.
2. Each arrow is an orthogonal path. Its tail has a numeric ID label. Its head is
   the triangle. The final segment determines its fixed direction.
3. A tap tests the straight ray beyond the head, in that direction, all the way
   to the rectangular board boundary. Any other remaining arrow cell on that ray
   blocks the tap. Empty cells, grey silhouette holes, and the tapped arrow's own
   body do not block it. Holes do not end the ray. Bent bodies do not turn the ray.
4. A clear tap removes the entire arrow. A blocked tap leaves the board unchanged
   and costs exactly one life. Never move, rotate, or change an arrow yourself.
5. Only a blocked tap costs a life. Unknown/removed IDs, malformed JSON, missing
   answers, and API failures are retried without changing the board or lives.
   At zero lives the level attempt ends in a loss. Each new level resets lives.
6. No hints or solver are available. Infer a safe tap from the attached image.
7. Use feedback from the previous action. Choose from the remaining arrow IDs.
OUTPUT CONTRACT: Return only a JSON object, for example:
{"arrow_id": 12, "explanation": "Its forward lane appears clear."}
arrow_id must be a positive integer. explanation is optional, brief (max 2000
characters), and describes the visible reason for the choice. No other fields,
markdown, list of moves, or proposed next state. The server applies the action.
"""


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arrow_id: StrictInt = Field(gt=0)
    explanation: str = Field(default="", max_length=2000)


def parse_decision(raw, remaining_ids):
    raw = raw.strip()
    # Accept an otherwise exact fenced JSON response without guessing an ID from prose.
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        raw = match[1]
    try:
        decision = Decision.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        raise ValueError("Return one JSON object with a positive integer arrow_id.") from None
    if decision.arrow_id not in remaining_ids:
        raise ValueError(f"Arrow {decision.arrow_id} is not on the current board.")
    return decision.model_dump()


def feedback(session):
    if not session["history"]:
        return f"This is a fresh level attempt. You have {session['lives_remaining']} lives."
    action = session["history"][-1]
    remaining = session["lives_remaining"]
    if action["result"] == "blocked":
        return (
            f"Your last action tapped arrow {action['arrow_id']}. It was blocked by arrow "
            f"{action['blocked_by']}, so you lost one life. You have {remaining} lives "
            "remaining. The board did not change. Choose a different safe action."
        )
    if action["result"] == "invalid":
        return (
            f"Your last action was invalid: {action['message']} You lost one life. "
            f"You have {remaining} lives remaining. The board did not change."
        )
    return (
        f"Your last action tapped arrow {action['arrow_id']} and cleared it. "
        f"You lost no lives. You have {remaining} lives remaining."
    )


def build_prompt(session, state, mode, response_feedback=None):
    observation = {
        "level_number": state["level"]["number"],
        "rows": state["level"]["rows"],
        "columns": state["level"]["columns"],
        "revision": state["revision"],
        "lives_remaining": state["lives_remaining"],
        "max_lives": state["max_lives"],
        "remaining_arrow_ids": [a["id"] for a in state["arrows"]],
    }
    if mode == "image_and_state":
        observation.update(matrix=state["matrix"], arrows=state["arrows"])
    return (
        feedback(session)
        + (
            f"\nPrevious request failed: {response_feedback} "
            'Return exactly one JSON object, e.g. {"arrow_id": 12}. '
            "Keep the explanation brief."
            if response_feedback
            else ""
        )
        + "\nCurrent observation (zero-based row/column paths are "
        "tail first, head last when supplied):\n"
        + json.dumps(observation)
        + "\nThe attached PNG is the current state. Choose exactly one arrow to tap."
    )


def invoke_model(config, api_key, prompt, png, *, base_url):
    response = complete(
        base_url=base_url,
        api_key=api_key,
        model=config["model"],
        timeout_seconds=config["timeout_seconds"],
        max_output_tokens=config["max_output_tokens"],
        messages=[
            {"role": "system", "content": RULES},
            {"role": "user", "content": image_content(prompt, png)},
        ],
    )
    # Keep completion metadata so an exhausted reasoning budget is distinguishable
    # from a bad game decision. Never store provider reasoning or refusal text.
    choices = getattr(response, "choices", None)
    choice = choices[0] if isinstance(choices, list) and choices else None
    refused = bool(getattr(getattr(choice, "message", None), "refusal", None))
    raw = "" if refused else completion_text(response, allow_empty=True)
    raw = raw.replace(api_key, "[redacted]") if api_key else raw
    reason = getattr(choice, "finish_reason", None)
    return {
        "raw_response": raw[:64000],
        "response_truncated": len(raw) > 64000,
        "usage": completion_usage(response),
        "finish_reason": reason
        if reason in {"stop", "length", "content_filter", "tool_calls", "function_call"}
        else "unknown",
        "refused": refused,
    }
