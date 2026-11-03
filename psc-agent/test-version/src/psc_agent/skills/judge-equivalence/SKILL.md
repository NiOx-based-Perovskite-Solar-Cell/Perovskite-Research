---
name: judge-equivalence
description: Decide whether a recorded text value and the paper's value say the same thing
roles: [judge]
order: 60
---
You decide whether two values for the same field of a perovskite solar cell device record say the same thing.
"recorded" was typed by a curator; "paper" was extracted from the source paper.
Same: synonyms, abbreviations vs full names (DI water = deionized water, CB = chlorobenzene), formula vs name,
formatting, word order, equivalent units, or a recorded value that faithfully summarises the paper value.
Different: other materials, suppliers, amounts, ratios, conditions or steps, or a recorded value that adds or
omits something material.
Return one verdict per id with a short reason.
