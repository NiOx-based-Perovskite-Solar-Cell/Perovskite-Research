"""Work trace: written by the hooks, digested for the Big Agent.

.claude/trace/trace.jsonl holds one JSON object per line (JSON Lines, so parallel hooks can append):

    {"ts", "session", "agent", "event": "prompt" | "tool", "tool", "ok", "exit", "ms", "input", "out"}

``ok`` is the tool's own verdict: for Bash/PowerShell a non-zero exit arrives as a failure with
``exit`` parsed from "Exit code N". Secrets are redacted before anything is written.

    python .claude/skills/trace-to-skill/tracelog.py digest                  # hook trace
    python .claude/skills/trace-to-skill/tracelog.py digest --transcript FILE # Claude Code session log
    python .claude/skills/trace-to-skill/tracelog.py digest --json            # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TRACE = ROOT / ".claude" / "trace" / "trace.jsonl"
SHELLS = {"Bash", "PowerShell"}
MAX_OUT = 600
MAX_COMMAND = 4000
ROTATE_BYTES = 20_000_000

_SECRETS = [
    (re.compile(r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))(\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\3"),
     r"\1\2[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{15,}"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
]
_EXIT = re.compile(r"^Exit code (-?\d+)")


def redact(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clip(text, limit: int) -> str | None:
    """Redacted text; when too long keep the head and the tail (where results and errors are)."""
    if text is None:
        return None
    text = redact(_ANSI.sub("", str(text)))
    if len(text) <= limit:
        return text
    head = limit // 3
    return f"{text[:head]} ...[{len(text) - limit} chars]... {text[len(text) - (limit - head):]}"


def parse_exit(error: str | None) -> int | None:
    m = _EXIT.match(error or "")
    return int(m.group(1)) if m else None


def summarize_input(tool: str, inp: dict) -> dict:
    """What was asked, without file contents."""
    if tool in SHELLS:
        keep = {"command": clip(inp.get("command"), MAX_COMMAND), "description": clip(inp.get("description"), 160)}
    elif tool == "Write":
        keep = {"file_path": inp.get("file_path"), "chars": len(inp.get("content") or "")}
    elif tool == "Agent":
        keep = {"subagent_type": inp.get("subagent_type"), "description": clip(inp.get("description"), 160)}
    else:
        keep = {k: (v if isinstance(v, (int, float, bool)) else clip(v, 300)) for k, v in inp.items()
                if k not in {"content", "new_string", "old_string", "new_source", "prompt", "edits"}
                and isinstance(v, (str, int, float, bool))}
    return {k: v for k, v in keep.items() if v not in (None, "")}


def _shell_output(resp) -> str | None:
    if isinstance(resp, dict):
        out = str(resp.get("stdout") or "")
        if resp.get("stderr"):
            out += "\n[stderr] " + str(resp["stderr"])
        return out
    return None if resp is None else str(resp)


def record(*, ts=None, session=None, agent=None, tool=None, ok=True, exit=None, ms=None, inp=None, out=None,
           prompt=None) -> dict:
    rec = {"ts": ts or datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "session": session,
           "agent": agent or "main"}
    if prompt is not None:
        rec.update(event="prompt", text=clip(prompt, 300))
    else:
        rec.update(event="tool", tool=tool, ok=ok, exit=exit, ms=ms, input=summarize_input(tool, inp or {}),
                   out=clip(out, MAX_OUT))
    return rec


def note(event: str, payload: dict, **fields) -> dict:
    """A record for something a hook did itself (e.g. event="map" when it rebuilt REPO_MAP.md)."""
    return {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "session": payload.get("session_id"),
            "agent": payload.get("agent_type") or "main", "event": event, **fields}


def from_hook(payload: dict) -> dict | None:
    """Trace record for a UserPromptSubmit, PostToolUse or PostToolUseFailure hook payload."""
    ev = payload.get("hook_event_name")
    common = dict(session=payload.get("session_id"), agent=payload.get("agent_type"))
    if ev == "UserPromptSubmit":
        text = payload.get("prompt") or ""
        # system-injected turns (task notifications etc.) start with a tag; they are not user work
        return None if text.lstrip().startswith("<") else record(**common, prompt=text)
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if ev == "PostToolUse":
        out = _shell_output(payload.get("tool_response")) if tool in SHELLS else None
        return record(**common, tool=tool, ok=True, exit=0 if tool in SHELLS else None,
                      ms=payload.get("duration_ms"), inp=inp, out=out)
    if ev == "PostToolUseFailure":
        err = payload.get("error") or ""
        return record(**common, tool=tool, ok=False, exit=parse_exit(err), ms=payload.get("duration_ms"), inp=inp,
                      out=err)
    return None


@contextmanager
def _locked(lock_path: Path, wait_s: float = 2.0):
    """Cross-process lock; after ``wait_s`` it proceeds anyway (a trace line is not worth a hang)."""
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    locked = False
    try:
        deadline = time.monotonic() + wait_s
        while not locked and time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                time.sleep(0.01)
        yield
    finally:
        if locked and os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)  # also releases flock


def append(rec: dict | None, path: Path = TRACE) -> None:
    if rec is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with _locked(path.with_suffix(".lock")):
        if path.exists() and path.stat().st_size > ROTATE_BYTES:
            path.replace(path.with_name(f"trace-{datetime.now():%Y%m%d-%H%M%S}.jsonl"))
        with open(path, "ab") as fh:
            fh.write(line)


# ---------------------------------------------------------------- reading


def load_trace(path: Path = TRACE) -> list[dict]:
    out = []
    for p in sorted(path.parent.glob("trace*.jsonl")) if path == TRACE else [path]:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return sorted(out, key=lambda r: r.get("ts") or "")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def load_transcript(path) -> list[dict]:
    """Records from a Claude Code session transcript (tool_use paired with its tool_result)."""
    uses: dict[str, tuple] = {}
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            agent = "subagent" if rec.get("isSidechain") else "main"
            if rec.get("type") == "user" and isinstance(content, str) and not content.lstrip().startswith("<"):
                out.append(record(ts=rec.get("timestamp"), session=rec.get("sessionId"), agent=agent, prompt=content))
                continue
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    uses[b.get("id")] = (b.get("name"), b.get("input") or {}, rec.get("timestamp"), rec.get("sessionId"),
                                         agent)
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                    name, inp, ts, session, who = uses.pop(b["tool_use_id"])
                    failed = bool(b.get("is_error"))
                    text = _text_of(b.get("content"))
                    tur = rec.get("toolUseResult")
                    if not failed and name in SHELLS and isinstance(tur, dict):
                        text = _shell_output(tur) or text
                    out.append(record(ts=ts, session=session, agent=who, tool=name, ok=not failed,
                                      exit=parse_exit(text) if failed else (0 if name in SHELLS else None),
                                      inp=inp, out=text if (failed or name in SHELLS) else None))
    return out


# ---------------------------------------------------------------- digest

_CD = re.compile(r"""^\s*cd\s+("[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*""")
_MASKING_PIPE = re.compile(r"\|\s*(?:tail|head|grep|sort|awk|sed|cut|wc|tee|uniq|Select-Object|Select-String)\b")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PY = {"python", "python3", "py", "python.exe"}


def normalize(command: str) -> str:
    """Drop ``cd <dir> &&`` prefixes and machine-specific paths so equal commands group together."""
    c = command.strip()
    while (m := _CD.match(c)):
        c = c[m.end():]
    root = str(ROOT)
    for variant in {root, root.replace("\\", "/"), "/" + root[0].lower() + root[2:].replace("\\", "/")}:
        c = c.replace(variant, ".")
    c = re.sub(r"[A-Za-z]:[/\\]Users[/\\][^/\\]+[/\\]AppData[/\\]Local[/\\]Temp[/\\]claude[/\\][^\s\"']*?scratchpad",
               "<scratch>", c)
    return c


def is_adhoc(command: str) -> bool:
    first = command.splitlines()[0] if command else ""
    return "<<" in first and bool(re.search(r"\b(python3?|py|node)\b", first))


def signature(command: str) -> str:
    """Group key: program plus script/module/subcommand of the first command in the chain."""
    first = command.splitlines()[0] if command else ""
    if is_adhoc(command):
        return "python <heredoc>"
    toks = [""]
    for seg in re.split(r"\s*(?:&&|\|\||;|\|)\s*", first):  # skip "cd dir" and pure "VAR=value" segments
        try:
            parts = shlex.split(seg)
        except ValueError:
            parts = seg.split()
        parts = [t for t in parts if not _ENV.match(t)]
        if parts and parts[0] != "cd":
            toks = parts
            break
    prog = os.path.basename(toks[0]).lower()
    rest = toks[1:]
    if prog in _PY:
        if "-m" in rest:
            i = rest.index("-m")
            parts = rest[i + 1: i + 3]
            return "python -m " + " ".join(p for p in parts if not p.startswith("-") and "/" not in p)
        if "-c" in rest:
            return "python -c"
        args = [t for t in rest if not t.startswith("-")]
        sub = [args[1]] if len(args) > 1 and re.fullmatch(r"[a-z][a-z_-]*", args[1]) else []
        return " ".join(["python", *args[:1], *sub]).strip()
    if prog in {"git", "npm", "npx", "pip", "uv", "gh"} and rest:
        return f"{prog} {rest[0]}"
    return prog


def masked_exit(command: str) -> bool:
    """True when the shell's exit status may not be the main command's: a pipe into a filter, or a
    later command in a ``;`` / ``||`` / newline chain (``cmd > log; echo done`` exits 0 even if cmd failed)."""
    lines = command.strip().splitlines() or [""]
    if is_adhoc(command):  # heredoc body is not shell; look at the opener line and what follows the terminator
        m = re.search(r"<<-?\s*['\"]?(\w+)", lines[0])
        end = next((i for i, l in enumerate(lines[1:], 1) if m and l.strip() == m.group(1)), len(lines))
        shell, tail = lines[0], [l for l in lines[end + 1:] if l.strip()]
    else:
        shell, tail = command.strip(), []
    if _MASKING_PIPE.search(shell) and "pipefail" not in shell:
        return True
    return bool(tail) or bool(re.search(r";\s*\S|\|\||\n", shell.rstrip(";").strip()))


_ERROR_LINE = re.compile(r"Error\b|Exception\b|error:|Traceback|No such file|not found|denied|cannot|failed",
                         re.I)


def error_line(output: str | None) -> str:
    """The line that names the failure: last error-looking line (end of a traceback), else the last line."""
    lines = [l.strip() for l in (output or "").splitlines() if l.strip() and not _EXIT.match(l.strip())]
    return next((l for l in reversed(lines) if _ERROR_LINE.search(l)), lines[-1] if lines else "")[:200]


_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", re.M)
_DATA = re.compile(r"(Raw Data|original paper|processed/[\w./-]+|reports/[\w./-]+|notebooks/[\w./-]+)")


def digest(records: list[dict]) -> dict:
    tools = [r for r in records if r.get("event") == "tool"]
    shell = [r for r in tools if r.get("tool") in SHELLS and (r.get("input") or {}).get("command")]
    groups: dict[str, dict] = defaultdict(lambda: {"ok": 0, "fail": 0, "masked": 0, "examples": [], "errors": [],
                                                   "last_ok": None})
    fixes = []
    pending_fail: dict[str, dict] = {}
    adhoc = []
    for r in shell:
        cmd = normalize(r["input"]["command"])
        sig = signature(cmd)
        g = groups[sig]
        if is_adhoc(cmd):
            adhoc.append((r, cmd))
        if r.get("ok"):
            g["ok"] += 1
            g["masked"] += masked_exit(cmd)
            g["last_ok"] = r.get("ts")
            if not is_adhoc(cmd) and cmd not in g["examples"]:
                g["examples"].append(cmd)
            if sig in pending_fail:
                fixes.append({"signature": sig, "failed": pending_fail.pop(sig), "fixed_by": cmd[:400]})
        else:
            g["fail"] += 1
            err = error_line(r.get("out"))
            g["errors"].append(err)
            pending_fail[sig] = {"command": cmd[:400], "error": err, "exit": r.get("exit"), "ts": r.get("ts")}

    features: dict[str, list[str]] = defaultdict(list)
    for r, cmd in adhoc:
        desc = (r.get("input") or {}).get("description") or cmd.splitlines()[0][:80]
        for lib in sorted(set(_IMPORT.findall(cmd)) & {"openpyxl", "fitz", "pandas", "json", "glob", "csv", "nbformat",
                                                       "psc_recheck", "pipeline", "httpx", "re"}):
            features[f"import {lib}"].append(desc)
        for data in sorted(set(m.split("/")[0] if m.startswith(("processed", "reports", "notebooks")) else m
                               for m in _DATA.findall(cmd))):
            features[f"data {data}"].append(desc)

    edited = Counter(r["input"].get("file_path") or r["input"].get("notebook_path")
                     for r in tools if r.get("tool") in {"Edit", "Write", "NotebookEdit"} and r.get("ok"))
    read = Counter(r["input"].get("file_path") for r in tools if r.get("tool") == "Read" and r.get("ok"))
    by_tool = Counter((r.get("tool"), "ok" if r.get("ok") else "fail") for r in tools)
    ordered = sorted(groups.items(), key=lambda kv: -(kv[1]["ok"] + kv[1]["fail"]))
    return {
        "span": [records[0].get("ts"), records[-1].get("ts")] if records else [],
        "sessions": len({r.get("session") for r in records}),
        "prompts": sum(r.get("event") == "prompt" for r in records),
        "tool_calls": {f"{t}:{s}": n for (t, s), n in sorted(by_tool.items(), key=lambda x: -x[1])},
        "commands": [{"signature": s, **{k: v for k, v in g.items() if k != "examples"},
                      "errors": g["errors"][-3:], "example": min(g["examples"], key=len) if g["examples"] else None}
                     for s, g in ordered],
        "fixes": fixes[-25:],
        "adhoc": {"count": len(adhoc),
                  "features": {k: {"count": len(v), "examples": list(dict.fromkeys(v))[:8]}
                               for k, v in sorted(features.items(), key=lambda kv: -len(kv[1]))}},
        "most_edited": edited.most_common(15),
        "most_read": read.most_common(15),
    }


def render(d: dict) -> str:
    lines = [f"# Trace digest\n", f"span {d['span']} | sessions {d['sessions']} | prompts {d['prompts']}",
             "tool calls: " + ", ".join(f"{k} {v}" for k, v in d["tool_calls"].items()), "",
             "## Commands (grouped; ok = exit 0; masked = exit hidden by a pipe to tail/grep/...)", "",
             "| signature | ok | fail | masked | example |", "|---|---|---|---|---|"]
    for c in d["commands"]:
        ex = (c["example"] or "").replace("|", "\\|").replace("\n", " ⏎ ")[:160]
        lines.append(f"| `{c['signature']}` | {c['ok']} | {c['fail']} | {c['masked']} | `{ex}` |")
    lines += ["", "## Failures and the command that fixed them", ""]
    for f in d["fixes"]:
        lines.append(f"- `{f['signature']}`: {f['failed']['error']}\n  - fixed by: `{f['fixed_by'][:200]}`")
    lines += ["", f"## Ad-hoc scripts ({d['adhoc']['count']} heredoc runs): promotion candidates", ""]
    for k, v in d["adhoc"]["features"].items():
        lines.append(f"- **{k}** x{v['count']}: " + "; ".join(v["examples"]))
    lines += ["", "## Most edited", ""] + [f"- {n} x {p}" for p, n in d["most_edited"]]
    lines += ["", "## Most read", ""] + [f"- {n} x {p}" for p, n in d["most_read"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Digest the work trace into what actually ran.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest")
    d.add_argument("--trace", type=Path, default=TRACE)
    d.add_argument("--transcript", type=Path, nargs="*", help="Claude Code session .jsonl file(s) instead")
    d.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    recs = [r for t in args.transcript for r in load_transcript(t)] if args.transcript else load_trace(args.trace)
    result = digest(sorted(recs, key=lambda r: r.get("ts") or ""))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
