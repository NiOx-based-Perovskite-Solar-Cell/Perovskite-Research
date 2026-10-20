# Folder notes for REPO_MAP (curated by the Big Agent)

One line per folder: `- path/: purpose`. build_map.py merges these into the Layout table and lists
folders that still have no line under "Unannotated".

- Raw Data/: source of truth — one hand-entered workbook per device, `<doi>_Sample<N>.xlsx`, sheets Layer/Vender/Materials/Process/Properties/Stability (inspect: `datactl.py xlsx "Raw Data"`)
- original paper/: source PDFs; file names are arbitrary, pair them with workbooks by DOI (`psc-recheck pairs`)
- processed/: pipeline outputs — normalize/ (stage 1) → prepared/ (stage 2) → prepared_v2/ (stage 3, final; target_table.csv = 1 row per device), plus audit/, eda/, model/, review/
- reports/: generated HTML/PDF reports and figures (built by scripts/gen_*.py; do not hand-edit)
- pipeline/: stages 1–3 that turn Raw Data into processed/prepared_v2 (run via run_pipeline.py; overrides/ holds manual fixes)
- scripts/: report generators (gen_pipeline_report, gen_model_report, gen_model_flow) → reports/
- notebooks/: analysis notebooks; the cleaning logic in prepare_data/ was ported into pipeline/
- notebooks/prepare_data/: original cleaning notebooks (column_audit → column_prepare → recheck → after_col_prepare)
- notebooks/old_eda/: earlier EDA and the HTL-modification model notebook (model_htl_modification.ipynb)
- notebooks/deep_info/: evidence that cross-paper comparison is invalid (evidence_proof) + its re-runnable analysis
- notebooks/nomad_lab/: comparison of our database with NOMAD's Perovskite Database (manual vs LLM extraction)
- agent for research/: psc-agent package — research agent on OpenRouter: recheck / extract papers, analyze data; steered by skills (system prompts) and hooks (checkpoints)
- agent for research/src/: package source (psc_agent; default skills in psc_agent/skills/)
- agent for research/tests/: unittest suite for psc_agent (fake OpenRouter per role, no network)
- agent for research/skills/: project skills for psc_agent (generated data-map, learned-rules, lab overrides)
- agent for research/tailwind/: Tailwind build for the web app (npm run build -> src/psc_agent/web/static/app.css; node_modules ignored)
- .claude/: Claude Code project config — hooks, skills, agents (caveman skills are vendored and git-ignored)
- .claude/hooks/: hooks.py — trace every tool call, rebuild this map when files move, PLAN.md evidence gate at Stop
- .claude/skills/: project skills (repo-map, trace-to-skill, project-cli, plan-evidence)
- .claude/agents/: small subagents — repo-scout (search with this map), ac-judge (accept / verify_more / revise)
- .claude/tests/: unittest suite for the hooks, map, trace, plan and datactl tools
