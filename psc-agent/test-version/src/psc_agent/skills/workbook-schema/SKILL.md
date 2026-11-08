---
name: workbook-schema
description: Exact sheets, columns, layer slots and cell conventions of the hand-entered device workbooks
roles: [recheck, mapper, extract, auditor]
order: 20
---
Each device (sample) of a paper is one workbook `<doi>_Sample<N>.xlsx`. All workbooks have the same sheets and
headers (first row):

- Layer: LayerID | LayerType | Material | Thickness (nm)
- Vender: Material | %Purity | Vender (the supplier)
- Materials: LayerID | Material | Type | Solvent (with ratio, e.g. DMF:DMSO (4:1 v/v)) | Amount | Unit (the unit of Amount)
- Process: LayerID | StepOrder (1, 2, ... within that layer) | ProcessType | Descriptions | Parameter | Unit
- Properties: Properties | Values | Errors (± spread) | Unit | Remarks (scan direction, condition, figure)
- Stability: Environment (the test condition) | Encapsulation (Y or N) | Temperature (°C) | Humidity (%) |
  Time (hr) | Retention (%) | T80 (hr)

Layer slots. LayerID is a fixed slot, the same in every workbook and in the Layer, Materials and Process sheets.
Never renumber it, whatever layers the paper has:

1 Anode (ITO, FTO) · 2 HTL (the hole transport layer: NiOx, Cu:NiOx, ...) · 3 HTL treatment (a SAM, interlayer
or surface treatment applied on the HTL; none for a control) · 4 PSK (the perovskite absorber, by formula, e.g.
Cs0.05MA0.10FA0.85Pb(I0.97Br0.03)3) · 5 PSK treatment (passivation or a layer on the perovskite; none if absent) ·
6 ETL (C60, PCBM, SnO2) · 7 ETL treatment (the buffer, e.g. BCP) · 8 Cathode (Ag, Cu, Au).

The glass is not a layer: it is a Materials row (Type Substrate) under LayerID 1. A slot the device does not use
has no Material.

Names the lab uses (use them when they fit, so that rows can be compared):

- ProcessType: SpinCoating, Annealing, ThermalEvaporation, Wash, Dry, UV/Ozone Treat, Antisolvent, Stir,
  BladeCoating, Plasma Treat, Sputtering, ALD, E-beam Evaporation.
- Materials Type: Precursor, Solution, Additive, Substrate, Nanoparticles, Dispersion, Dopant, Evaporation material,
  SAM solution.
- Properties: Champion PCE, Champion Voc, Champion Jsc, Champion FF; Average PCE, Average Voc, Average Jsc,
  Average FF (the mean in Values, the ± in Errors). When reverse and forward scans are reported separately add a
  suffix: Champion PCE (RS) for reverse, Champion PCE (FS) for forward. Other properties: VTFL, Trap Density, VBM,
  CBM, Work Function, Fermi level, Contact Angle, Grain Size, RMS Roughness, Rs, Rrec, Rsh, HI (hysteresis index),
  Jsc EQE, SPO PCE, Avg PL Lifetime, Hole mobility, Conductivity, Ideality factor, optical band gap.
- Units: % (PCE, FF), V, mA cm-2, eV, nm, cm-3, deg, Ω, Ω cm2, ns, S cm-1, cm2 V-1 s-1.
- Stability Environment: the storage or stress condition, one row each, e.g. "Ambient air", "N2", "Thermal Aging",
  "MPP tracking, 1 Sun, ambient air (ISOS-L-2)". Retention (%) is the percentage of the initial PCE left after
  Time (hr); T80 (hr) only when the paper states it.

Cell conventions: Parameter holds all numbers of a step, comma-separated, with Unit in the same order
(SpinCoating "3000, 30" + "RPM, s"; with a ramp "2000, 200, 10" + "RPM, RPM/s, s"; Annealing "100, 10" + "C, min";
Antisolvent with Descriptions "CB" and Parameter "300, 15" + "μL, s"; ThermalEvaporation "1" + "Å/s").
Numbers have no thousands separators; ranges are written "50-60". A column whose name carries a unit is in that unit.
