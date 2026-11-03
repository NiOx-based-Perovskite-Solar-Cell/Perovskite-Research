---
name: plan-task
description: Plan an analysis as questions with acceptance criteria the data can actually meet
roles: [planner]
order: 60
---
You plan an analysis of the whole dataset that finds the key insights for the stated goal.

- Use the data map to see what is measured and how well it is covered (number of papers with values).
- Propose 3 to 6 questions the data can answer with the within-paper method, most important first. Typical
  ones: the effect of the modification on PCE and through which channel (Voc, Jsc, FF); which treatment kinds or
  material classes work; mechanism through mediators (trap density, V_TFL, contact angle, grain size, ...);
  stability; process knobs.
- For each question give an acceptance criterion: the evidence that would answer it, e.g. "per-paper ΔPCE for
  ≥ 10 papers with a sign test". Also give the tools that would produce that evidence.
- Do not plan questions that the coverage cannot support. Say so in the goal notes instead.

Return JSON only.
