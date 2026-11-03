---
name: analysis-method
description: How this project compares results — within-paper deltas, papers not devices, honest uncertainty
roles: [planner, analyst, reporter, decision]
order: 20
---
These rules are not negotiable:

- Never compare raw values across papers. Every lab has its own baseline. Compare a modified device with the
  control of the same paper (within-paper delta = mean of treated − mean of control), then pool the deltas.
- A paper is identified by its DOI key (lowercase letters and digits only). Some DOIs are written two ways in
  device_id, and the tools merge them.
- Count papers, not devices: one paper = one vote. Several treated levels in one paper are averaged before
  pooling.
- Report n papers, how many deltas are positive (sign test) and the median delta. With fewer than 5 papers it is
  an observation, not a finding.
- A mechanism claim (e.g. lower trap density → higher Voc) needs within-paper evidence for both steps.
  Correlations across papers are confounded by the lab.
- Say what the data cannot show: coverage gaps, small n, and selection (papers report what worked).
