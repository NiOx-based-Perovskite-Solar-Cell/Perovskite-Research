---
name: trace-to-skill
description: Mine the work trace (every prompt, tool call and failure, captured by hooks) for what actually worked, then update the project-cli skill — verified commands and gotchas — and promote repeated ad-hoc scripts into tested CLI subcommands. Use at the end of a work session, or when asked which commands work, what failed, or to turn the trace into skills.
---

# Trace → skill (Big Agent)

Hooks append to `.claude/trace/trace.jsonl` (JSON Lines, safe for parallel hooks): one record per prompt, tool call and
failure, with exit code, duration, the command or file path (never file contents), and output clipped head + tail.
A `map` record notes each REPO_MAP rebuild caused by a file being added, moved or removed (`changes: ["+new", "-old"]`).
Secrets are redacted before writing. The file is git-ignored local history. Older sessions: Claude Code transcripts in
`~/.claude/projects/<project>/<session>.jsonl`.

1. Digest:
   `python .claude/skills/trace-to-skill/tracelog.py digest` — or `... digest --transcript <session.jsonl>`; `--json` for data.
2. **Commands**: candidates are rows with ok > 0. `masked` > 0 means the shell's exit status was not the main command's
   (pipe into tail/grep, `; echo`, `|| true`), so that "ok" proves nothing.
3. Re-run each candidate now, unmasked (no filter pipe, or `set -o pipefail`). Note the exit code and the decisive
   output line. Only then add it to `../project-cli/SKILL.md` → *Verified commands*, with today's date. Commands that
   rewrite tracked files may be listed from the trace instead, marked "not re-run".
4. **Failures and the command that fixed them** → one line each under *Gotchas* in project-cli (symptom → fix).
5. **Ad-hoc scripts** → a feature seen 3+ times (same data source, library or intent) is a promotion candidate. Add a
   subcommand to `../project-cli/datactl.py` (or a script beside it) with a test in `.claude/tests/`, run
   `python -m unittest discover -s .claude/tests`, then document it. From then on call the CLI instead of a heredoc.
6. Delete entries that now fail. Never list a command you have not seen exit 0, and never describe a data structure from
   memory: run datactl.
