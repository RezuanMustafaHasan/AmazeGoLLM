"""Verified model-specific thinking choices and their UFL request parameters.

Keep this catalog on the server: both admin screens consume it, and request
validation uses the same choices. Unknown aliases retain gateway defaults.
"""

import re
from typing import Literal

ThinkingEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


def profile(family, label, efforts, description):
    return {"family": family, "label": label, "efforts": efforts, "description": description}


_GPT = profile(
    "openai",
    "Reasoning effort",
    ["low", "medium", "high", "xhigh", "max"],
    "Choose how much reasoning the model applies to each decision.",
)
_LUNA = {**_GPT, "efforts": ["none", *_GPT["efforts"]]}
_CLAUDE = profile(
    "anthropic",
    "Thinking effort",
    ["low", "medium", "high", "xhigh", "max"],
    "Claude uses adaptive thinking. Effort controls how deeply it reasons.",
)
_GEMINI = profile(
    "gemini",
    "Thinking level",
    ["low", "medium", "high"],
    "Gemini 3.8 Flash supports low, medium, and high thinking levels.",
)

# Include UFL presets and the equivalent native model names. Do not infer
# support for an entire family: e.g. Astra cannot use Luna's 'none' option.
THINKING_MODELS = {
    "gpt-6-luna": _LUNA,
    "gpt-6-sol": _LUNA,
    "gpt-6.1-sol": _GPT,
    "gpt-6-astra": _GPT,
    "opus-5": _CLAUDE,
    "claude-opus-5": _CLAUDE,
    "opus-5.5": _CLAUDE,
    "opus-5-5": _CLAUDE,
    "claude-opus-5.5": _CLAUDE,
    "claude-opus-5-5": _CLAUDE,
    "fable-5.1": _CLAUDE,
    "fable-5-1": _CLAUDE,
    "claude-fable-5.1": _CLAUDE,
    "claude-fable-5-1": _CLAUDE,
    "gemini-3.8-flash": _GEMINI,
}


def model_thinking(model):
    # Recognize provider-qualified IDs and dated snapshots of verified models.
    name = model.strip().lower().rsplit("/", 1)[-1]
    name = re.sub(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$", "", name)
    return THINKING_MODELS.get(name)


def validate_thinking(model, effort):
    if effort is None:
        return
    settings = model_thinking(model)
    if settings is None:
        raise ValueError(
            "Thinking controls are unavailable for this model ID. Use gateway default."
        )
    if effort not in settings["efforts"]:
        allowed = ", ".join(settings["efforts"])
        raise ValueError(f"This model supports these thinking choices: {allowed}.")


def thinking_parameters(model, effort):
    """Return wire parameters, omitting every override for gateway default."""
    validate_thinking(model, effort)
    if effort is None:
        return {}
    if model_thinking(model)["family"] == "anthropic":
        # Native controls avoid converting 'max' into a fixed thinking budget.
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    # OpenAI and Gemini's compatible API use this field. UFL/LiteLLM maps
    # Gemini effort to the corresponding native thinking level.
    return {"reasoning_effort": effort}
