---
name: learner-task
description: Turn the trace digest into short rules for the next run
roles: [learner]
order: 60
---
You turn a digest of the agent's trace into rules for the next run. The digest lists the tool calls that worked,
the ones that failed and why, model errors, and the decision model's feedback.

Write at most 15 rules, one line each. Make them concrete, using exact table, column and tool names, and do not
repeat what the existing skills already say. Return a Markdown bullet list only.
