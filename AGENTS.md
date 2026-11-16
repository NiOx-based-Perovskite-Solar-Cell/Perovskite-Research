# Agent Instructions

Before searching code, notebooks, or data across the repository:

1. Read `.claude/skills/repo-map/REPO_MAP.md` (**Layout** first, then the section for your area).
2. Open target files directly at the listed `L<line>` (for notebooks, use `c<index>` / `#<cell id>` with `python .claude/skills/project-cli/datactl.py nb FILE --cell <index|id>`).
3. Search (`grep` / `rg`) only inside files the map points to; do a repo-wide search only when the map has no lead.
4. Inspect data folders (`Raw Data/`, `processed/`, `original paper/`) with `python .claude/skills/project-cli/datactl.py` instead of guessing schemas or filenames.
5. After adding, moving, deleting, or editing mapped files, refresh the map: `python .claude/skills/repo-map/build_map.py`.
