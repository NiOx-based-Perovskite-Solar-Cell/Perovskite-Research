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
- A number read off a plot is an estimate and a person reads that plot again. So give the estimate and, in `note`, say what
  the graph shows: the quantity, both axes with their units, which curve, bar or legend entry is this device, and how the
  number is read (for example "dark J-V on a log-log scale, S2 curve: VTFL is the voltage where the slope changes from 1 to 2").
  A number printed anywhere (text, table, inset, bar label, caption) is not a plot reading: use that one, with its page.
- The supporting-information pages come as text only, without the figure images. A figure there is known from its caption
  and the text that cites it: do not estimate a number from it, say in `note` what the caption says it shows and leave the cell null.
