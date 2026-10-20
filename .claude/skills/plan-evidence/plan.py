"""PLAN.md: acceptance criteria bound to real evidence, judged before the work is closed.

Format and workflow: see SKILL.md next to this file. State of each AC, decided in this order:

    verify_more  no evidence, a missing evidence file, or a screenshot without before + after
    revise       the newest `test` evidence (output of `plan.py run`) exited non-zero
    <verdict>    the recorded verdict, if its sig still matches the AC text and evidence contents
    needs_judge  evidence complete but no current verdict -> ask the ac-judge subagent

    python .claude/skills/plan-evidence/plan.py new "title" [--goal TEXT] [--ac TEXT [--check CMD]]...
    python .claude/skills/plan-evidence/plan.py status [--json]
    python .claude/skills/plan-evidence/plan.py run AC-1 [--cmd CMD]       # run check:, save output as evidence
    python .claude/skills/plan-evidence/plan.py diff AC-1 [PATH...]        # git diff as evidence
    python .claude/skills/plan-evidence/plan.py attach AC-1 file PATH... | screenshot BEFORE AFTER
    python .claude/skills/plan-evidence/plan.py verdict AC-1 accept|verify_more|revise --reason TEXT [--p 0.9] [--by NAME]
    python .claude/skills/plan-evidence/plan.py close                      # all accepted -> done, archived
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[3])
VERDICTS = ("accept", "verify_more", "revise")
KINDS = ("test", "diff", "file", "screenshot")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TOOL = "python .claude/skills/plan-evidence/plan.py"

_AC = re.compile(r"^###\s+(AC-\d+)\s*:\s*(.+?)\s*$")
_CHECK = re.compile(r"^\s*(?:-\s*)?check:\s*(.+?)\s*$")
_EVIDENCE = re.compile(r"^\s*(?:-\s*)?evidence:\s*(\w+)\s+(.+?)\s*$")
_VERDICT = re.compile(r"^\s*(?:-\s*)?verdict:\s*(\w+)(.*)$")
_SHOT = re.compile(r"^before=(.+?)\s+after=(.+)$")
_EXIT = re.compile(r"^# exit: (-?\d+)\s*$", re.M)
_TREE = re.compile(r"^# tree: (\w+)\s*$", re.M)
# bookkeeping that changes while judging; everything else in the work tree counts as "the code"
TREE_EXCLUDE = (":(exclude)PLAN.md", ":(exclude).claude/plan", ":(exclude).claude/skills/repo-map/REPO_MAP.md")


@dataclass
class AC:
    id: str
    title: str
    start: int  # index of the ### line in Plan.lines
    end: int  # index after the block's last line
    check: str | None = None
    evidence: list[tuple[str, str]] = field(default_factory=list)
    verdict: dict | None = None

    def files(self) -> list[tuple[str, str]]:
        """(kind, path) for every evidence file; a screenshot gives two."""
        out = []
        for kind, value in self.evidence:
            if kind == "screenshot":
                m = _SHOT.match(value)
                out += [(kind, m.group(1).strip("`")), (kind, m.group(2).strip("`"))] if m else []
            else:
                out.append((kind, value.strip("`")))
        return out


@dataclass
class Plan:
    path: Path
    title: str
    status: str
    lines: list[str]
    acs: list[AC]

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:40] or "plan"


def plan_path(root: Path = ROOT) -> Path:
    return root / "PLAN.md"


def load(root: Path = ROOT) -> Plan | None:
    p = plan_path(root)
    if not p.exists():
        return None
    lines = p.read_text(encoding="utf-8").splitlines()
    title = next((m.group(1).strip() for l in lines if (m := re.match(r"^#\s+PLAN:\s*(.+)$", l))), "untitled")
    status = next((m.group(1) for l in lines if (m := re.match(r"^status:\s*(\w+)", l))), "active")
    acs: list[AC] = []
    for i, line in enumerate(lines):
        open_block = bool(acs) and acs[-1].end == len(lines)
        if re.match(r"^#{1,3}\s", line) and open_block:  # any heading ends the current AC block
            acs[-1].end = i
            open_block = False
        if (m := _AC.match(line)):
            acs.append(AC(m.group(1), m.group(2), i, len(lines)))
        elif open_block:
            ac = acs[-1]
            if (m := _CHECK.match(line)):
                ac.check = m.group(1).strip("`")
            elif (m := _EVIDENCE.match(line)) and m.group(1) in KINDS:
                ac.evidence.append((m.group(1), m.group(2)))
            elif (m := _VERDICT.match(line)) and m.group(1) in VERDICTS:
                meta, _, reason = m.group(2).partition("|")
                v = dict(kv.split("=", 1) for kv in meta.split() if "=" in kv)
                ac.verdict = {"verdict": m.group(1), "reason": reason.strip(), "line": i, **v}
    return Plan(p, title, status, lines, acs)


def save(plan: Plan) -> None:
    plan.path.write_text("\n".join(plan.lines) + "\n", encoding="utf-8", newline="\n")


def signature(ac: AC, root: Path = ROOT) -> str:
    """What a verdict is bound to: AC text, check, evidence lines and evidence file contents."""
    h = hashlib.sha1("\n".join([ac.id, ac.title, ac.check or "", *map(" ".join, ac.evidence)]).encode())
    for _, rel in ac.files():
        f = root / rel
        h.update(rel.encode() + b"\0" + (f.read_bytes() if f.is_file() else b"<missing>"))
    return h.hexdigest()[:8]


def recorded_exit(path: Path) -> int | None:
    m = _EXIT.search(path.read_text(encoding="utf-8", errors="replace")[:4000])
    return int(m.group(1)) if m else None


def recorded_tree(path: Path) -> str | None:
    m = _TREE.search(path.read_text(encoding="utf-8", errors="replace")[:4000])
    return m.group(1) if m else None


def tree_hash(root: Path = ROOT) -> str | None:
    """Fingerprint of the working tree (uncommitted diff + untracked files), None outside git."""
    if not _git(root, "rev-parse", "--git-dir").strip():
        return None
    h = hashlib.sha1(_git(root, "rev-parse", "HEAD").encode())
    h.update(_git(root, "diff", "HEAD", "--binary", "--", ".", *TREE_EXCLUDE).encode())
    for rel in _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".", *TREE_EXCLUDE).split("\0"):
        if rel and (root / rel).is_file():
            h.update(rel.encode() + b"\0" + (root / rel).read_bytes())
    return h.hexdigest()[:12]


def blocker(ac: AC, root: Path = ROOT, tree: str | None = None) -> tuple[str, str] | None:
    """Deterministic reasons an AC cannot be accepted, whatever a judge says. ``tree`` is the
    current tree_hash(); evidence captured on a different tree is stale."""
    if not ac.evidence:
        hint = f"{TOOL} run {ac.id}" if ac.check else f"add a check: line, then {TOOL} run {ac.id} (or diff/attach)"
        return "verify_more", f"no evidence attached -> {hint}"
    if any(k == "screenshot" and not _SHOT.match(v) for k, v in ac.evidence):
        return "verify_more", "screenshot evidence needs before=<file> after=<file>"
    missing = [rel for _, rel in ac.files() if not (root / rel).is_file()]
    if missing:
        return "verify_more", f"evidence file missing: {', '.join(missing)}"
    runs = [rel for kind, rel in ac.files() if kind == "test"]
    if runs:
        newest = max(runs, key=lambda r: (root / r).stat().st_mtime)
        code = recorded_exit(root / newest)
        if code is None:
            return "verify_more", f"{newest} has no '# exit:' header; capture it with {TOOL} run {ac.id}"
        if code != 0:
            return "revise", f"newest check exited {code} ({newest}) -> fix it, then {TOOL} run {ac.id}"
    for kind, verb in (("test", "run"), ("diff", "diff")):
        caps = [rel for k, rel in ac.files() if k == kind]
        if caps and tree:
            newest = max(caps, key=lambda r: (root / r).stat().st_mtime)
            if recorded_tree(root / newest) not in (None, tree):
                return "verify_more", f"code changed after {newest} was captured -> {TOOL} {verb} {ac.id} again"
    return None


def evaluate(plan: Plan, root: Path = ROOT) -> list[dict]:
    rows = []
    tree = tree_hash(root)
    for ac in plan.acs:
        state = blocker(ac, root, tree)
        v = ac.verdict
        sig = signature(ac, root)
        if state is None:
            if v and v.get("sig") == sig:
                state = (v["verdict"], f"{v.get('by', '?')}: {v['reason']}")
            else:
                state = ("needs_judge", "evidence complete, " + ("verdict is stale (evidence changed)" if v else
                                                                  "no verdict yet") + " -> ask the ac-judge subagent")
        rows.append({"id": ac.id, "title": ac.title, "state": state[0], "reason": state[1], "check": ac.check,
                     "evidence": [{"kind": k, "path": p} for k, p in ac.files()], "sig": sig,
                     "verdict": {k: x for k, x in v.items() if k != "line"} if v else None})
    return rows


def _insert_line(plan: Plan, ac: AC, line: str) -> None:
    """Add a line at the end of the AC's machine lines (after check/evidence), before free text."""
    at = ac.start + 1
    for i in range(ac.start + 1, ac.end):
        if _CHECK.match(plan.lines[i]) or _EVIDENCE.match(plan.lines[i]):
            at = i + 1
    plan.lines.insert(at, line)
    ac.end += 1


def _get(plan: Plan, ac_id: str) -> AC:
    for ac in plan.acs:
        if ac.id.lower() == ac_id.lower():
            return ac
    raise SystemExit(f"{ac_id} not found in {plan.path.name} (have: {', '.join(a.id for a in plan.acs) or 'none'})")


def _evidence_dir(plan: Plan, root: Path) -> Path:
    d = root / ".claude" / "plan" / "evidence" / plan.slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _shell() -> list[str] | None:
    """Git Bash on Windows (never WSL's System32 bash); bash/sh elsewhere; None = platform default shell."""
    if os.name == "nt":
        found = shutil.which("bash")
        for cand in [found, r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]:
            if cand and "system32" not in cand.lower() and Path(cand).exists():
                return [cand, "-c"]
        return None
    return [shutil.which("bash") or "/bin/sh", "-c"]


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60, check=False
                              ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- commands


def cmd_new(root: Path, title: str, goal: str | None, acs: list[str], checks: list[str], force: bool) -> int:
    old = load(root)
    if old and old.status != "done" and not force:
        print(f"PLAN.md already active ('{old.title}'); close it first or pass --force")
        return 1
    lines = [f"# PLAN: {title}", "status: active", f"created: {datetime.now():%Y-%m-%d}", "", "## Goal", "",
             goal or "<what done looks like, in one paragraph>", "", "## Acceptance criteria", "",
             "<!-- One `### AC-n:` per observable criterion. Evidence lines are written by plan.py "
             "(run / diff / attach); verdict lines by plan.py verdict. Do not type them by hand. -->", ""]
    for i, text in enumerate(acs or ["<observable criterion>"], 1):
        lines.append(f"### AC-{i}: {text}")
        if i <= len(checks) and checks[i - 1]:
            lines.append(f"check: {checks[i - 1]}")
        lines.append("")
    lines += ["## Notes", ""]
    plan_path(root).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {plan_path(root).name} with {len(acs or [1])} AC(s)")
    return 0


def cmd_status(root: Path, as_json: bool) -> int:
    plan = load(root)
    if plan is None:
        print("no PLAN.md")
        return 0
    rows = evaluate(plan, root)
    if as_json:
        print(json.dumps({"title": plan.title, "status": plan.status, "acs": rows}, ensure_ascii=False, indent=1))
        return 0
    ok = sum(r["state"] == "accept" for r in rows)
    print(f"PLAN: {plan.title} [{plan.status}] {ok}/{len(rows)} accepted")
    for r in rows:
        print(f"  {r['id']:6} {r['state']:12} {r['title'][:60]}\n         {r['reason'][:160]}")
    return 0


def cmd_run(root: Path, ac_id: str, command: str | None, timeout: int) -> int:
    plan = load(root) or _no_plan()
    ac = _get(plan, ac_id)
    command = command or ac.check
    if not command:
        raise SystemExit(f"{ac.id} has no check: line; add one or pass --cmd")
    out = _evidence_dir(plan, root) / f"{ac.id}-{_stamp()}.txt"
    shell = _shell()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(shell + [command] if shell else command, shell=shell is None, cwd=root,
                              capture_output=True, timeout=timeout)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code, stdout, stderr = 124, e.stdout or b"", (e.stderr or b"") + f"\n[timeout after {timeout}s]".encode()
    head = _git(root, "rev-parse", "--short", "HEAD").strip()
    dirty = " (dirty)" if _git(root, "status", "--porcelain").strip() else ""
    text = "\n".join([f"# plan.py run {ac.id}", f"# command: {command}", f"# cwd: {root}",
                      f"# started: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                      f"# duration_s: {time.monotonic() - t0:.1f}", f"# git: {head}{dirty}",
                      f"# tree: {tree_hash(root) or 'none'}", f"# exit: {code}",
                      "----- stdout -----", stdout.decode("utf-8", "replace"),
                      "----- stderr -----", stderr.decode("utf-8", "replace")])
    out.write_text(text, encoding="utf-8", newline="\n")
    _insert_line(plan, ac, f"evidence: test {_rel(out, root)}")
    save(plan)
    print(f"{ac.id}: exit {code} -> {_rel(out, root)}")
    return code


def cmd_diff(root: Path, ac_id: str, paths: list[str]) -> int:
    plan = load(root) or _no_plan()
    ac = _get(plan, ac_id)
    diff = _git(root, "diff", "HEAD", "--", *paths)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", *paths).split()
    if not diff.strip() and not untracked:
        print("nothing changed for these paths; no evidence written")
        return 1
    out = _evidence_dir(plan, root) / f"{ac.id}-{_stamp()}.diff"
    note = ("\n# untracked (new) files; attach them with `plan.py attach AC file PATH`:\n"
            + "\n".join(f"#   {u}" for u in untracked)) if untracked else ""
    out.write_text(f"# git diff HEAD -- {' '.join(paths) or '.'}\n# tree: {tree_hash(root) or 'none'}\n{diff}{note}\n",
                   encoding="utf-8", newline="\n")
    _insert_line(plan, ac, f"evidence: diff {_rel(out, root)}")
    save(plan)
    print(f"{ac.id}: diff -> {_rel(out, root)} ({diff.count(chr(10))} lines, {len(untracked)} untracked)")
    return 0


def cmd_attach(root: Path, ac_id: str, kind: str, files: list[str]) -> int:
    plan = load(root) or _no_plan()
    ac = _get(plan, ac_id)
    paths = [Path(f) if Path(f).is_absolute() else root / f for f in files]
    for p in paths:
        if not p.is_file():
            raise SystemExit(f"not a file: {p}")
    if kind == "screenshot" and (len(paths) != 2 or any(p.suffix.lower() not in IMAGE_EXT for p in paths)):
        raise SystemExit("screenshot needs exactly two images: BEFORE AFTER")
    rels = []
    for p in paths:  # files outside the repo (e.g. a temp screenshot) are copied in so the evidence survives
        try:
            rels.append(_rel(p, root))
        except ValueError:
            dest = _evidence_dir(plan, root) / f"{ac.id}-{_stamp()}-{p.name}"
            shutil.copy2(p, dest)
            rels.append(_rel(dest, root))
    if kind == "screenshot":
        _insert_line(plan, ac, f"evidence: screenshot before={rels[0]} after={rels[1]}")
    else:
        for r in rels:
            _insert_line(plan, ac, f"evidence: file {r}")
    save(plan)
    print(f"{ac.id}: attached {kind}: {', '.join(rels)}")
    return 0


def cmd_verdict(root: Path, ac_id: str, verdict: str, reason: str, p: float, by: str) -> int:
    plan = load(root) or _no_plan()
    ac = _get(plan, ac_id)
    block = blocker(ac, root, tree_hash(root))
    if verdict == "accept" and block:
        print(f"refused: {ac.id} cannot be accepted: {block[1]}")
        return 2
    line = (f"verdict: {verdict} p={p:.2f} by={by} sig={signature(ac, root)} "
            f"at={datetime.now(timezone.utc).isoformat(timespec='seconds')} | {' '.join(reason.split())}")
    if ac.verdict:
        plan.lines[ac.verdict["line"]] = line
    else:
        _insert_line(plan, ac, line)
    save(plan)
    print(f"{ac.id}: {verdict} (p={p:.2f})")
    return 0


def cmd_close(root: Path) -> int:
    plan = load(root) or _no_plan()
    rows = evaluate(plan, root)
    open_ = [r for r in rows if r["state"] != "accept"]
    if not rows or open_:
        print("cannot close:" + ("" if rows else " no acceptance criteria"))
        for r in open_:
            print(f"  {r['id']} {r['state']}: {r['reason']}")
        return 1
    plan.lines = [re.sub(r"^status:\s*\w+", "status: done", l) for l in plan.lines]
    plan.lines.insert(next(i for i, l in enumerate(plan.lines) if l.startswith("status:")) + 1,
                      f"closed: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    save(plan)
    archive = root / ".claude" / "plan" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    dest = archive / f"{datetime.now():%Y%m%d}-{plan.slug}.md"
    shutil.move(str(plan.path), dest)
    print(f"closed: {len(rows)}/{len(rows)} accepted -> {_rel(dest, root)}")
    return 0


def _no_plan():
    raise SystemExit(f"no PLAN.md; start one with: {TOOL} new \"title\"")


# ---------------------------------------------------------------- Stop gate


def gate(payload: dict, root: Path = ROOT) -> dict | None:
    """Stop-hook decision. Blocks a stop once per plan state, so a turn that must wait for the user
    can still end (the second stop with nothing changed passes, with a status line for the user)."""
    plan = load(root)
    if plan is None or plan.status == "done":
        return None
    rows = evaluate(plan, root)
    todo = [r for r in rows if r["state"] != "accept"]
    summary = f"PLAN '{plan.title}': {len(rows) - len(todo)}/{len(rows)} accepted"
    digest = hashlib.sha1(json.dumps([(r["id"], r["state"], r["sig"]) for r in rows]).encode()).hexdigest()
    state_file = root / ".claude" / "plan" / ".gate_state.json"
    try:
        seen = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    session = str(payload.get("session_id", ""))
    if seen.get(session) == digest:
        tail = ", ".join(f"{r['id']} {r['state']}" for r in todo) or f"all accepted: run {TOOL} close"
        return {"systemMessage": f"{summary} ({tail})"}
    seen.pop(session, None)
    seen[session] = digest
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(dict(list(seen.items())[-20:])), encoding="utf-8")
    if not rows:
        reason = f"PLAN.md '{plan.title}' has no acceptance criteria: add `### AC-n:` sections."
    elif not todo:
        reason = f"{summary}. Close it now: {TOOL} close"
    else:
        lines = [f"PLAN.md gate: {summary}; not ready to close."]
        lines += [f"- {r['id']} [{r['state']}] {r['title'][:70]}: {r['reason']}" for r in todo]
        lines.append("Act on these now. If you are waiting on the user instead, say so and stop: "
                     "this gate stays quiet until the plan changes.")
        reason = "\n".join(lines)
    return {"decision": "block", "reason": reason}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Thai text on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="PLAN.md with evidence-bound acceptance criteria")
    sub = ap.add_subparsers(dest="action", required=True)
    n = sub.add_parser("new")
    n.add_argument("title")
    n.add_argument("--goal")
    n.add_argument("--ac", action="append", default=[])
    n.add_argument("--check", action="append", default=[], help="check command for the AC at the same position")
    n.add_argument("--force", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("ac")
    r.add_argument("--cmd", dest="command", help="run this instead of the AC's check: line")
    r.add_argument("--timeout", type=int, default=1800)
    d = sub.add_parser("diff")
    d.add_argument("ac")
    d.add_argument("paths", nargs="*")
    a = sub.add_parser("attach")
    a.add_argument("ac")
    a.add_argument("kind", choices=["file", "screenshot"])
    a.add_argument("files", nargs="+")
    v = sub.add_parser("verdict")
    v.add_argument("ac")
    v.add_argument("verdict", choices=VERDICTS)
    v.add_argument("--reason", required=True)
    v.add_argument("--p", type=float, default=1.0, help="judge confidence 0..1")
    v.add_argument("--by", default="ac-judge")
    sub.add_parser("close")
    args = ap.parse_args(argv)
    if args.action == "new":
        return cmd_new(ROOT, args.title, args.goal, args.ac, args.check, args.force)
    if args.action == "status":
        return cmd_status(ROOT, args.json)
    if args.action == "run":
        return cmd_run(ROOT, args.ac, args.command, args.timeout)
    if args.action == "diff":
        return cmd_diff(ROOT, args.ac, args.paths)
    if args.action == "attach":
        return cmd_attach(ROOT, args.ac, args.kind, args.files)
    if args.action == "verdict":
        return cmd_verdict(ROOT, args.ac, args.verdict, args.reason, args.p, args.by)
    return cmd_close(ROOT)


if __name__ == "__main__":
    sys.exit(main())
