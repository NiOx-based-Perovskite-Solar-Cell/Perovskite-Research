"""ขั้น 2 -- processed/normalize/  ->  processed/prepared/  (+ processed/audit/)

พอร์ตจาก notebooks/prepare_data/column_prepare.ipynb
ลำดับการเพิ่มคอลัมน์ต้องเป๊ะตาม notebook เพราะลำดับคอลัมน์ใน CSV ขึ้นกับมัน:

  ช่วง 1 (ข้อความ)  1-unicode/ws -> 2-typo -> 3-variant
  ช่วง 2 (ค่า)      thickness -> stability -> purity -> amount -> material_family
                    -> properties unit fix -> solvent -> stability retention
  ช่วง 3 (ชื่อ)      decompose property -> FF scale -> เขียนดิสก์
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

from . import overrides as OV
from . import parsers as P
from .gates import GateReport
from .paths import Paths

TABLE_ORDER = ["devices", "layers", "materials", "process",
               "properties", "stability", "vendors"]

QUANT_COLS = {"thickness_nm", "amount", "parameter", "value", "error", "sample_no",
              "temperature_c", "humidity_pct", "time_hr", "retention_pct", "t80_hr",
              "initial_pce", "final_pce"}

TYPO_FIX = {
    ("properties", "property"): {
        "Trap densitiy": "Trap density",   # สะกดผิด
        "AverageVoc": "Average Voc",       # space หาย
    },
}

TEXT_COLS = {
    "layers":     ["layer_type", "material"],
    "materials":  ["material", "type", "solvent", "unit"],
    "process":    ["process_type", "description", "unit"],
    "properties": ["property", "unit"],
    "stability":  ["environment", "encapsulation"],
    "vendors":    ["material", "vendor"],
}

# ชื่อ canonical ที่ห้ามให้ "เสียงข้างมาก" ตัดสิน (พอเพิ่มไฟล์แล้วผลโหวตพลิกได้)
PREFERRED = {
    ("process", "process_type", "spincoating"): "Spin coating",
    ("process", "process_type", "thermalevaporation"): "Thermal evaporation",
    ("materials", "solvent", "dmfdmso41vv"): "DMF/DMSO (4:1 v/v)",
    ("materials", "solvent", "dmfdmso3081vv"): "DMF/DMSO (3.08/1 v/v)",
    ("materials", "solvent", "dmfdmso41"): "DMF/DMSO (4:1)",
    ("properties", "property", "grainsize"): "Grain size",   # เสมอกัน 10:10
}

NUM_COLS = {
    "layers":     ["thickness_nm"],
    "materials":  ["amount"],
    "properties": ["value", "error"],
    "stability":  ["temperature_c", "humidity_pct", "time_hr", "retention_pct",
                   "t80_hr", "initial_pce", "final_pce"],
    "vendors":    ["purity"],
}

VOC_JSC_UNIT = {"Voc": "V", "Jsc": "mA cm-2"}

# ---- ชื่อ property ที่ pipeline ปลายทางอ้างถึง ต้องตรึงไว้ ห้ามให้เสียงข้างมากเปลี่ยน ----
# เคยเกิดจริง: template ใหม่เขียน "Trap Density" (D ใหญ่) พอไฟล์ใหม่เยอะกว่า
# ชื่อ canonical พลิก -> recheck match ไม่ติด -> คอลัมน์ trap_density ว่างทั้งชุดแบบเงียบ ๆ
BASE_ALIAS = {
    "trapdensity": "Trap density",
    "contactangle": "Contact angle",
    "grainsize": "Grain size",
    "eisrrec": "Rrec", "eisrs": "Rs",
    "ef": "Fermi level", "efups": "Fermi level",
    "vbmups": "VBM", "vb": "VBM",
    "electricalconductivity": "Conductivity",
    "hysteresisindex": "HI",
    "integratedjsc": "Jsc EQE",
    "bandgap": "optical band gap", "energygap": "optical band gap",
    "kpfmcpd": "Surface potential (CPD)",
    # ความหยาบผิว: เจอ 7 ชื่อสำหรับปริมาณเดียวกัน
    "afmrms": "AFM rms", "afmroughness": "AFM rms", "roughness": "AFM rms",
    "rmsroughness": "AFM rms", "roughnessrms": "AFM rms", "rms": "AFM rms", "rq": "AFM rms",
}


class _Cleaner:
    """ตัวห่อ tables + diff_log -- เท่ากับ log_and_apply/step_diff ของ notebook"""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        self.tables = tables
        self.diff_log: list[dict] = []

    def apply(self, name: str, col: str, new: pd.Series, step: str) -> int:
        df = self.tables[name]
        old = df[col]
        mask = old.notna() & (old != new)
        for idx in df.index[mask]:
            self.diff_log.append({
                "step": step, "table": name, "column": col, "row": int(idx),
                "device_id": df.at[idx, "device_id"] if "device_id" in df.columns else "",
                "before": old.at[idx], "after": new.at[idx]})
        df[col] = new.astype(object)
        return int(mask.sum())


def load(paths: Paths) -> dict[str, pd.DataFrame]:
    """dtype=str -> pandas ไม่แปลง 100 เป็น 100.0 เขียนกลับได้ตรงต้นฉบับ"""
    tables = {p.stem: pd.read_csv(p, dtype=str)
              for p in sorted(paths.normalize.glob("*.csv"))}
    # materials/process มี NaN ใน LayerID ตอน ETL -> pandas cast เป็น float -> "1.0"
    # ขณะที่ layers เป็น "1" ทำให้ merge on layer_id ได้ 0 แถว (join พังเงียบ ๆ)
    for _df in tables.values():
        if "layer_id" in _df.columns:
            _lid = pd.to_numeric(_df["layer_id"], errors="coerce").astype("Int64")
            _df["layer_id"] = _lid.astype(str).replace("<NA>", pd.NA)
    return tables


def run(paths: Paths, gate: GateReport) -> dict:
    gate.stage = "stage2"
    tables = load(paths)
    if not gate.require(bool(tables), "no-input",
                        f"ไม่มี CSV ใน {paths.normalize} -- ต้องรันขั้น 1 ก่อน"):
        return {}
    tables_raw = {name: df.copy() for name, df in tables.items()}
    C = _Cleaner(tables)
    gate.note(f"โหลด {len(tables)} ตาราง: { {k: v.shape for k, v in tables.items()} }")

    # ---------------------------------------------------------- ช่วง 1: ข้อความ
    n_fixed = 0
    for name, df in tables.items():
        for col in df.columns:
            after = df[col].map(lambda v: P.norm_text(v) if isinstance(v, str) else v)
            n_fixed += C.apply(name, col, after, "1-unicode/ws")
    gate.note(f"1-unicode/ws: แก้ {n_fixed} เซลล์")

    n_fixed = 0
    for (name, col), fixes in TYPO_FIX.items():
        n_fixed += C.apply(name, col, tables[name][col].replace(fixes), "2-typo")
    gate.note(f"2-typo: แก้ {n_fixed} เซลล์")

    map_rows = []
    for name, cols in TEXT_COLS.items():
        for col in cols:
            counts = Counter(tables[name][col].dropna())
            groups: dict[str, list] = {}
            for val, n in counts.items():
                k = P.variant_key(val)
                if re.search(r"[^\W\d_]", k):
                    groups.setdefault(k, []).append((val, n))
            for k, variants in groups.items():
                if len(variants) < 2 and (name, col, k) not in PREFERRED:
                    continue
                canonical = (PREFERRED.get((name, col, k))
                             or max(variants, key=lambda v: (v[1], v[0]))[0])
                for val, n in variants:
                    if val != canonical:
                        map_rows.append({
                            "table": name, "column": col, "raw": val,
                            "canonical": canonical, "n_rows": n,
                            "rule": "override" if (name, col, k) in PREFERRED else "majority"})

    var_map = (pd.DataFrame(map_rows,
                            columns=["table", "column", "raw", "canonical", "n_rows", "rule"])
               .sort_values(["table", "column", "canonical", "raw"])
               .reset_index(drop=True))
    for (name, col), g in var_map.groupby(["table", "column"]):
        C.apply(name, col, tables[name][col].replace(dict(zip(g["raw"], g["canonical"]))),
                "3-variant")
    gate.note(f"3-variant: {len(var_map)} แถว mapping")

    diff_cells = pd.DataFrame(C.diff_log,
                              columns=["step", "table", "column", "row", "device_id",
                                       "before", "after"])
    recount = sum(int((tables_raw[n][c].notna() & (tables_raw[n][c] != tables[n][c])).sum())
                  for n in tables for c in tables[n].columns)
    n_logged = len(diff_cells[["table", "column", "row"]].drop_duplicates())
    gate.require(recount == n_logged, "clean-log",
                 f"จำนวนเซลล์ที่แก้ไม่ตรงกับ log ({recount} vs {n_logged})")

    paths.audit.mkdir(parents=True, exist_ok=True)
    var_map.to_csv(paths.audit / "text_cleanup_map.csv", index=False)
    diff_cells.to_csv(paths.audit / "clean_diff_cells.csv", index=False)

    tables_after_text = {name: df.copy() for name, df in tables.items()}
    audit_rows: list[dict] = []

    # ---------------------------------------------------------- ช่วง 2: ค่า
    # layers.thickness_nm
    lay = tables["layers"]
    parsed = lay["thickness_nm"].map(P.parse_thickness)
    lay["thickness_nm_parsed"] = parsed.map(lambda t: t[0])
    lay["thickness_nm_min"] = parsed.map(lambda t: t[1])
    lay["thickness_nm_max"] = parsed.map(lambda t: t[2])
    lay["thickness_nm_note"] = parsed.map(lambda t: t[3])
    for _, r in lay[lay["thickness_nm_note"] != ""].iterrows():
        audit_rows.append({"table": "layers", "column": "thickness_nm",
                           "device_id": r["device_id"], "raw": r["thickness_nm"],
                           "parsed": r["thickness_nm_parsed"], "vmin": r["thickness_nm_min"],
                           "vmax": r["thickness_nm_max"], "extra": r["layer_type"],
                           "note": r["thickness_nm_note"]})

    # stability
    stb = tables["stability"]
    t_ = stb["temperature_c"].map(P.parse_temperature)
    stb["temperature_c_parsed"] = t_.map(lambda x: x[0])
    stb["temperature_c_note"] = t_.map(lambda x: x[1])

    h = stb["humidity_pct"].map(P.parse_range_pm)
    stb["humidity_pct_parsed"] = h.map(lambda x: x[0])
    stb["humidity_pct_min"] = h.map(lambda x: x[1])
    stb["humidity_pct_max"] = h.map(lambda x: x[2])
    stb["humidity_pct_note"] = h.map(lambda x: x[3])

    # t80_hr เดิมว่างทั้งคอลัมน์จึงไม่เคยมี parser -- ชุดที่ 2 เริ่มมีค่า (มีทั้ง "> 1300")
    t80 = stb["t80_hr"].map(P.parse_censored)
    stb["t80_hr_parsed"] = t80.map(lambda x: x[0])
    stb["t80_hr_censor"] = t80.map(lambda x: x[1])
    stb["t80_hr_note"] = t80.map(lambda x: x[2])

    r_ = stb["retention_pct"].map(P.parse_censored)
    stb["retention_pct_parsed"] = r_.map(lambda x: x[0])
    stb["retention_pct_censor"] = r_.map(lambda x: x[1])
    stb["retention_pct_note"] = r_.map(lambda x: x[2])

    for col, pcol, note_col in [("temperature_c", "temperature_c_parsed", "temperature_c_note"),
                                ("humidity_pct", "humidity_pct_parsed", "humidity_pct_note"),
                                ("t80_hr", "t80_hr_parsed", "t80_hr_note"),
                                ("retention_pct", "retention_pct_parsed", "retention_pct_note")]:
        for _, row in stb[stb[note_col] != ""].iterrows():
            audit_rows.append({
                "table": "stability", "column": col, "device_id": row["device_id"],
                "raw": row[col], "parsed": row[pcol],
                "vmin": row.get("humidity_pct_min") if col == "humidity_pct" else None,
                "vmax": row.get("humidity_pct_max") if col == "humidity_pct" else None,
                "extra": row.get("retention_pct_censor", "") if col == "retention_pct" else "",
                "note": row[note_col]})

    # vendors.purity
    ven = tables["vendors"]
    pu = ven["purity"].map(P.parse_purity)
    ven["purity_pct"] = pu.map(lambda x: x[0])
    ven["purity_qualifier"] = pu.map(lambda x: x[1])
    ven["purity_kind"] = pu.map(lambda x: x[2])
    _purity_note = pu.map(lambda x: x[3])
    hit = (ven["purity"].notna() & (ven["purity"].str.strip() != "")
           & ~ven["purity"].fillna("").map(P.is_num_float))
    for idx in ven.index[hit]:
        audit_rows.append({"table": "vendors", "column": "purity",
                           "device_id": ven.at[idx, "device_id"], "raw": ven.at[idx, "purity"],
                           "parsed": ven.at[idx, "purity_pct"], "vmin": None, "vmax": None,
                           "extra": ven.at[idx, "purity_kind"], "note": _purity_note[idx]})

    # materials.amount -- "1:50" หน่วย "v:v" คืออัตราส่วนเจือจาง ไม่ใช่ปริมาณสเกลาร์
    _mt = tables["materials"]
    _am = [P.parse_amount(v, u) for v, u in zip(_mt["amount"], _mt["unit"])]
    _mt["amount_parsed"] = [t[0] for t in _am]
    _mt["amount_ratio"] = [t[1] for t in _am]
    _mt["amount_note"] = [t[2] for t in _am]
    for _, r in _mt[_mt["amount_note"] != ""].iterrows():
        audit_rows.append({"table": "materials", "column": "amount",
                           "device_id": r["device_id"], "raw": r["amount"],
                           "parsed": r["amount_parsed"], "vmin": None, "vmax": None,
                           "extra": r["unit"], "note": r["amount_note"]})

    # material_family (layers / materials / vendors)
    for name in ["layers", "materials", "vendors"]:
        tables[name]["material_family"] = tables[name]["material"].map(P.material_family)

    # properties: หน่วยผิดชนิด + mobility m2 -> cm2
    prop = tables["properties"]
    metric = prop["property"].map(P.metric_of)
    u = prop["unit"].astype(str).str.strip()
    mislabel_idx = prop.index[(u == "%") & metric.isin(["Voc", "Jsc"])]
    mobility_m2_idx = prop.index[(metric == "mobility") & u.str.startswith("m2")]

    prop["unit_corrected"] = prop["unit"]
    _vf = prop["value"].map(P.parse_property_value)
    prop["value_after_unit_fix"] = _vf.map(lambda t: t[0])
    prop["unit_fix_note"] = _vf.map(lambda t: t[1])
    _parse_note = _vf.map(lambda t: t[1])   # เก็บไว้ก่อนสองลูปข้างล่างเขียนทับ note

    for idx in mislabel_idx:
        correct_unit = VOC_JSC_UNIT[metric.at[idx]]
        prop.at[idx, "unit_corrected"] = correct_unit
        prop.at[idx, "unit_fix_note"] = (
            f"unit เดิม '%' ผิดชนิด แก้เป็น '{correct_unit}' (ไม่แตะค่า value)")
    for idx in mobility_m2_idx:
        old_val = prop.at[idx, "value_after_unit_fix"]
        new_val = old_val * 1e4
        prop.at[idx, "value_after_unit_fix"] = new_val
        prop.at[idx, "unit_corrected"] = prop.at[idx, "unit"].replace("m2", "cm2", 1)
        prop.at[idx, "unit_fix_note"] = f"หน่วย m2 -> cm2 (x1e4): {old_val:g} -> {new_val:g}"

    # ทุกแถวที่ถูกตีความ (สัญกรณ์ยกกำลัง / ช่วงค่า / ค่าขอบ) ต้องมีร่องรอยใน audit
    for _i in prop.index[_parse_note.fillna("") != ""]:
        audit_rows.append({"table": "properties", "column": "value",
                           "device_id": prop.at[_i, "device_id"], "raw": prop.at[_i, "value"],
                           "parsed": prop.at[_i, "value_after_unit_fix"],
                           "vmin": None, "vmax": None, "extra": prop.at[_i, "property"],
                           "note": _parse_note[_i]})
    gate.note(f"properties unit fix: {len(mislabel_idx)} mislabel + "
              f"{len(mobility_m2_idx)} mobility m2->cm2")

    # materials.solvent -- ไม่ทับคอลัมน์เดิม เพิ่มคอลัมน์ใหม่
    mat = tables["materials"]
    _sv = pd.DataFrame([P.parse_solvent(v) for v in mat["solvent"]], index=mat.index)
    for c in ["solvent_1", "solvent_2", "solvent_ratio", "solvent_system", "solvent_note"]:
        mat[c] = _sv[c]
    # แยกสองกรณี: จำแนกชื่อไม่ได้เลย (ข้อมูลหาย) vs แยกได้แล้วแต่วงเล็บไม่ใช่อัตราส่วน (แค่หมายเหตุ)
    # ตัวทำละลายเกิน 2 ชนิด -- โครงสร้างเก็บได้แค่ solvent_1/solvent_2 ตัวที่ 3 หายเงียบ ๆ
    def _n_names(v):
        if not isinstance(v, str):
            return 0
        head = v.split("(")[0]
        return len([t for t in re.split(r"[/:,+]| and ", head)
                    if t.strip() and P.canon_solvent(t)])
    _over = mat[mat["solvent"].notna() & (mat["solvent"].map(_n_names) > 2)]
    if len(_over):
        gate.review("solvent-over-two",
                    f"มี {len(_over)} แถวที่กรอกตัวทำละลายเกิน 2 ชนิด -- ตารางเก็บได้แค่ 2 "
                    f"ชนิดที่ 3 และอัตราส่วนของมันไม่ได้ถูกเก็บ",
                    _over[["device_id", "material", "solvent",
                           "solvent_1", "solvent_2", "solvent_system"]].drop_duplicates())

    _bad_sv = mat[mat["solvent"].notna() & (mat["solvent_note"] != "")
                  & mat["solvent_1"].isna()]
    if len(_bad_sv):
        gate.review("solvent-unparsed",
                    f"แยกชื่อตัวทำละลายไม่ได้ {len(_bad_sv)} แถว -- ต้องเพิ่มชื่อใน SOLVENT_CANON",
                    _bad_sv[["device_id", "layer_id", "material", "solvent", "solvent_note"]])

    # stability: stress_group + retention
    stb = tables["stability"]
    stb["stress_group"] = stb["environment"].map(P.stress_group)
    _ini = pd.to_numeric(stb["initial_pce"], errors="coerce")
    _fin = pd.to_numeric(stb["final_pce"], errors="coerce")

    _m_calc = stb["retention_pct_parsed"].isna() & _ini.notna() & _fin.notna() & _ini.ne(0)
    stb.loc[_m_calc, "retention_pct_parsed"] = (_fin[_m_calc] / _ini[_m_calc] * 100).round(2)
    stb.loc[_m_calc, "retention_pct_note"] = "คำนวณจาก final_pce/initial_pce x100"

    # ต้นฉบับ .xlsx จัดรูปแบบเซลล์เป็น Excel percent ('0%') -> ในไฟล์เก็บ 0.97 แต่โชว์ "97%"
    # openpyxl อ่านค่าดิบ จึงได้ 0.97 · สแกนทุกไฟล์แล้วมีเฉพาะรายการใน overrides
    xlsx_percent = _load_percent_devices(paths)
    stb["retention_pct_parsed"] = pd.to_numeric(stb["retention_pct_parsed"], errors="coerce")
    _m_frac = stb["retention_pct_parsed"].notna() & (stb["retention_pct_parsed"] <= 1)
    stb.loc[_m_frac, "retention_pct_parsed"] *= 100
    _confirmed = _m_frac & stb["device_id"].isin(xlsx_percent)
    for _i in stb.index[_confirmed]:
        stb.at[_i, "retention_pct_note"] = xlsx_percent[str(stb.at[_i, "device_id"])]
    stb.loc[_m_frac & ~_confirmed, "retention_pct_note"] = (
        "สเกล: x100 [อนุมาน -- ต้นฉบับพิมพ์ค่าดิบไว้ ไม่ได้จัดรูปแบบ percent ต้องเช็ค paper]")
    _m_over = stb["retention_pct_parsed"] > 100
    stb.loc[_m_over, "retention_pct_note"] = (
        "> 100% (เป็นไปได้จาก light-soaking) เก็บค่าเดิมไว้")
    if int((_m_frac & ~_confirmed).sum()):
        gate.review("retention-scale-guess",
                    f"retention ที่คูณ 100 แบบอนุมาน {int((_m_frac & ~_confirmed).sum())} แถว "
                    f"-- ยืนยันกับเปเปอร์แล้วเพิ่มลง overrides/retention_x100_confirmed.csv",
                    stb.loc[_m_frac & ~_confirmed,
                            ["device_id", "environment", "retention_pct", "retention_pct_parsed"]])

    audit = pd.DataFrame(audit_rows,
                         columns=["table", "column", "device_id", "raw",
                                  "parsed", "vmin", "vmax", "extra", "note"])
    audit.to_csv(paths.audit / "value_parse_audit.csv", index=False)

    # ---- ด่านตรวจช่วง 2 ----
    raw_ok = all(tables[n][c].fillna("").equals(tables_after_text[n][c].fillna(""))
                 for n in tables_after_text for c in tables_after_text[n].columns)
    rows_ok = all(len(tables[n]) == len(tables_after_text[n]) for n in tables)

    n_bad_total = 0
    unhandled = []
    for name, cols in NUM_COLS.items():
        for col in cols:
            s = tables[name][col].fillna("").str.strip()
            bad_idx = s[(s != "") & ~s.map(P.is_num_float)].index
            n_bad_total += len(bad_idx)
            for i in bad_idx:
                note_cols = [c for c in tables[name].columns
                             if c.startswith(col) and c != col]
                vals = [str(tables[name].at[i, c]) for c in note_cols]
                handled = any(v not in ("", "nan", "None") for v in vals)
                flagged_bad = any("parse ไม่ได้" in v or v == "unrecognized" for v in vals)
                if not handled or flagged_bad:
                    unhandled.append({"ตาราง": name, "คอลัมน์": col,
                                      "row": int(i), "ค่า": s[i],
                                      "device_id": tables[name].at[i, "device_id"]})

    gate.require(raw_ok, "orig-touched", "คอลัมน์เดิมถูกแก้ระหว่างช่วง 2 (ห้ามแตะ)")
    gate.require(rows_ok, "row-count", "จำนวนแถวเปลี่ยนระหว่างช่วง 2")
    if unhandled:
        # notebook เดิม assert ที่นี่ -> ข้อมูลชุดใหม่ที่มีรูปแบบใหม่ทำให้ทั้ง pipeline ตาย
        # ตอนนี้ปล่อยผ่านแต่ขึ้น review queue: ค่านั้นเป็น NaN ในผลลัพธ์ (ไม่ได้เดาแทน)
        gate.review("value-unparsed",
                    f"ค่าที่ยังไม่มี parser รองรับ {len(unhandled)} แถว -- ค่าจะเป็นว่างในผลลัพธ์ "
                    f"ต้องเพิ่มกฎใน pipeline/parsers.py",
                    pd.DataFrame(unhandled))
    gate.note(f"ช่วง 2: non-numeric {n_bad_total} แถว | ใน audit {len(audit)} | "
              f"ยังไม่มีใครตีความ {len(unhandled)}")

    # ---------------------------------------------------------- ช่วง 3: ชื่อ property
    prep_audit: list[dict] = []
    prop = tables["properties"]
    parts = prop["property"].map(P.decompose)
    for i, col in enumerate(["base_property", "stat", "scan", "layer", "carrier"]):
        prop[col] = parts.map(lambda t, i=i: t[i])

    groups2: dict[str, list] = {}
    for val, n in Counter(prop["base_property"]).items():
        groups2.setdefault(P.variant_key(val), []).append((val, n))
    base_map = {}
    for k, variants in groups2.items():
        canonical = BASE_ALIAS.get(k) or max(variants, key=lambda v: (v[1], v[0]))[0]
        base_map.update({val: canonical for val, n in variants if val != canonical})
    prop["base_property"] = prop["base_property"].replace(base_map)

    # บังคับ alias อีกชั้นหลังโหวต -- กันเคสที่ชื่อในกลุ่มไม่ได้อยู่ใน BASE_ALIAS โดยตรง
    _alias_hit = prop["base_property"].map(lambda b: BASE_ALIAS.get(P.variant_key(str(b))))
    _am = _alias_hit.notna() & (_alias_hit != prop["base_property"])
    if _am.any():
        prop.loc[_am, "base_property"] = _alias_hit[_am]
    gate.note(f"ชื่อ property {prop['property'].nunique()} แบบ -> "
              f"base_property {prop['base_property'].nunique()} แบบ")

    decomp_map = (prop.groupby(["property", "base_property", "stat", "scan", "layer", "carrier"])
                  .size().rename("n_rows").reset_index()
                  .sort_values(["base_property", "stat", "scan", "layer", "carrier"])
                  .reset_index(drop=True))

    _pv = prop["value"].map(P.parse_property_value)
    prop["value_parsed"] = _pv.map(lambda t: t[0])
    prop["value_note"] = _pv.map(lambda t: t[1])

    # ⚠ parser ไม่แปลงหน่วย -- ค่าที่รายงานเป็น µm ยังเป็น µm อยู่
    #   ยังไม่กระทบตาราง target เพราะชื่อพวกนี้ยังไม่ถูกยุบเข้าคอลัมน์ grain_size
    #   แต่ถ้ายุบชื่อเมื่อไหร่ ต้องแปลง µm -> nm พร้อมกัน ไม่งั้นผิดพันเท่า
    _um = prop["unit"].astype(str).str.strip().isin(["µm", "μm", "um"])
    if _um.any():
        gate.review("unit-micrometre",
                    f"มี {int(_um.sum())} แถวที่รายงานเป็นไมโครเมตร ขณะที่ค่าชนิดเดียวกันส่วนใหญ่เป็นนาโนเมตร "
                    f"-- ค่ายังไม่ถูกแปลงหน่วย ต้องแปลงพร้อมกับตอนยุบชื่อเข้าคอลัมน์",
                    prop.loc[_um, ["device_id", "property", "value", "value_parsed", "unit"]])

    ff_mask = ((prop["base_property"] == "FF") & prop["value_parsed"].notna()
               & (prop["value_parsed"] <= 1))
    ff_ba = prop.loc[ff_mask, ["device_id", "property", "value_parsed"]].rename(
        columns={"value_parsed": "before"})
    prop.loc[ff_mask, "value_parsed"] = prop.loc[ff_mask, "value_parsed"] * 100
    prop.loc[ff_mask, "value_note"] = "FF scale: fraction -> % (x100)"
    ff_ba["after"] = prop.loc[ff_mask, "value_parsed"].values
    for _, r in ff_ba.iterrows():
        prep_audit.append({"table": "properties", "column": "value_parsed",
                           "device_id": r["device_id"], "property": r["property"],
                           "before": r["before"], "after": r["after"],
                           "note": "FF scale x100"})
    gate.note(f"FF: แปลงสเกล {int(ff_mask.sum())} แถว")

    # ---- ด่านตรวจก่อนเขียนดิสก์ ----
    leftover = []
    for name, cols in TEXT_COLS.items():
        for col in cols:
            g2: dict[str, set] = {}
            for val in tables[name][col].dropna():
                k = P.variant_key(val)
                if re.search(r"[^\W\d_]", k):
                    g2.setdefault(k, set()).add(val)
            leftover += [(name, col, sorted(v)) for v in g2.values() if len(v) > 1]
    bad_chars = sum(int(tables[n][c].fillna("").astype(str).str.contains(P.BAD_CHARS_RE).sum())
                    for n in tables for c in tables[n].columns)
    rows_ok = all(len(tables[n]) == len(tables_raw[n]) for n in tables)
    orig_ok = all(tables[n][c].fillna("").equals(tables_after_text[n][c].fillna(""))
                  for n in tables_after_text for c in tables_after_text[n].columns)

    gate.require(not leftover, "variant-leftover",
                 f"ยังมีคำที่สะกดต่างแต่ความหมายเดียวกันเหลืออยู่: {leftover[:3]}")
    gate.require(bad_chars == 0, "bad-chars",
                 f"ยังมีอักขระ mu/minus/NBSP เหลือ {bad_chars} เซลล์")
    gate.require(rows_ok, "row-count-final", "จำนวนแถวไม่เท่าข้อมูลดิบ")
    gate.require(orig_ok, "orig-touched-final", "คอลัมน์เดิมถูกแตะหลังจบช่วง 1")
    gate.raise_if_blocked()

    return {"tables": tables, "tables_raw": tables_raw,
            "decomp_map": decomp_map, "prep_audit": prep_audit}


_RETENTION_DEFAULT_NOTE = "สเกล: x100 [ยืนยันแล้ว: ต้นฉบับใช้ Excel percent format '0%']"


def _load_percent_devices(paths: Paths) -> dict[str, str]:
    """device_id -> ข้อความ note · ว่าง = ใช้ข้อความมาตรฐาน (เคส Excel percent format)"""
    f = paths.overrides / "retention_x100_confirmed.csv"
    if not f.exists():
        return {}
    df = pd.read_csv(f).dropna(how="all")
    notes = df.get("note", pd.Series([""] * len(df))).fillna("").astype(str)
    return {str(d): (n.strip() or _RETENTION_DEFAULT_NOTE)
            for d, n in zip(df["device_id"].astype(str), notes)}


def write(state: dict, paths: Paths, gate: GateReport) -> None:
    tables, tables_raw = state["tables"], state["tables_raw"]
    paths.prepared.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(paths.prepared / f"{name}.csv", index=False)
    state["decomp_map"].to_csv(paths.audit / "property_decomposition.csv", index=False)
    pd.DataFrame(state["prep_audit"]).to_csv(paths.audit / "prepare_audit.csv", index=False)

    # ---- verify จากไฟล์บนดิสก์ ----
    svn = {p.stem: pd.read_csv(p) for p in sorted(paths.prepared.glob("*.csv"))}
    mismatches = []
    for n in tables:
        for c in tables[n].columns:
            mem, dsk = tables[n][c], svn[n][c]
            if pd.api.types.is_numeric_dtype(dsk):
                m = pd.to_numeric(mem, errors="coerce")
                same = (bool((m.isna() == dsk.isna()).all())
                        and bool(((m - dsk).abs().fillna(0) < 1e-9).all()))
            else:
                same = mem.fillna("").astype(str).equals(dsk.fillna("").astype(str))
            if not same:
                mismatches.append(f"{n}.{c}")

    gate.require(not mismatches, "disk-mismatch",
                 f"ไฟล์บนดิสก์ไม่ตรงกับตารางใน memory: {mismatches[:5]}")
    gate.require(all(len(svn[n]) == len(tables_raw[n]) for n in svn), "disk-rows",
                 "จำนวนแถวบนดิสก์ไม่เท่าข้อมูลดิบ")
    # base_property ต้องครบ "ทุกแถวที่มีชื่อ property" -- แถวที่ต้นทางไม่ได้กรอกชื่อเลย
    # เดาแทนไม่ได้ จึงปล่อยให้ base_property ว่างแล้วขึ้น review (ค่าก็ยังอยู่ในไฟล์)
    _named = svn["properties"]["property"].fillna("").str.strip() != ""
    _empty_base = _named & (svn["properties"]["base_property"].fillna("") == "")
    gate.require(not bool(_empty_base.any()), "base-property-empty",
                 f"base_property ว่างในแถวที่มีชื่อ property {int(_empty_base.sum())} แถว")
    # แถวที่มี override ตั้งชื่อรออยู่แล้ว (ขั้น 3 จะเติมให้) ไม่ต้องขึ้น review ซ้ำ
    _fix = OV.prop_name_fix(paths)
    _pending = set()
    if not _fix.empty:
        _pending = {(str(r["device_id"]), str(r["value"]), str(r["unit"]))
                    for _, r in _fix.iterrows()}
    _nameless = svn["properties"][~_named]
    if _pending and len(_nameless):
        _key = _nameless.apply(lambda r: (str(r["device_id"]), str(r["value"]).strip(),
                                          str(r["unit"]).strip()), axis=1)
        _nameless = _nameless[~_key.isin(_pending)]
    if len(_nameless):
        gate.review("property-nameless",
                    f"มีแถวที่ไม่ได้กรอกชื่อ property {len(_nameless)} แถว "
                    f"-- ค่าเข้าระบบแต่ไม่ถูกจัดกลุ่ม (เดาชื่อแทนไม่ได้ ต้องเปิดเปเปอร์)",
                    _nameless[["device_id", "value", "unit", "remarks"]])

    ffv = svn["properties"].loc[svn["properties"]["base_property"] == "FF",
                               "value_parsed"].dropna()
    if len(ffv) and not bool(((ffv > 30) & (ffv <= 100)).all()):
        _out = svn["properties"].loc[
            (svn["properties"]["base_property"] == "FF")
            & ~svn["properties"]["value_parsed"].between(30, 100, inclusive="right"),
            ["device_id", "property", "value", "value_parsed"]]
        gate.review("ff-range", f"FF หลุดช่วง 30-100 ({ffv.min():.1f}-{ffv.max():.1f}) "
                                f"-- เช็คว่าเป็นเศษส่วนที่ยังไม่ถูกคูณ 100 หรือค่ากรอกผิด", _out)
    gate.note(f"เขียน {paths.prepared} | FF {ffv.min():.1f}-{ffv.max():.1f}"
              if len(ffv) else f"เขียน {paths.prepared}")
    gate.raise_if_blocked()
