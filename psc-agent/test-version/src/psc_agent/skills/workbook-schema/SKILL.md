---
name: workbook-schema
description: Exact sheets, columns and cell conventions of the hand-entered device workbooks
roles: [recheck, mapper, extract]
order: 20
---
Each device (sample) of a paper is one workbook `<doi>_Sample<N>.xlsx`. Sheets, header row first:

- Layer: LayerID (1 = bottom, counting up) | LayerType (Anode, HTL, HTL treatment, PSK, PSK treatment, ETL,
  Buffer, Cathode, ...) | Material | Thickness (nm)
- Vender: Material | %Purity | Vender (the supplier)
- Materials: LayerID | Material | Type (Substrate, Precursor, Solution, Dispersion, Additive, Antisolvent, ...) |
  Solvent (with ratio, e.g. DMF:DMSO (4:1 v/v)) | Amount | Unit (the unit of Amount)
- Process: LayerID | StepOrder (1, 2, ... within that layer) | ProcessType (Wash, UV/OzoneTreat, SpinCoating,
  Annealing, Antisolvent, Evaporation, Sputtering, ...) | Descriptions | Parameter | Unit
- Properties: Properties (Champion PCE, Champion Voc, Champion Jsc, Champion FF, Average PCE, PSK optical band
  gap, VTFL, Trap density, Contact angle, AFM rms, Grain size, ...) | Values | Errors (± spread) | Unit |
  Remarks (scan direction, condition, figure)
- Stability: Environment (the test condition) | Encapsulation (Y or N) | Temperature (°C) | Humidity (%) |
  Time (hr) | Retention (%) | T80 (hr)

Conventions: Parameter holds all numbers of a step, comma-separated, with Unit in the same order
(SpinCoating "3000, 30" + "RPM, s"; Annealing "100, 10" + "C, min"). Numbers have no thousands separators;
ranges are written "50-60". A column whose name carries a unit is in that unit.
