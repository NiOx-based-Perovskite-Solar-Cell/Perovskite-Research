"""Tool-using loop: the model calls tested tools instead of guessing, until it gives a final answer.

Tool results are numbered [T1], [T2], ... so the final answer can cite exactly which results back each
number. Unknown tools, malformed arguments and ToolErrors (e.g. an unknown column, with the valid names)
go back to the model as results, so it can correct itself. before_tool / after_tool hooks fire on every call.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from .hooks import HookBlock

MAX_RESULT_CHARS = 12000


class ToolError(Exception):
    """An expected, explainable failure (bad name or argument). Shown to the model, not raised."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema of the keyword arguments
    fn: Callable[..., object]

    def spec(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class ToolCall:
    id: str  # T1, T2, ...
    name: str
    args: dict | None
    ok: bool
    result: str


@dataclass
class LoopResult:
    final: str
    calls: list[ToolCall] = field(default_factory=list)
    steps: int = 0


def run_tools(rt, role: str, messages: list[dict], tools: list[Tool], *, max_steps: int = 12, usage=None,
              first_id: int = 1) -> LoopResult:
    """Call the model with ``tools`` until it answers without a tool call (or ``max_steps`` is reached,
    when it is told to answer from what it has)."""
    msgs = list(messages)
    specs = [t.spec() for t in tools]
    by_name = {t.name: t for t in tools}
    calls: list[ToolCall] = []
    n = first_id
    for step in range(1, max_steps + 1):
        comp = rt.complete(role, msgs, tools=specs, usage=usage)
        if not comp.tool_calls:
            return LoopResult(comp.content, calls, step)
        msgs.append(comp.message)
        for tc in comp.tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
                args = args if isinstance(args, dict) else None
            except ValueError:
                args = None
            call_id = f"T{n}"
            n += 1
            ok, result = False, ""
            try:
                rt.hooks.emit("before_tool", name=name, args=args)
                if name not in by_name:
                    result = f"error: unknown tool {name!r}; available: {', '.join(by_name)}"
                elif args is None:
                    result = "error: arguments must be a JSON object"
                else:
                    out = by_name[name].fn(**args)
                    result, ok = json.dumps(out, ensure_ascii=False, default=str), True
            except HookBlock as e:
                result = f"error: refused by a hook: {e}"
            except ToolError as e:
                result = f"error: {e}"
            except TypeError as e:  # wrong or missing argument names
                result = f"error: bad arguments for {name}: {e}"
            except Exception as e:  # a value the tool could not handle; recorded in the trace via after_tool
                result = f"error: {type(e).__name__}: {e}"
            if len(result) > MAX_RESULT_CHARS:
                result = result[:MAX_RESULT_CHARS] + f" ...[truncated {len(result) - MAX_RESULT_CHARS} chars]"
            calls.append(ToolCall(call_id, name, args, ok, result))
            rt.hooks.emit("after_tool", name=name, args=args, ok=ok, result=result)
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", call_id), "content": f"[{call_id}] {result}"})
    msgs.append({"role": "user", "content": "Step limit reached. Give your final answer now from the results you have."})
    comp = rt.complete(role, msgs, tools=specs, tool_choice="none", usage=usage)
    return LoopResult(comp.content, calls, max_steps)
