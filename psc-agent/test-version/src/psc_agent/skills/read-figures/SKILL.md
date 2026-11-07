---
name: read-figures
description: How to read values off J-V curves, stability plots, bar and box charts in page images
roles: [recheck, mapper, extract]
order: 40
---
- Read figures from the page images. Name the panel (e.g. "Fig. 4b"), check the axis and its unit, and pick
  the curve or bar of the right device from the legend.
- J–V curve: Voc where J = 0, Jsc at V = 0. Prefer the inset or table numbers when the figure has them.
- Stability plot (normalized PCE vs time): retention at the stated time; T80 where the curve crosses 0.8.
- Bar or box plots: the bar height or the box median; error bars are Errors.
- Give values at the precision the axis allows, and set source = "figure" for anything read off a plot.
