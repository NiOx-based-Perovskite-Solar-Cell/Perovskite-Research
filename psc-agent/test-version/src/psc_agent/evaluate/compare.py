"""Compare extracted device workbooks with the hand-entered ones, cell by cell.

1. Devices are paired by their layer stacks (the extractor's S1..Sn are not the curators' Sample1..N).
2. Within a device each sheet's rows are paired by what identifies them: the layer slot, process type,
   property name (champion / average / scan direction), material, or test condition.
3. Every non-empty hand-entered cell gets a verdict: match, review (text that may mean the same), mismatch,
   unit_mismatch, or not_found (the model gave nothing, or not that row). Values only the model gave are
   ``extra``: not wrong, the curator just did not record them.

Numbers go through the recheck comparison (units, rounding to either side's precision, ranges).
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass

from ..recheck.compare import compare_values
from ..sources.workbook import STANDARD_HEADERS, Device
from .text import (CORE_METRICS, blank, formula_status, likely_in_paper, process_key, property_key, solvent_status,
                   text_sim, text_status, vkey)

STATUSES = ("match", "review", "mismatch", "unit_mismatch", "not_found", "extra")
SHEETS = tuple(STANDARD_HEADERS)
# sheet -> the column that names a row (its existence is one "row" cell) and the columns that carry values:
# (column, kind, unit). kind: text | formula | solvent | yn | num (fixed unit) | num+unit (the row's Unit column)
ROW_COLUMN = {"Layer": "Material", "Vender": "Material", "Materials": "Material", "Process": "ProcessType",
              "Properties": "Properties", "Stability": "Environment"}
VALUES = {
    "Layer": [("Thickness (nm)", "num", "nm")],
    "Vender": [("Vender", "text", None), ("%Purity", "num", "%")],
    "Materials": [("Type", "text", None), ("Solvent", "solvent", None), ("Amount", "num+unit", None)],
    "Process": [("Parameter", "num+unit", None)],
    "Properties": [("Values", "num+unit", None), ("Errors", "num+unit", None)],
    "Stability": [("Encapsulation", "yn", None), ("Temperature (°C)", "num", "C"), ("Humidity (%)", "num", "%"),
                  ("Time (hr)", "num", "h"), ("Retention (%)", "num", "%"), ("T80 (hr)", "num", "h")],
}
_YN = {"y": "yes", "yes": "yes", "true": "yes", "n": "no", "no": "no", "false": "no"}


@dataclass
class Cell:
    device: str  # the hand-entered workbook
    ex_device: str  # the extracted device paired with it
    sheet: str
    row: str  # what identifies the row, e.g. "slot 3 HTL treatment", "Champion PCE", "L4 SpinCoating"
    column: str
    kind: str  # "row": does the row exist; "value": a cell in it
    gt: str | None
    gt_unit: str | None
    ex: str | None
    ex_unit: str | None
    status: str
    detail: str = ""
    likely: bool = True  # the hand-entered value is in the PDF text (a ceiling: SI-only values are not)
    core: bool = False  # champion PCE / Voc / Jsc / FF and the HTL, HTL treatment, PSK, ETL materials
    audit_verdict: str | None = None  # extractor_correct | human_correct | equivalent | ambiguous | si_only
    audit_resolved: str | None = None  # resolved true value
    audit_evidence: str | None = None  # verbatim quote or figure/table label
    audit_explanation: str | None = None  # why the discrepancy occurred


# ---------------------------------------------------------------- rows

def _col(sheet: str, header) -> str:
    h = vkey(header)
    for std in STANDARD_HEADERS.get(sheet, ()):
        if vkey(std) == h:
            return std
    if sheet == "Stability":  # lower-case and renamed headers in some workbooks
        for part, std in (("temperature", "Temperature (°C)"), ("humidity", "Humidity (%)"), ("retention", "Retention (%)"),
                          ("normalizepce", "Retention (%)"), ("t80", "T80 (hr)")):
            if part in h:
                return std
        if h.startswith("time"):
            return "Time (hr)"
    return str(header).strip()


def canon_rows(sheet: str, rows) -> list[dict]:
    """Rows as {canonical column: value}, keeping only rows that state a fact."""
    out = []
    for r in rows:
        c = {}
        for header, v in r.cells.items():
            c.setdefault(_col(sheet, header), v)
        if sheet == "Properties":
            keep = not blank(c.get("Properties")) and not (blank(c.get("Values")) and blank(c.get("Errors")))
        elif sheet == "Stability":
            keep = any(not blank(c.get(x)) for x in ("Environment", "Time (hr)", "Retention (%)", "T80 (hr)"))
        else:
            keep = not blank(c.get(ROW_COLUMN[sheet]))
        if keep:
            out.append(c)
    return out


def _slot(c: dict) -> int | None:
    try:
        return int(float(c.get("LayerID")))
    except (TypeError, ValueError):
        return None


def _label(sheet: str, c: dict) -> str:
    slot = _slot(c)
    if sheet == "Layer":
        return f"slot {slot} {c.get('LayerType') or ''}".strip()
    if sheet == "Materials":
        return f"L{slot} {c.get('Material')}"
    if sheet == "Process":
        return f"L{slot} {c.get('ProcessType')}"
    return str(c.get(ROW_COLUMN[sheet]))


def _keys(sheet: str) -> list:
    if sheet == "Layer":
        return [_slot, lambda c: vkey(c.get("LayerType")) or None]
    if sheet == "Vender":
        return [lambda c: vkey(c["Material"])]
    if sheet == "Materials":
        return [lambda c: (_slot(c), vkey(c["Material"])), lambda c: vkey(c["Material"])]
    if sheet == "Process":
        return [lambda c: (_slot(c), process_key(c["ProcessType"]))]
    if sheet == "Properties":
        return [lambda c: property_key(c["Properties"]), lambda c: property_key(c["Properties"])[:2],
                lambda c: property_key(c["Properties"])[:1]]
    return []


def _align(gt: list, ex: list, keys: list):
    """Pair rows by the first key under which they agree; equal keys pair in order. -> pairs, unpaired gt, unpaired ex."""
    pairs, gt_left, ex_left = [], list(gt), list(ex)
    for fn in keys:
        buckets = defaultdict(list)
        for e in ex_left:
            k = fn(e)
            if k is not None:
                buckets[k].append(e)
        still = []
        for g in gt_left:
            k = fn(g)
            if k is not None and buckets.get(k):
                e = buckets[k].pop(0)
                ex_left.remove(e)
                pairs.append((g, e))
            else:
                still.append(g)
        gt_left = still
    return pairs, gt_left, ex_left


def _align_scored(gt: list, ex: list, score, threshold: float):
    cand = sorted(((score(g, e), i, j) for i, g in enumerate(gt) for j, e in enumerate(ex)), reverse=True)
    used_g, used_e, pairs = set(), set(), []
    for s, i, j in cand:
        if s < threshold:
            break
        if i not in used_g and j not in used_e:
            pairs.append((gt[i], ex[j]))
            used_g.add(i)
            used_e.add(j)
    return (pairs, [g for i, g in enumerate(gt) if i not in used_g], [e for j, e in enumerate(ex) if j not in used_e])


def _pair_rows(sheet: str, gt: list, ex: list):
    if sheet == "Stability":
        def score(g, e):
            env = text_sim(g.get("Environment"), e.get("Environment")) if not blank(g.get("Environment")) and not blank(e.get("Environment")) else 0.4
            tg, te = _num(g.get("Time (hr)")), _num(e.get("Time (hr)"))
            t = 0.3 if tg is None or te is None else 1.0 if abs(tg - te) <= 0.05 * max(abs(tg), abs(te), 1e-9) else 0.0
            return 0.5 * env + 0.5 * t
        return _align_scored(gt, ex, score, 0.45)
    pairs, gt_left, ex_left = _align(gt, ex, _keys(sheet))
    if sheet in ("Vender", "Materials") and gt_left and ex_left:  # the same material under another name
        more, gt_left, ex_left = _align_scored(
            gt_left, ex_left, lambda g, e: text_sim(g["Material"], e["Material"]) * (1.0 if _slot(g) == _slot(e) else 0.9), 0.8)
        pairs += more
    return pairs, gt_left, ex_left


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------- cells

def _s(v) -> str | None:
    return None if blank(v) else str(v)


def _score(sheet: str, spec: tuple, g: dict, e: dict, tol: float) -> tuple[str, str] | None:
    """(status, detail) for one value column of a paired row; None when neither side has a value."""
    col, kind, unit = spec
    gv, ev = g.get(col), e.get(col)
    if blank(gv) and blank(ev):
        return None
    if blank(ev):
        return "not_found", "no value given"
    if blank(gv):
        return "extra", "not in the record"
    if kind == "yn":
        return ("match", "") if _YN.get(vkey(gv), vkey(gv)) == _YN.get(vkey(ev), vkey(ev)) else ("mismatch", f"{gv} vs {ev}")
    if kind in ("num", "num+unit"):
        gu, eu = (g.get("Unit"), e.get("Unit")) if kind == "num+unit" else (unit, unit)
        v = compare_values(gv, gu, ev, eu, tol=tol)
        return (v.status, v.detail) if v else None
    if kind == "solvent":
        return solvent_status(gv, ev), f"{gv} vs {ev}"
    st = text_status(gv, ev)
    return st, "" if st == "match" else f"{gv} vs {ev}"


def _material_status(g: dict, e: dict) -> tuple[str, str]:
    """Do two Material cells name the same thing? Perovskite formulas are compared by composition."""
    gv, ev = g["Material"], e["Material"]
    st = formula_status(gv, ev) or text_status(gv, ev)
    return st, "" if st == "match" else f"{gv} vs {ev}"


def _core(sheet: str, g: dict) -> bool:
    if sheet == "Properties":
        base, stat, _ = property_key(g["Properties"])
        return base in CORE_METRICS and stat == "champion"
    return sheet == "Layer" and _slot(g) in (2, 3, 4, 6)


_EVIDENCE = {"Layer": "Material", "Vender": "Material", "Materials": "Material", "Process": "Parameter",
             "Properties": "Values", "Stability": "Retention (%)"}


def _compare_sheet(sheet: str, gt_rows: list, ex_rows: list, *, device: str, ex_device: str, text, tol: float) -> list[Cell]:
    pairs, gt_left, ex_left = _pair_rows(sheet, gt_rows, ex_rows)
    rc = ROW_COLUMN[sheet]
    cells: list[Cell] = []

    def likely(c: dict, col: str) -> bool:
        return True if text is None or blank(c.get(col)) else likely_in_paper(c[col], text)

    def add(g: dict, e: dict | None, row_status: str, row_detail: str) -> None:
        label, core = _label(sheet, g), _core(sheet, g)
        cells.append(Cell(device, ex_device, sheet, label, rc, "row", _s(g.get(rc)), None, _s(e.get(rc)) if e else None, None,
                          row_status, row_detail, likely(g, _EVIDENCE[sheet]), core and sheet == "Layer"))
        for spec in VALUES.get(sheet, []):
            col, kind, unit = spec
            if e is None:
                if blank(g.get(col)):
                    continue
                res = ("not_found", "row not extracted")
            else:
                res = _score(sheet, spec, g, e, tol)
                if res is None:
                    continue
            gu = g.get("Unit") if kind == "num+unit" else unit
            eu = (e.get("Unit") if kind == "num+unit" else unit) if e else None
            cells.append(Cell(device, ex_device, sheet, label, col, "value", _s(g.get(col)), _s(gu), _s(e.get(col)) if e else None,
                              _s(eu), res[0], res[1], likely(g, col), core and sheet == "Properties" and col == "Values"))

    for g, e in pairs:
        if sheet in ("Layer", "Vender", "Materials"):
            st, detail = _material_status(g, e)
        else:
            st, detail = "match", ""
        add(g, e, st, detail)
    for g in gt_left:
        add(g, None, "not_found", "row not extracted")
    for e in ex_left:
        cells.append(Cell(device, ex_device, sheet, _label(sheet, e), rc, "row", None, None, _s(e.get(rc)), None, "extra",
                          "row not in the record"))
    return cells


# ---------------------------------------------------------------- devices

def _stack(device: Device | None) -> dict[int, str]:
    rows = canon_rows("Layer", device.sheets.get("Layer", [])) if device else []
    return {s: str(c["Material"]) for c in rows if (s := _slot(c)) is not None}


def device_similarity(g: Device, e: Device) -> float:
    """How alike two devices' layer stacks are (HTL, treatments, perovskite, ETL, buffer); 1 = same stack."""
    gm, em = _stack(g), _stack(e)
    total = weight = 0.0
    for slot, w in ((2, 1.0), (3, 2.0), (4, 1.0), (5, 2.0), (6, 1.0), (7, 1.0)):
        a, b = gm.get(slot), em.get(slot)
        if a is None and b is None:
            s = 1.0
        elif a is None or b is None:
            s = 0.0
        else:
            s = {"match": 1.0, "mismatch": 0.2}.get(formula_status(a, b) or "", text_sim(a, b))
        total += w * s
        weight += w
    return total / weight


def align_devices(gt: list[Device], ex: list[Device]) -> list[tuple[Device | None, Device | None, float]]:
    """Best one-to-one pairing of hand-entered and extracted devices; ties go to the same position."""
    n, m = len(gt), len(ex)
    sim = [[device_similarity(g, e) + (0.05 if i == j else 0.0) for j, e in enumerate(ex)] for i, g in enumerate(gt)]
    if n and m and max(n, m) <= 7:
        if n <= m:
            best = max(itertools.permutations(range(m), n), key=lambda p: sum(sim[i][j] for i, j in enumerate(p)))
            match = dict(enumerate(best))
        else:
            best = max(itertools.permutations(range(n), m), key=lambda p: sum(sim[i][j] for j, i in enumerate(p)))
            match = {i: j for j, i in enumerate(best)}
    else:  # many devices: greedy by similarity
        match, used = {}, set()
        for s, i, j in sorted(((sim[i][j], i, j) for i in range(n) for j in range(m)), reverse=True):
            if i not in match and j not in used:
                match[i] = j
                used.add(j)
    out = [(gt[i], ex[match[i]] if i in match else None, sim[i][match[i]] if i in match else 0.0) for i in range(n)]
    out += [(None, ex[j], 0.0) for j in range(m) if j not in set(match.values())]
    return out


def compare_devices(gt: list[Device], ex: list[Device], *, pages: list[str] | None = None, tol: float = 0.01):
    """-> (cells, pairing) for one paper. ``pages`` (the PDF text) only sets each cell's ``likely`` flag."""
    from .text import paper_text

    text = paper_text(pages) if pages else None
    cells: list[Cell] = []
    pairing = []
    for g, e, sim in align_devices(gt, ex):
        pairing.append({"record": g.name if g else None, "extracted": e.name if e else None, "similarity": round(sim, 2)})
        if g is None:
            continue  # a device the curators did not record: nothing to score
        for sheet in SHEETS:
            cells += _compare_sheet(sheet, canon_rows(sheet, g.sheets.get(sheet, [])),
                                    canon_rows(sheet, e.sheets.get(sheet, [])) if e else [],
                                    device=g.name, ex_device=e.name if e else "-", text=text, tol=tol)
    return cells, pairing
