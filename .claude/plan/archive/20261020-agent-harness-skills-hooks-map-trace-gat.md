# PLAN: Agent harness skills hooks map trace gate
status: done
closed: 2026-10-20T14:13:08+00:00
created: 2026-10-20

## Goal

Skills say how to work; hooks enforce checkpoints. REPO_MAP.md is the small agents' compass and is rebuilt when files
move. The trace of real tool calls becomes verified commands and a tested CLI. PLAN.md binds every acceptance
criterion to evidence that a small judge model accepts before the work is closed.

## Acceptance criteria

<!-- One `### AC-n:` per observable criterion. Evidence lines are written by plan.py (run / diff / attach); verdict lines by plan.py verdict. Do not type them by hand. -->

### AC-1: The test suite for hooks, map, trace, plan and datactl passes
check: python -m unittest discover -s .claude/tests
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-1-20261020-210011.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-1-20261020-210429.txt
verdict: accept p=0.90 by=ac-judge-haiku sig=2fd37cf2 at=2026-10-20T14:09:44+00:00 | AC-1-20261020-210429.txt (newest): exit 0 on line 8 and 'Ran 25 tests' with OK on lines 14 and 16 for python -m unittest discover -s .claude/tests; that suite holds test_hooks, test_build_map, test_tracelog, test_plan and test_datactl. The older 210011 run (23 tests) is superseded.

### AC-2: The live trace hook recorded the deliberate failing command with its exit code 3
check: python -I -c "import json; recs=[json.loads(l) for l in open('.claude/trace/trace.jsonl', encoding='utf-8')]; fails=[r for r in recs if r.get('event')=='tool' and r.get('ok') is False and r.get('exit')==3]; print(len(recs), 'trace records;', len(fails), 'failure(s) with exit 3, last:', fails[-1]['input'] if fails else None); raise SystemExit(0 if fails else 1)"
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-2-20261020-210015.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-2-20261020-210436.txt
verdict: accept p=0.88 by=ac-judge-haiku sig=7d1ecd08 at=2026-10-20T14:09:44+00:00 | AC-2-20261020-210436.txt (newest): exit 0, and line 10 shows 68 trace records with 1 tool failure where ok is False and exit is 3, whose input is the deliberate sys.exit(3) command. The record count grew from 31 in 210015, which fits a live hook.

### AC-3: Real file create, move and delete each rebuilt REPO_MAP.md through the hook, and the final map is fresh
check: python -I .claude/skills/repo-map/build_map.py --check && ! grep -n "_map_probe" .claude/skills/repo-map/REPO_MAP.md && python -I -c "import json; ev=[json.loads(l) for l in open('.claude/trace/trace.jsonl', encoding='utf-8')]; got=[c for r in ev if r.get('event')=='map' for c in r.get('changes', []) if 'probe' in c]; need=['+scripts/_map_probe.py', '+scripts/_map_probe_moved.py', '-scripts/_map_probe.py', '-scripts/_map_probe_moved.py']; print('map rebuild events from hooks:', got); raise SystemExit(0 if all(n in got for n in need) else 1)"
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-3-20261020-210015.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-3-20261020-210436.txt
verdict: accept p=0.80 by=ac-judge-haiku sig=cc3e1925 at=2026-10-20T14:09:44+00:00 | AC-3-20261020-210436.txt (newest): exit 0, build_map --check prints fresh (line 10), and the hook map events (line 11) show +_map_probe.py for create, +_map_probe_moved.py and -_map_probe.py for move, and -_map_probe_moved.py for delete. The older 210015 run (exit 1, stale) is superseded.

### AC-4: Every command listed as verified in project-cli SKILL.md exits 0 when re-run
check: (cd "agent for research" && PYTHONPATH=src python -m unittest discover -s tests) && PYTHONPATH="agent for research/src" PYTHONIOENCODING=utf-8 python -m psc_recheck pairs --raw "Raw Data" --pdf "original paper" && python -m unittest discover -s .claude/tests && python .claude/skills/repo-map/build_map.py --check && python .claude/skills/trace-to-skill/tracelog.py digest > /dev/null && echo "verified commands: all 5 rows exit 0"
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-4-20261020-210016.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-4-20261020-210437.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-4-20261020-211046.txt
verdict: accept p=0.85 by=ac-judge-haiku sig=873d6ccc at=2026-10-20T14:12:47+00:00 | AC-4-20261020-211046.txt (newest): exit 0 on line 8 and the chain prints 'verified commands: all 5 rows exit 0' on line 65, re-running the five rows of the Verified commands table in project-cli SKILL.md (lines 25-29) with matching outputs: 42 paired on line 63, fresh on line 64, Ran 15 and Ran 25 with OK on lines 73-80. The datactl section sits outside that table and is not counted; the 210016 and 210437 captures are superseded.

### AC-5: datactl answers a real column question and rejects the guessed column from the trace
check: python .claude/skills/project-cli/datactl.py csv processed/prepared_v2/target_table.csv --values htl_mod_kind && ! python .claude/skills/project-cli/datactl.py csv processed/prepared_v2/target_table.csv --values htl_dose && python .claude/skills/project-cli/datactl.py xlsx "Raw Data" --sheet Stability
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-5-20261020-210021.txt
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-5-20261020-210442.txt
verdict: accept p=0.90 by=ac-judge-haiku sig=a051b5e4 at=2026-10-20T14:09:45+00:00 | AC-5-20261020-210442.txt (newest): htl_mod_kind gives 3 distinct values (lines 11-14); htl_dose gives no column (line 16) and the chain still runs the Raw Data step (line 17), so that command exited non-zero. project-cli SKILL.md line 46 records htl_dose as a guessed name that failed in the trace.

### AC-6: Project skills, hooks and agents are not ignored by git (committable; committing was not requested) while caveman skills and the trace stay ignored
check: git check-ignore -q .claude/skills/caveman/SKILL.md && ! git check-ignore -q .claude/skills/repo-map/SKILL.md && ! git check-ignore -q .claude/settings.json && git check-ignore -q .claude/trace/trace.jsonl && git status --short --untracked-files=all .claude .gitignore
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-6-20261020-210022.txt
evidence: diff .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-6-20261020-210023.diff
evidence: test .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-6-20261020-210444.txt
evidence: diff .claude/plan/evidence/agent-harness-skills-hooks-map-trace-gat/AC-6-20261020-210445.diff
verdict: accept p=0.85 by=ac-judge-haiku sig=f941e2c8 at=2026-10-20T14:12:47+00:00 | AC-6-20261020-210444.txt (newest): exit 0 on line 8, and lines 11-42 list every project skill, hook, agent and settings file as ?? (not ignored, so committable), while no .claude/trace/ or caveman path appears and the chain confirms caveman SKILL.md and trace.jsonl are ignored. The ignore rules behind this are in the .gitignore diff AC-6-20261020-210445.diff; the older 210022 and 210023 captures are superseded or identical.

## Notes

Dogfood run: this plan is judged with the same flow it describes.
