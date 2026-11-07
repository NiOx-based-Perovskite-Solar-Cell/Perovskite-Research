"""Trace → skill: what the agent's own runs teach the next run.

``digest`` reads trace.jsonl files written by TraceHook: model calls and errors per role, tool calls that
worked and those that failed (with the error the model saw), quote-check failures, decisions and their
reasons. ``learn`` turns that into the generated skill ``learned-rules``: verified tool calls to reuse,
errors not to repeat, the decision model's recurring complaints, and, when a runtime is given, rules
condensed by the Big model (role "learner"). The next run gets them in its system prompt.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

NAME = "learned-rules"
ROLES = "[planner, analyst, recheck, mapper, extract]"


def load(paths) -> list[dict]:
    records = []
    for p in [paths] if isinstance(paths, (str, Path)) else paths:
        for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def _call_key(rec: dict) -> str:
    return f"{rec.get('name')}({json.dumps(rec.get('args'), ensure_ascii=False, sort_keys=True)})"


def digest(records: list[dict]) -> dict:
    models = defaultdict(lambda: {"calls": 0, "cached": 0, "cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0})
    model_errors: Counter = Counter()
    ok_calls: dict[str, dict] = {}
    bad_calls: dict[str, dict] = {}
    flagged: Counter = Counter()
    decisions: Counter = Counter()
    quote_failed = Counter()
    runs = Counter()
    for r in records:
        ev = r.get("event")
        if ev == "model_result":
            m = models[f"{r.get('role')} · {r.get('model')}"]
            m["calls"] += 1
            m["cached"] += bool(r.get("cached"))
            if not r.get("cached"):
                m["cost"] += r.get("cost") or 0.0
                m["prompt_tokens"] += r.get("prompt_tokens") or 0
                m["completion_tokens"] += r.get("completion_tokens") or 0
        elif ev == "model_error":
            model_errors[f"{r.get('role')} · {r.get('model')}: {str(r.get('error'))[:160]}"] += 1
        elif ev == "tool":
            key = _call_key(r)
            target = ok_calls if r.get("ok") else bad_calls
            entry = target.setdefault(key, {"call": key, "count": 0, "result": str(r.get("result"))[:200]})
            entry["count"] += 1
        elif ev == "decision":
            decisions.update(r.get("counts") or {})
            for f in r.get("flagged") or []:
                flagged[f"{f.get('choice')}: {str(f.get('reason'))[:160]}"] += 1
        elif ev == "answer":
            quote_failed[r.get("kind")] += r.get("quote_failed") or 0
        elif ev == "run_start":
            runs[(r.get("run") or {}).get("kind", "?")] += 1
    return {
        "runs": dict(runs),
        "models": {k: {**v, "cost": round(v["cost"], 4)} for k, v in models.items()},
        "model_errors": model_errors.most_common(20),
        "tools_ok": sorted(ok_calls.values(), key=lambda e: -e["count"])[:25],
        "tools_failed": sorted(bad_calls.values(), key=lambda e: -e["count"])[:25],
        "decisions": dict(decisions),
        "decision_feedback": flagged.most_common(15),
        "quote_failures": {k: v for k, v in quote_failed.items() if v},
    }


def render(d: dict) -> str:
    lines = ["# Trace digest", "", f"runs: {d['runs']} | decisions: {d['decisions']} | quote failures: "
             f"{d['quote_failures']}", "", "## Models (role · model)"]
    lines += [f"- {k}: {v['calls']} calls ({v['cached']} cached), ${v['cost']}, {v['prompt_tokens']} in / "
              f"{v['completion_tokens']} out tokens" for k, v in d["models"].items()]
    lines += ["", "## Model errors"] + [f"- {n}x {e}" for e, n in d["model_errors"]]
    lines += ["", "## Tool calls that worked"] + [f"- {e['count']}x `{e['call']}`" for e in d["tools_ok"]]
    lines += ["", "## Tool calls that failed"] + [f"- {e['count']}x `{e['call']}` -> {e['result']}"
                                                  for e in d["tools_failed"]]
    lines += ["", "## Decision feedback"] + [f"- {n}x {e}" for e, n in d["decision_feedback"]]
    return "\n".join(lines) + "\n"


def learn(records: list[dict], skills_dir, runtime=None, usage=None) -> Path:
    """Write ``<skills_dir>/learned-rules/SKILL.md`` from the trace (and the learner model, if given)."""
    d = digest(records)
    lines = ["---", f"name: {NAME}", "description: Lessons from earlier runs (generated from the trace)",
             f"roles: {ROLES}", "order: 90", "---",
             "These come from the agent's own trace. Prefer the verified calls, never repeat the failed ones.", ""]
    if d["tools_ok"]:
        lines += ["## Verified tool calls (worked before; reuse instead of guessing names)"]
        lines += [f"- `{e['call']}`" for e in d["tools_ok"][:20]] + [""]
    if d["tools_failed"]:
        lines += ["## Failed tool calls (do not repeat; the error says what is valid)"]
        lines += [f"- `{e['call']}` -> {e['result']}" for e in d["tools_failed"][:20]] + [""]
    if d["decision_feedback"]:
        lines += ["## What the decision model kept asking for"]
        lines += [f"- {e}" for e, _ in d["decision_feedback"][:10]] + [""]
    if d["model_errors"]:
        lines += ["## Model errors seen"] + [f"- {e}" for e, _ in d["model_errors"][:10]] + [""]
    if runtime is not None:
        comp = runtime.complete("learner", [{"role": "system", "content": runtime.system("learner")},
                                            {"role": "user", "content": render(d)}], usage=usage)
        lines += ["## Rules (written by the learner model from the digest; review them)", comp.content.strip(), ""]
    target = Path(skills_dir) / NAME / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return target
