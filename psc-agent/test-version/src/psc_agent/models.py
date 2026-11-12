"""The one place that says which model answers which role. The CLI, the web app and the docs read it from here:
change a model in this file only.

Roles: ``default`` (extract / recheck rows, mapper, analyst), ``decision`` (accept / verify_more / revise), ``judge``
(recheck), ``si_reader`` (does the paper refer to an SI), ``si_finder`` (web-search agent: needs tool calling).
A role a provider does not list falls back to its ``default``, except ``si_finder``, which is left out.
"""
from __future__ import annotations

# OpenRouter id for the cheap fast reader; billed from the OpenRouter credit, with the cost in every call.
FAST = "google/gemini-3.6-flash"
CLAUDE_BIG = "anthropic/claude-sonnet-5.5"
CLAUDE_SMALL = "anthropic/claude-haiku-5.5"

MODELS: dict[str, dict[str, str]] = {
    "openrouter": {"default": FAST, "decision": FAST, "judge": FAST, "si_reader": FAST, "si_finder": CLAUDE_BIG},
    "claude-code": {"default": "claude-sonnet-5-5", "decision": "claude-haiku-5-5", "judge": "claude-haiku-5-5",
                    "si_reader": "claude-haiku-5-5", "si_finder": "claude-sonnet-5-5"},
    "gemini": {"default": "gemini-3.5-flash"},  # Google API directly: no tool calling, no price (cost shows 0)
    "ollama": {"default": "deepseek-v4-pro"},
}

ROLES = ("default", "decision", "judge", "si_finder", "si_reader")

# What the model boxes of the web page suggest (OpenRouter ids).
SUGGESTED = [FAST, "google/gemini-3.5-flash", CLAUDE_BIG, "anthropic/claude-opus-5.5", CLAUDE_SMALL, "openai/gpt-5.6-luna"]

PROVIDERS = tuple(MODELS)
DEFAULT_MODEL = {p: m["default"] for p, m in MODELS.items()}


def role_models(provider: str, roles=ROLES) -> dict[str, str]:
    """role -> model for ``provider``; an unlisted role takes the provider's default (``si_finder`` only when listed,
    unless the provider is used by the web app, which asks for every role: pass ``roles``)."""
    m = MODELS[provider]
    return {r: m.get(r, m["default"]) for r in roles}


def run_model(provider: str) -> str:
    """The model of the extract / recheck agents of ``psc-agent run`` (the small reader)."""
    return MODELS[provider].get("decision", MODELS[provider]["default"])


def finder_model(provider: str) -> str | None:
    """The model of the SI web-search agent, None where the provider has none."""
    return MODELS[provider].get("si_finder")
