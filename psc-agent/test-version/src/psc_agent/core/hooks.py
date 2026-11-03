"""Hooks: checkpoints around every run. A hook is any object with ``on_<event>(**ctx)`` methods.

Events and what they receive (always as keyword arguments; take ``**_`` for the rest):

    run_start     run (dict), runtime
    before_model  role, model, messages             raise HookBlock to stop the call
    after_model   role, model, completion
    model_error   role, model, error
    before_tool   name, args                         raise HookBlock to refuse the tool call
    after_tool    name, args, ok, result
    after_answer  kind, paper, rows                  rows are EvidenceRows; hooks may set quote_ok
    decision      kind, items, verdicts
    run_end       run, summary

Built in here: TraceHook (trace.jsonl), BudgetHook (stop at a cost), QuoteCheckHook (a quote must be on the
page it cites). Elsewhere: DataMapHook (analyze.datamap), ProgressHook and CancelHook (web.jobs).
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .evidence import quote_on_page


class HookBlock(RuntimeError):
    """Raised by a hook in before_model / before_tool to refuse the action."""


class Hooks:
    def __init__(self, hooks=()):
        self._hooks = list(hooks)

    def add(self, hook):
        self._hooks.append(hook)
        return hook

    def __iter__(self):
        return iter(self._hooks)

    def emit(self, event: str, **ctx) -> None:
        for h in self._hooks:
            fn = getattr(h, f"on_{event}", None)
            if fn is not None:
                fn(**ctx)


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


class TraceHook:
    """One JSON line per event: model calls (tokens, cost, tool calls), tool calls with results and
    errors, answers, decisions. Message bodies are summarized, never stored, so the trace stays small."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event,
               **{k: _jsonable(v) for k, v in fields.items()}}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def on_run_start(self, run, **_):
        self.write("run_start", run=run)

    def on_before_model(self, role, model, messages, **_):
        chars = sum(len(c.get("text", "")) if isinstance(c, dict) else 0
                    for m in messages for c in (m["content"] if isinstance(m.get("content"), list) else
                                                [{"text": m.get("content") or ""}]))
        images = sum(1 for m in messages if isinstance(m.get("content"), list)
                     for c in m["content"] if c.get("type") == "image_url")
        self.write("model_call", role=role, model=model, messages=len(messages), chars=chars, images=images)

    def on_after_model(self, role, completion, **_):
        u = completion.usage or {}
        self.write("model_result", role=role, model=completion.model, cached=completion.cached,
                   tool_calls=[tc.get("function", {}).get("name") for tc in completion.tool_calls],
                   prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"),
                   cost=u.get("cost"), content_chars=len(completion.content or ""))

    def on_model_error(self, role, model, error, **_):
        self.write("model_error", role=role, model=model, error=str(error)[:500])

    def on_after_tool(self, name, args, ok, result, **_):
        self.write("tool", name=name, args=args, ok=ok, result=str(result)[:600])

    def on_after_answer(self, kind, rows, paper=None, **_):
        self.write("answer", kind=kind, paper=getattr(paper, "name", paper), rows=len(rows),
                   quote_failed=sum(getattr(r, "quote_ok", None) is False for r in rows))

    def on_decision(self, kind, verdicts, **_):
        self.write("decision", kind=kind, counts=dict(Counter(v.choice for v in verdicts)),
                   flagged=[{"id": v.id, "choice": v.choice, "reason": v.reason[:200]}
                            for v in verdicts if v.choice != "accept"][:30])

    def on_run_end(self, run, summary, **_):
        self.write("run_end", run=run, summary=summary)


class BudgetHook:
    """Refuses further model calls once the spend (USD, cache hits excluded) reaches ``max_usd``."""

    def __init__(self, max_usd: float):
        self.max_usd = max_usd
        self.spent = 0.0
        self._lock = threading.Lock()

    def on_after_model(self, completion, **_):
        if not completion.cached:
            with self._lock:
                self.spent += (completion.usage or {}).get("cost") or 0.0

    def on_before_model(self, **_):
        if self.spent >= self.max_usd:
            raise HookBlock(f"budget ${self.max_usd:.2f} reached (spent ${self.spent:.4f})")


class QuoteCheckHook:
    """Checkpoint against invented evidence: sets ``row.quote_ok`` on every answer row."""

    def __init__(self, min_ratio: float = 0.8):
        self.min_ratio = min_ratio

    def on_after_answer(self, paper, rows, **_):
        if paper is None:
            return
        for r in rows:
            r.quote_ok = quote_on_page(paper.pages, getattr(r, "page", None), getattr(r, "evidence", None),
                                       getattr(r, "source", None), self.min_ratio)
