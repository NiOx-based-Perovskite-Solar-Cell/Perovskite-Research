---
name: project-cli
description: Verified commands for this repo (tests, DOI pairing, map, trace, reports) and datactl — the tested CLI for looking up CSV columns and values, Raw Data workbook sheets/headers/units, and notebook cells with outputs. Use before running project commands or touching data or notebooks; never guess column names, sheet layouts or cell contents.
---

# Project CLI

Run from the repo root in Git Bash. Add `PYTHONIOENCODING=utf-8` when output contains Thai.

## datactl: look, don't guess
| need | command |
|---|---|
| CSV columns: type, fill rate, distinct, examples | `python .claude/skills/project-cli/datactl.py csv processed/prepared_v2/target_table.csv` |
| values of one column (exit 1 + similar names if absent) | `python .claude/skills/project-cli/datactl.py csv FILE --values htl_mod_kind` |
| sheets + header variants across workbooks | `python .claude/skills/project-cli/datactl.py xlsx "Raw Data"` (`--sheet Process`, `--units`) |
| notebook outline / search / one cell + outputs | `python .claude/skills/project-cli/datactl.py nb FILE.ipynb` · `--grep TEXT` · `--cell 28` or `--cell <id>` |

Promoted from the trace: 53 ad-hoc pandas scripts on `processed/`, 21 notebook-JSON scripts, 9 Raw Data surveys.
Tested in `.claude/tests/test_datactl.py`. For big notebooks use `datactl nb --cell` instead of Read or raw JSON, then
edit with NotebookEdit using the cell id it prints.

## Verified commands (re-run 2026-10-21, real exit codes)
The research agent package is `psc_agent` in `agent for research/` (renamed from psc_recheck on 2026-10-21).
| command | what | evidence |
|---|---|---|
| `cd "agent for research" && PYTHONPATH=src python -m unittest discover -s tests` | psc_agent tests incl. web (fake OpenRouter per role) | exit 0, `Ran 45 tests … OK` |
| `PYTHONPATH="agent for research/src" PYTHONIOENCODING=utf-8 python -m psc_agent pairs --raw "Raw Data" --pdf "original paper"` | pair PDFs ↔ workbooks by DOI | exit 0, `42 paired, 1 DOI(s) without PDF, 9 PDF(s) without workbooks` |
| `PYTHONPATH="agent for research/src" PYTHONIOENCODING=utf-8 python -m psc_agent map --data processed/prepared_v2 --skills "agent for research/skills"` | rebuild the data-map skill | exit 0 |
| `PYTHONPATH="agent for research/src" PYTHONIOENCODING=utf-8 python -m psc_agent skills --skills "agent for research/skills" --role analyst` | print a role's system prompt | exit 0 |
| `python -m unittest discover -s .claude/tests` | hooks, map, trace, plan, datactl | exit 0, `Ran 25 tests … OK` |
| `PYTHONPATH="agent for research/src" python -m psc_agent serve --root . --port 5057` | web app (waitress) | served /, /api/status, /api/pairs (42 pairs); job POST without key → 409 (2026-10-21) |
| `cd "agent for research/tailwind" && npm ci && npm run build` | rebuild app.css (Tailwind 4.3.3) | exit 0, `Done in …ms` |
| `python .claude/skills/repo-map/build_map.py --check` | is REPO_MAP.md fresh | exit 0 `fresh` (1 = stale) |
| `python .claude/skills/trace-to-skill/tracelog.py digest` | what ran / failed / was ad hoc | exit 0 |

From the trace, not re-run (they rewrite tracked files under `reports/`):
| command | trace evidence |
|---|---|
| `PYTHONIOENCODING=utf-8 python scripts/gen_model_report.py` | ok ×2, last 2026-09-01 |
| `PYTHONIOENCODING=utf-8 python scripts/gen_model_flow.py` | ok ×2, last 2026-09-01 |
| `PYTHONIOENCODING=utf-8 python scripts/gen_pipeline_report.py` | ok ×1 (exit masked by a pipe), 2026-08-31 |

Known failure: `python run_pipeline.py verify` from Git Bash → exit 1. GNU tar 1.35 (`/usr/bin/tar`) mangles
`-C C:\…` (`Cannot open`). Works only where `tar` is Windows bsdtar (cmd/PowerShell). The fix belongs in
run_pipeline.py (extract with Python's `tarfile`).

## Gotchas (each one failed in the trace)
- `python -c "…"` containing `$` → bash expands it (`syntax error: operand expected`). Use `python - <<'EOF' … EOF`.
- Thai output → `UnicodeEncodeError: 'charmap'` on this console. Prefix `PYTHONIOENCODING=utf-8`.
- Windows paths in Python string literals (`"C:\Users…"`) → `SyntaxError: (unicode error)`. Use forward slashes.
- Guessed names failed: `KeyError: 'htl_dose'`, `FileNotFoundError: processed/model/model_stability_effect.csv`. Look them
  up with `datactl csv --values` and REPO_MAP first.
- `cmd | tail -N` and `cmd; echo …` exit 0 even when cmd failed. To verify, run unmasked or use `set -o pipefail`.
- `python -I` hides user site-packages (openpyxl, pandas live there). Use it only for stdlib scripts (the hooks); plain
  `python` for datactl and psc_recheck.
- PDFs print `MuPDF error: format error: No common ancestor in structure tree` on stderr: harmless.
