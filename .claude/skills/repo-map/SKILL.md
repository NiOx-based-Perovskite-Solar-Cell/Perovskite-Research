---
name: repo-map
description: Compass of this repository. Read REPO_MAP.md before searching code, notebooks or data — it lists every Python module (symbols with line numbers, imports, used-by), every notebook (headings with cell index/id, data files it names) and the data folders. Use for any "where is / what uses / which file computes" question.
---

# Repo map

1. Read [REPO_MAP.md](REPO_MAP.md): **Layout** first, then the section for your area.
2. Jump to the file and line it names (Read with `offset` = the `L<line>`). Notebook cells are `c<index>` / `#<cell id>`;
   view one with `python .claude/skills/project-cli/datactl.py nb FILE --cell <index|id>`.
3. Grep only inside the files the map points to. Search the whole repo only when the map has no lead, and say so.
4. Data folders are summarized, not mapped: inspect them with `datactl.py` (project-cli skill), never by guessing names.

Small agents: the `repo-scout` subagent does lookups this way, and every subagent gets the map's Layout at start
(SubagentStart hook).

## How it stays fresh (hooks, no action needed)
- A Bash / PowerShell / Write call that adds, moves or deletes a file → map rebuilt at once, and Claude is told which
  paths changed (`+new`, `-old`).
- End of every turn (Stop hook) → rebuilt if any mapped file's content changed.
- By hand: `python .claude/skills/repo-map/build_map.py` (`--force` rebuild, `--check` exits 1 when stale).

## Big Agent duties
- REPO_MAP.md is generated (stdlib `ast` + notebook JSON + `git ls-files`): never edit it.
- Give every folder a purpose line in [notes.md](notes.md) (`- path/: purpose`). The map's **Unannotated** section lists
  folders still missing one. After moving or renaming a folder, update its key in notes.md.
