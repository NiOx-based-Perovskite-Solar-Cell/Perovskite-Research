"""Project hooks, one Python process per event (wired in .claude/settings.json).

    prompt    UserPromptSubmit    trace the prompt
    post      PostToolUse         trace the call; after Bash/PowerShell/Write, rebuild REPO_MAP.md if files
                                  were added, moved or deleted, and tell Claude what changed
    fail      PostToolUseFailure  trace the failure (exit code, error tail)
    subagent  SubagentStart       give the subagent the repo compass (map layout + pointer)
    stop      Stop                rebuild REPO_MAP.md if code changed; PLAN.md evidence gate

Hooks fail open: an internal error is appended to .claude/trace/hook_errors.log and never blocks work.
Stdlib only, so it runs under `python -I`.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
SKILLS = Path(__file__).resolve().parents[1] / "skills"
for _d in ("trace-to-skill", "repo-map", "plan-evidence"):
    sys.path.insert(0, str(SKILLS / _d))

import build_map  # noqa: E402
import plan  # noqa: E402
import tracelog  # noqa: E402

STRUCTURE_TOOLS = {"Bash", "PowerShell", "Write"}  # the tools that can add, move or delete files
NO_COMPASS = {"ac-judge"}  # subagents that judge evidence and never search code


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


TRACE = ROOT / ".claude" / "trace" / "trace.jsonl"


def on_prompt(p: dict):
    tracelog.append(tracelog.from_hook(p), TRACE)


def on_post(p: dict):
    tracelog.append(tracelog.from_hook(p), TRACE)
    if p.get("tool_name") not in STRUCTURE_TOOLS:
        return None
    kind, changes = build_map.refresh(ROOT, structure_only=True)
    if kind != "structure":
        return None
    tracelog.append(tracelog.note("map", p, tool=p.get("tool_name"), changes=changes), TRACE)
    shown = ", ".join(changes[:12]) + (f" (+{len(changes) - 12} more)" if len(changes) > 12 else "")
    return _context("PostToolUse", f"Files were added/moved/removed ({shown or 'first build'}); "
                                   f"{build_map.SKILL}/REPO_MAP.md was rebuilt. Use the new paths from the map.")


def on_fail(p: dict):
    tracelog.append(tracelog.from_hook(p), TRACE)


def on_subagent(p: dict):
    if p.get("agent_type") in NO_COMPASS:
        return None
    build_map.refresh(ROOT, structure_only=True)
    text = build_map.compass(ROOT)
    return _context("SubagentStart", text) if text else None


def on_stop(p: dict):
    build_map.refresh(ROOT)
    return plan.gate(p, ROOT)


HANDLERS = {"prompt": on_prompt, "post": on_post, "fail": on_fail, "subagent": on_subagent, "stop": on_stop}


def main(event: str) -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        out = HANDLERS[event](payload)
        if out:
            sys.stdout.write(json.dumps(out))  # ASCII-escaped: safe on any console code page
    except Exception:  # fail open: log, never block the session
        try:
            log = ROOT / ".claude" / "trace" / "hook_errors.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"--- {datetime.now().isoformat(timespec='seconds')} {event}\n{traceback.format_exc()}")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
