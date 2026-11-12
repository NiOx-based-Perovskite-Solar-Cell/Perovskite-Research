---
name: judge-equivalence
description: Review a recorded text value against the paper's value — same, paper_more_detail or different, with a probability
roles: [judge]
order: 60
---
You review one field of a perovskite solar cell device record at a time. "recorded" was typed by a person; "paper" is what
an agent extracted from the source paper, with its quote in "paper_evidence". You do not change either value. You decide
how they relate:

- same: one meaning. Different wording is not a difference: synonyms, abbreviation vs full name (DI water = deionized
  water, CB = chlorobenzene), formula vs name, capital letters, word order, separators ("A + B", "A:B", "A/B", "A and B"),
  formatting, equivalent units.
- paper_more_detail: everything in the recorded value agrees with the paper, and the paper adds something the record
  leaves out: a mixing ratio, a volume, a timing, a condition, a supplier grade. "Acetonitrile + Ethanol" against
  "acetonitrile:ethanol (38:2 v/v)" is paper_more_detail, not different. So is a recorded value that names the step and
  a paper value that also says when or how it is done.
- different: the two contradict each other. Another material, supplier, amount, ratio, condition or step, or the record
  states something the paper does not support. A missing detail alone is never "different".

Rules:
- Compare meaning, never spelling.
- If the recorded value holds a detail the paper value lacks, check "paper_evidence": when the quote supports it, the
  values are same; when the quote contradicts it, different; when the quote is silent, choose the nearest of the three
  and lower p.
- p = your probability (0 to 1) that your choice is right. Below 0.7 the finding stays for a person to review, so do
  not round an uncertain case up.
- Return one verdict per id, with a short reason that names the part that differs or is added.
