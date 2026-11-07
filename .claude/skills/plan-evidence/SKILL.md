---
name: plan-evidence
description: PLAN.md whose acceptance criteria are bound to real evidence (test output, diffs, before/after screenshots) and closed only after a small judge model accepts each one. Use for any multi-step task, when asked to plan with acceptance criteria, and before declaring work done.
---

# PLAN.md: acceptance criteria ↔ evidence

## Structure (`PLAN.md` at the repo root)
```markdown
# PLAN: <title>
status: active                                   ← active | done (plan.py close sets done and archives)
created: 2026-10-20

## Goal
<what done looks like>

## Acceptance criteria
### AC-1: <one observable, checkable claim>
check: <command that proves it, run from the repo root>
evidence: test .claude/plan/evidence/<slug>/AC-1-<time>.txt     ← plan.py run (output + exit code + git HEAD)
evidence: diff .claude/plan/evidence/<slug>/AC-1-<time>.diff    ← plan.py diff
evidence: screenshot before=<png> after=<png>                   ← plan.py attach … screenshot
evidence: file <path>                                           ← plan.py attach … file
verdict: accept p=0.93 by=ac-judge sig=1a2b3c4d at=<utc> | <reason>   ← plan.py verdict

## Notes
```
Prose can go anywhere. The machine lines (`check:`, `evidence:`, `verdict:`) are written by plan.py. Evidence comes
from running things; never type it by hand.

## States (computed by plan.py, in this order)
| state | when | next |
|---|---|---|
| verify_more | no evidence, a missing file, a screenshot without before + after, or code changed after the newest run/diff | `plan.py run / diff / attach` |
| revise | newest `test` evidence exited non-zero, or the judge said revise | fix, then `plan.py run` |
| needs_judge | evidence complete but no verdict for exactly this evidence | ac-judge subagent |
| accept | the judge accepted exactly this evidence | — |

A verdict stores `sig` = hash of the AC text, its check, and the contents of its evidence files. Change any of them and
the AC drops back to needs_judge. Each `run` and `diff` capture also records `# tree:`, a fingerprint of the working
tree (uncommitted diff + untracked files, ignoring PLAN.md, `.claude/plan/` and REPO_MAP.md). Edit code after
capturing and the AC needs a fresh capture, so "tests passed before my last edit" cannot be accepted. In practice: finish
the edits, re-run every check, then judge. `plan.py verdict … accept` is refused while a verify_more or revise blocker
exists.

## Workflow
1. Big Agent: `python .claude/skills/plan-evidence/plan.py new "title" --ac "claim" --check "cmd" …`
2. Work. Produce evidence per AC: `plan.py run AC-n`, `plan.py diff AC-n [paths]`,
   `plan.py attach AC-n screenshot BEFORE.png AFTER.png`, `plan.py attach AC-n file PATH`.
3. `plan.py status` → hand every needs_judge AC to the **ac-judge** subagent (haiku). It reads the evidence and records
   accept / verify_more / revise with a confidence `p`. `p < 0.7` comes back as "escalate:".
4. Big Agent reviews only what the judge did not accept or escalated, instead of every item.
5. `plan.py close` → refused unless every AC is accept; then `status: done` and moved to `.claude/plan/archive/`.

## Stop gate (hook)
While PLAN.md is active, ending a turn with ACs not accepted is blocked once per plan state, with the next action per AC.
Stopping again with nothing changed passes (so a turn can wait for the user), and the user sees a status line.

## Small decision model
The judge contract is typed: AC + evidence in → choice ∈ {accept, verify_more, revise} + probability out. That is the
same shape as TypeSafe's Jev ("state in, probability out, code decides"). To use Jev instead of the haiku subagent,
call it where ac-judge decides and record the result with `plan.py verdict … --by jev --p <prob>`. It is not wired in
yet: no `TYPESAFE_API_KEY` here, and its SDK schema is unverified.
