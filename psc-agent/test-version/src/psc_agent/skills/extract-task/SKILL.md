---
name: extract-task
description: Build workbook rows for every device of a new paper — device map first, then rows per sheet
roles: [mapper, extract]
order: 60
---
You turn a paper into database rows, one device (sample) at a time.

Devices (the paper map): list every device the paper fabricates and reports results for: the control
(reference, unmodified) and each modified variant. Every concentration or treatment level is its own device.
Name them S1, S2, ... in the order the paper introduces them, control first, and give each its layer stack
(one Layer row per layer, bottom to top).

Rows: for the sheets you are asked for, give one row per material, per process step, per reported property and
per stability condition, for every device. Steps shared by several devices (e.g. the same perovskite recipe)
are repeated for each device that uses them. Fill only what the paper states for that device. Use the exact
column names of the workbook schema, and follow evidence-rules for every row.
