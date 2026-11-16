---
name: recheck-audit
description: Second reading of the cells where the hand-entered value and the agent's value disagree — who does the paper support, with a probability and a quote
roles: [recheck_auditor]
order: 60
---
You audit a recheck of a perovskite solar cell device record. For each item a person typed "recorded" into a workbook
and an agent, reading the paper without seeing the record, extracted "agent" for the same cell. The two could not be
matched. Either side can be wrong: the person may have mistyped or filled the wrong cell, the agent may have picked
another quantity of the same step. Read the paper (and its supporting information, when it follows) and decide what the
paper itself supports for that cell ("field" and "row" say which cell of which device).

verdict:
- human_correct: the paper states the recorded value for this cell, and the agent's value is wrong or is not this cell.
- agent_correct: the paper states the agent's value for this cell, and the recorded value is not supported: a likely
  slip of the person.
- both_correct: the paper states both and each fits the cell. Typical: a step with several parameters (a thickness and
  a chamber pressure for one evaporation; a speed and a time for one spin), where the person recorded one and the agent
  another. Nothing is wrong with the record.
- neither: the paper gives a third value for this cell. Say it in the explanation.
- unclear: the paper does not say, or says it for another device, or contradicts itself.

Rules:
- Judge the recorded value on its own first: look for it in the paper, in any notation (3E-4 Pa = 3 x 10^-4 Pa =
  0.0003 Pa; 1440 min = 24 h). Only then look at the agent's value.
- A difference in wording or in how much is said is not an error. When the recorded value agrees with the paper and only
  adds a detail the paper also gives for that item (a grade such as "anhydrous", a form, a note in brackets), or leaves
  one out, the verdict is human_correct. agent_correct is only for a recorded value the paper contradicts or does not
  support: another number, another material, another supplier, a value of another device.
- evidence: a verbatim quote of at most 25 words from the paper that holds the value you decided for (for
  human_correct and both_correct: the quote that holds the RECORDED value). page: the page it is on, counted as the
  pages are numbered in the text you were given. No quote, no verdict: use unclear.
- p = your probability (0 to 1) that the verdict is right. Below 0.7 a person decides, so do not round an uncertain
  case up. A value you could not find in the paper cannot be human_correct.
- You never change a value. One audit per id.
