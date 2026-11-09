"""Runtime: the one place that turns (role, messages) into a model call.

Each role gets its own model (``models[role]``, else ``models["default"]``) and its own system prompt
(the skills listed for that role). Every call passes through the hooks (before_model / after_model /
model_error) and is added to the usage totals. Roles used by the package:

    recheck   re-extract hidden cells of existing rows        mapper    list a paper's devices (Big model)
    extract   fill rows for the devices of a new paper        judge     text equivalence
    decision  accept / verify_more / revise (small model)     planner   analysis plan (Big model)
    analyst   tool-using investigation                        reporter  insight report (Big model)
    learner   rules from the trace
"""
from __future__ import annotations

import json
import threading

from pydantic import BaseModel

from .hooks import Hooks
from .openrouter import Completion, OpenRouterClient, OpenRouterError
from .skills import SkillBook


class Usage(BaseModel):
    """Calls, tokens and USD of a run (cache hits are counted as calls but cost nothing)."""

    calls: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, c: Completion) -> None:
        self.calls += 1
        if c.cached:  # answered from the local cache: nothing billed now
            self.cached_calls += 1
            return
        u = c.usage
        self.prompt_tokens += u.get("prompt_tokens") or 0
        self.completion_tokens += u.get("completion_tokens") or 0
        self.cached_prompt_tokens += (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        self.cost_usd += u.get("cost") or 0.0

    def merge(self, other: "Usage") -> None:
        for name in type(self).model_fields:
            setattr(self, name, getattr(self, name) + getattr(other, name))


def parse_json(text: str):
    """JSON from a reply, tolerating code fences or prose around the object."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start: end + 1])


class Runtime:
    """Thread-safe; one instance can serve a whole batch.

    ``models``: a model id (used for every role) or ``{"default": id, "decision": small_id, ...}``.
    ``skills_dirs``: project skill folders added to the package defaults (the last one receives
    generated skills). ``hooks``: hook objects (see hooks.py).
    """

    def __init__(self, models, *, client: OpenRouterClient | None = None, api_key: str | None = None,
                 cache_dir=None, skills: SkillBook | None = None, skills_dirs=(), hooks=(),
                 max_tokens: int = 32000, extra: dict | None = None, native_pdf: bool = False):
        models = {"default": models} if isinstance(models, str) else dict(models)
        if not models.get("default"):
            raise ValueError("models needs a 'default' model id")
        self.models = models
        self.client = client or OpenRouterClient(api_key, cache_dir=cache_dir)
        self.skills = skills or SkillBook(*skills_dirs)
        self.hooks = hooks if isinstance(hooks, Hooks) else Hooks(hooks)
        self.max_tokens = max_tokens
        self.extra = extra
        self.native_pdf = native_pdf  # the model reads whole PDFs: papers go in as one file, not as page images
        self.usage = Usage()
        self._lock = threading.Lock()

    def model_for(self, role: str) -> str:
        return self.models.get(role) or self.models["default"]

    def system(self, role: str) -> str:
        return self.skills.prompt(role)

    def complete(self, role: str, messages: list[dict], *, schema: dict | None = None, tools: list[dict] | None = None,
                 tool_choice=None, max_tokens: int | None = None, usage: Usage | None = None) -> Completion:
        model = self.model_for(role)
        self.hooks.emit("before_model", role=role, model=model, messages=messages)
        try:
            comp = self.client.complete(model, messages, schema=schema, tools=tools, tool_choice=tool_choice,
                                        max_tokens=max_tokens or self.max_tokens, extra=self.extra)
        except OpenRouterError as e:
            self.hooks.emit("model_error", role=role, model=model, error=e)
            raise
        with self._lock:
            self.usage.add(comp)
        if usage is not None:
            usage.add(comp)
        self.hooks.emit("after_model", role=role, model=model, completion=comp)
        return comp

    def ask_json(self, role: str, content, answer_type, *, schema: dict, max_tokens: int | None = None,
                 usage: Usage | None = None):
        """System prompt of ``role`` + one user message -> a validated ``answer_type`` (pydantic).
        Invalid JSON is shown back to the model once."""
        messages = [{"role": "system", "content": self.system(role)}, {"role": "user", "content": content}]
        comp = self.complete(role, messages, schema=schema, max_tokens=max_tokens, usage=usage)
        try:
            return answer_type.model_validate(parse_json(comp.content))
        except ValueError as e:  # bad JSON or wrong shape
            messages += [{"role": "assistant", "content": comp.content},
                         {"role": "user", "content": f"That JSON is invalid: {str(e)[:1000]}\n"
                                                     "Return the complete corrected JSON only."}]
            comp = self.complete(role, messages, schema=schema, max_tokens=max_tokens, usage=usage)
            return answer_type.model_validate(parse_json(comp.content))
