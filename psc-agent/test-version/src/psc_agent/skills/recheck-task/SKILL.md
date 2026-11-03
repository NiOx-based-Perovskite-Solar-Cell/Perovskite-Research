---
name: recheck-task
description: Blind re-extraction of hidden cells for rows of an existing database
roles: [recheck]
order: 60
---
You re-check a hand-curated database against the paper the data came from. Each row shows only its key columns
(layer, material, step, property, condition). Fill the other columns with what THIS PAPER reports for THAT
device. You never see the recorded values, so report what the paper says, not what you expect.

1. Several numbers in one cell are separated by ", " (e.g. "4000, 30"). A column whose name carries a unit
   (e.g. "Thickness (nm)", "Time (hr)") is filled in that unit, converting if needed. If the sheet has a "Unit"
   column, put the paper's own unit(s) there in the same order as the numbers and do not convert.
2. found = false only if the paper has no such layer / material / step / property / condition for that device;
   then every value is null.
3. If a shown key value disagrees with the paper (wrong material, step type, property name, ...), keep
   found = true, fill the values from the paper and describe the disagreement in key_issue. Otherwise
   key_issue = null.
4. Answer every row_id exactly once.
