"""ขั้น 3 -- processed/prepared/  ->  processed/prepared_v2/

พอร์ตจาก notebooks/prepare_data/recheck_comlumn_preapare.ipynb

สิ่งที่เปลี่ยนจาก notebook (ตั้งใจ ไม่ใช่การพอร์ตพลาด):
  · การแก้รายเครื่องทั้งหมดย้ายไป pipeline/overrides/*.csv -- เพิ่มเคสใหม่ไม่ต้องแก้โค้ด
  · override ที่จับไม่เจอ -> ข้ามแล้วขึ้น review (notebook เดิม assert แล้วตาย)
  · ด่านที่ "ข้อมูลใหม่ทำให้แดงได้" -> review แล้วเดินต่อ ติดธงไว้ที่แถวนั้น
  · ด่านที่ "แดงแล้วผลลัพธ์เชื่อไม่ได้" -> ยังหยุดเหมือนเดิม (require)
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from . import overrides as OV
from . import parsers as P
from .gates import GateReport
from .paths import Paths

MAIN_IDS = {1, 2, 4, 6, 8}
COATING_IDS = {3, 5, 7}
MOD_KIND = {3: "htl_mod_kind", 5: "psk_mod_kind"}
COAT_MAP = {3: "coat_htl", 5: "coat_psk", 7: "coat_etl"}

# รวมหน่วยพ้อง -- ชื่อหน่วยที่ไม่ถูก map จะกลายเป็นคอลัมน์ของตัวเอง แล้วค่าจะไม่เข้า knob เงียบ ๆ
#   "Μl" ใช้มิวตัวใหญ่ (U+039C) ไม่ใช่ µ (U+00B5) -- เจอ 16 แถว
#   "s" ใช้แทนวินาทีเหมือน "sec" -- เจอ 151 แถว ซึ่งทั้งหมดเคยหลุดออกจากคอลัมน์เวลา
UNIT_FIX = {
    "µL": "uL", "μL": "uL", "Μl": "uL", "ΜL": "uL",
    "μl": "uL", "ul": "uL", "uL": "uL",
    "s": "sec", "S": "sec",
    "min each": "min",
}
SEC_PER = {"sec": 1, "min": 60, "hr": 3600}

# หน่วยที่มี knob อ่านจริง -- ที่เหลือจะถูกรายงานว่า "แตกคอลัมน์แล้วแต่ไม่มีใครอ่าน"
_CONSUMED_UNITS = {"C", "RPM", "sec", "min", "hr", "uL", "ml",
                   "time_s", "vol_uL", "timing_s", "timing_ref"}

TARGETS = ["PCE", "Voc", "Jsc", "FF"]
MEDIATOR = ["VTFL", "Trap density", "Rs", "Rrec", "Contact angle", "Grain size"]
MED_PIVOT = ["VTFL", "Trap density", "Rs", "Rrec", "Contact angle", "Grain size",
             "AFM rms", "VBM", "Work Function", "HI"]

_VAR_ORDER = ["champion", "plain", "average"]
_SCAN_ORDER = ["-", "RS", "FS"]

# SCLC: N_t = 2*eps*eps0*V_TFL / (q*L^2)
EPS_R, EPS0, Q_E = 32.0, 8.854e-14, 1.602e-19
L_OK_NM = (250.0, 900.0)

LAYER_DEPENDENT = {"mobility", "Rs", "Rsh", "Conductivity", "VBM", "Fermi level",
                   "Work Function", "Bandgap", "optical band gap"}

KNOB = ["psk_spin_rpm", "psk_anneal_C", "psk_anneal_time_s",
        "antisolvent_vol_uL", "antisolvent_timing_s", "htl_anneal_C"]

_DESC_QTY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(RPM|rpm|°C|C\b|sec\b|\bs\b|min\b|hr\b)", re.I)


def split_param(param, unit):
    """คืน ({unit: value}, note) -- ทุกเส้นทางที่ทำให้ข้อมูลหายต้องมี note"""
    has_p = pd.notna(param) and str(param).strip() != ""
    has_u = pd.notna(unit) and str(unit).strip() != ""
    if not has_p and not has_u:
        return {}, ""                       # ว่างทั้งคู่ = ช่องว่างของ template ไม่ใช่ข้อมูลหาย
    if has_p and not has_u:
        return {}, f"มี parameter แต่ไม่มี unit: {param!r}"
    if has_u and not has_p:
        return {}, f"มี unit แต่ไม่มี parameter: {unit!r}"

    ps = [t.strip() for t in str(param).split(",") if t.strip()]
    us = [UNIT_FIX.get(t.strip(), t.strip()) for t in str(unit).split(",") if t.strip()]

    def _key(out, u):
        return u if u not in out else f"{u}#{sum(1 for k in out if k.split('#')[0] == u) + 1}"

    if len(ps) != len(us):
        n = min(len(ps), len(us))
        out = {}
        for p, u in zip(ps[:n], us[:n]):
            out[_key(out, u)] = pd.to_numeric(p, errors="coerce")
        return out, (f"param {len(ps)} ค่า != unit {len(us)} ค่า (เก็บ {n} คู่แรก): "
                     f"{param!r} | {unit!r}")
    out = {}
    for p, u in zip(ps, us):
        out[_key(out, u)] = pd.to_numeric(p, errors="coerce")
    return out, ""


def _implied_L_nm(vtfl, nt):
    if pd.isna(vtfl) or pd.isna(nt) or nt <= 0:
        return np.nan
    return float(np.sqrt(2 * EPS_R * EPS0 * vtfl / (Q_E * nt)) * 1e7)


def _paper(idx):
    return [str(i).rsplit("_Sample", 1)[0] for i in idx]


# ===========================================================================

def run(paths: Paths, gate: GateReport) -> dict:
    gate.stage = "stage3"
    tp = {p.stem: pd.read_csv(p) for p in sorted(paths.prepared.glob("*.csv"))}
    if not gate.require(bool(tp), "no-input",
                        f"ไม่มี CSV ใน {paths.prepared} -- ต้องรันขั้น 2 ก่อน"):
        return {}
    lay, pr = tp["layers"], tp["process"]

    n_raw = len(paths.raw_workbooks())
    n_tbl = int(tp["devices"]["device_id"].nunique())
    N_DEV = n_raw if n_raw else n_tbl
    gate.require(N_DEV > 0, "no-device", "หา device ไม่เจอเลย")
    if n_raw:
        gate.require(n_tbl == n_raw, "stale-upstream",
                     f"ไฟล์ใน Raw Data มี {n_raw} แต่ตาราง devices มี {n_tbl} device "
                     f"-- ขั้น 1/2 ยังไม่ได้รันใหม่หลังเพิ่มไฟล์")
    gate.raise_if_blocked()
    gate.note(f"device ที่คาดไว้: {N_DEV}")

    # ---------------------------------------------------------------- §2 ชั้น
    _L = lay.dropna(subset=["material"]).copy()
    dev = tp["devices"][["device_id"]].copy()

    # ชั้นเคลือบจริงต้องมี process ของตัวเอง · มี material แต่ไม่มี process = ผสมในน้ำหมึกชั้นหลัก
    _Pn = pr[pr["process_type"].notna()]
    for lid, col in MOD_KIND.items():
        has_mat = dev["device_id"].isin(_L.loc[_L["layer_id"] == lid, "device_id"])
        has_proc = dev["device_id"].isin(_Pn.loc[_Pn["layer_id"] == lid, "device_id"])
        dev[col] = np.where(~has_mat, None, np.where(has_proc, "coating", "additive"))

    for lid, col in COAT_MAP.items():
        if lid in MOD_KIND:
            dev[col] = dev[MOD_KIND[lid]].eq("coating")
        else:
            dev[col] = dev["device_id"].isin(_L.loc[_L["layer_id"] == lid, "device_id"])
    dev["is_coated"] = dev[list(COAT_MAP.values())].any(axis=1)
    dev["n_coating_layers"] = dev[list(COAT_MAP.values())].sum(axis=1)
    gate.require(bool(dev["coat_htl"].eq(dev["htl_mod_kind"].eq("coating")).all()),
                 "coat-htl", "coat_htl ไม่ตรงกับ htl_mod_kind")

    _P = pr[pr["source_sheet"] == "Process"]
    for lid, col in [(2, "anneal_htl"), (4, "anneal_psk")]:
        hit = _P[(_P["layer_id"] == lid)
                 & _P["process_type"].str.contains("nneal", case=False, na=False)
                 ]["device_id"].unique()
        dev[col] = dev["device_id"].isin(hit)

    # -------------------------------------------------- §4.0 แก้ parameter พิมพ์ผิด
    pr["param_fixed"] = False
    _lidn = pd.to_numeric(pr["layer_id"], errors="coerce")
    fx = OV.param_fix(paths)
    n_ap = 0
    for _, o in fx.iterrows():
        m = ((pr["device_id"] == o["device_id"]) & (_lidn == o["layer_id"])
             & (pr["parameter"].astype(str).str.strip() == o["ค่าดิบ"]))
        what = f"{o['device_id']} L{o['layer_id']} {o['ค่าดิบ']!r}"
        if gate.expect_one(int(m.sum()), "param-fix", what) != "apply":
            continue
        pr.loc[m, "parameter"] = o["ค่าที่ถูก"]
        pr.loc[m, "param_fixed"] = True
        n_ap += 1
    gate.note(f"§4.0 แก้ parameter: {n_ap}/{len(fx)} แถว (ยืนยันจาก SI)")

    # ---------------------------------- §4.0b กู้ค่าที่อยู่ในช่อง Descriptions
    pr["param_recovered"] = False
    _stepn = pd.to_numeric(pr["step_order"], errors="coerce")
    rec = OV.desc_recover(paths)
    n_rec = 0
    for _, o in rec.iterrows():
        m = ((pr["device_id"] == o["device_id"]) & (_lidn == o["layer_id"])
             & (_stepn == o["step_order"]))
        what = f"{o['device_id']} L{o['layer_id']} step{o['step_order']}"
        if gate.expect_one(int(m.sum()), "desc-recover", what) != "apply":
            continue
        pr.loc[m, ["parameter", "unit", "param_recovered"]] = [o["parameter"], o["unit"], True]
        n_rec += 1
    gate.note(f"§4.0b กู้ค่าจากช่อง description: {n_rec}/{len(rec)} แถว")

    # กันไม่ให้เกิดซ้ำ: เลขที่ยังค้างในช่อง description แต่ไม่อยู่ใน parameter
    _leak = []
    for i, r in pr.iterrows():
        d = str(r["description"]) if pd.notna(r["description"]) else ""
        if not d or pd.isna(r["process_type"]):
            continue
        have = str(r["parameter"]) if pd.notna(r["parameter"]) else ""
        for num, _u in _DESC_QTY.findall(d):
            if num not in have:
                _leak.append({"device_id": r["device_id"], "layer_id": r["layer_id"],
                              "process_type": r["process_type"], "description": d,
                              "parameter": have, "เลขที่ยังไม่เข้าระบบ": num})
    if _leak:
        gate.review("desc-leak",
                    f"มีข้อมูลเชิงปริมาณค้างในช่อง description {len(_leak)} จุด ที่ยังไม่เข้า parameter "
                    f"-- ยืนยันกับเปเปอร์แล้วเพิ่มลง overrides/desc_recover.csv",
                    pd.DataFrame(_leak))

    # ------------------------------------------- §4.0c แก้ตามเปเปอร์ต้นฉบับ
    _pp0 = tp["properties"]
    for _, o in OV.prop_value_fix(paths).iterrows():
        m = (_pp0["device_id"] == o["device_id"]) & (_pp0["property"] == o["property"])
        if gate.expect_one(int(m.sum()), "prop-value-fix",
                           f"{o['device_id']} / {o['property']}") != "apply":
            continue
        # ห้ามแตะ `value` ซึ่งเป็นค่าดิบตามที่กรอกมา (และเป็น dtype str)
        for c in ["value_parsed", "value_after_unit_fix"]:
            if c in _pp0.columns:
                _pp0.loc[m, c] = float(o["ค่าที่ถูก"])
        _pp0.loc[m, "value_note"] = (
            (_pp0.loc[m, "value_note"].fillna("") + " | " + str(o["note"])).str.strip(" |"))

    for alias, canon in OV.base_alias_recheck(paths).items():
        m = _pp0["base_property"] == alias
        if m.any():
            _pp0.loc[m, "base_property"] = canon
            _pp0.loc[m, "value_note"] = (
                _pp0.loc[m, "value_note"].fillna("")
                + f" | recheck §4.0c: ยุบชื่อย่อ '{alias}' เข้ากับ '{canon}' (ปริมาณเดียวกัน)"
            ).str.strip(" |")
            gate.note(f"§4.0c ยุบชื่อ {alias} -> {canon}: {int(m.sum())} แถว")

    _new = []
    for _, o in OV.prop_add(paths).iterrows():
        m = ((_pp0["device_id"] == o["device_id"])
             & (_pp0["base_property"] == o["base_property"]))
        if m.any():
            gate.review("prop-add-dup",
                        f"overrides/prop_add.csv: {o['device_id']} มี {o['base_property']} "
                        f"อยู่แล้ว -- ไม่เติมซ้ำ")
            continue
        row = {c: pd.NA for c in _pp0.columns}
        row.update({"device_id": o["device_id"], "property": o["property"],
                    "value": o["value"], "unit": o["unit"], "unit_corrected": o["unit"],
                    "remarks": o["remarks"], "base_property": o["base_property"],
                    "stat": "", "scan": "", "layer": pd.NA, "carrier": pd.NA,
                    "value_parsed": o["value"], "value_after_unit_fix": o["value"],
                    "unit_fix_note": "", "value_note": o["note"]})
        _new.append(row)
    if _new:
        tp["properties"] = pd.concat([_pp0, pd.DataFrame(_new)], ignore_index=True)
        gate.note(f"§4.0c เติมแถวที่เปเปอร์มีแต่ไฟล์ขาด: {len(_new)} แถว")

    # แถวที่มีค่าแต่ไม่ได้กรอกชื่อ -- ตั้งชื่อตามหลักฐานจากเปเปอร์
    _pp0 = tp["properties"]
    for _, o in OV.prop_name_fix(paths).iterrows():
        m = ((_pp0["device_id"] == o["device_id"])
             & (_pp0["property"].isna() | (_pp0["property"].astype(str).str.strip() == ""))
             & (_pp0["value"].astype(str).str.strip() == str(o["value"]))
             & (_pp0["unit"].astype(str).str.strip() == str(o["unit"])))
        what = f"{o['device_id']} / {o['value']} {o['unit']}"
        if gate.expect_one(int(m.sum()), "prop-name-fix", what) != "apply":
            continue
        _pp0.loc[m, "property"] = o["property"]
        _pp0.loc[m, "base_property"] = o["base_property"]
        _pp0.loc[m, "value_note"] = (_pp0.loc[m, "value_note"].fillna("")
                                     + " | " + str(o["note"])).str.strip(" |")
        gate.note(f"§4.0c ตั้งชื่อค่าที่ไม่มีชื่อ: {o['value']} {o['unit']} -> {o['property']}")

    _st = tp["stability"]
    for _, o in OV.stab_temp_fix(paths).iterrows():
        m = ((_st["device_id"] == o["device_id"]) & (_st["environment"] == o["environment"])
             & _st["temperature_c_parsed"].isna())
        if gate.expect_one(int(m.sum()), "stab-temp-fix",
                           f"{o['device_id']} / {o['environment']}") != "apply":
            continue
        _st.loc[m, "temperature_c_parsed"] = float(o["temperature_c"])
        _st.loc[m, "temperature_c_note"] = str(o["note"])

    # ---------------------------------------------- §5 แตก parameter เป็นคอลัมน์
    parsed, notes = [], []
    for _, r in pr.iterrows():
        d, note = split_param(r["parameter"], r["unit"])
        parsed.append(d)
        notes.append(note)
    long = []
    for i, d in enumerate(parsed):
        ptype = pr.iloc[i]["process_type"]
        for u, v in d.items():
            long.append({"row": i, "col": f"{ptype}__{u}", "value": v})
    proc_split = pd.DataFrame(long)

    pr_out = pr.copy()
    pr_out["param_note"] = notes
    wide = proc_split.pivot_table(index="row", columns="col", values="value", aggfunc="first")
    pr_out = pr_out.join(wide)
    gate.note(f"§5 แตกเป็น {wide.shape[1]} คอลัมน์ | "
              f"{int((pr_out['param_note'] != '').sum())} แถวติดธง")

    # ค่าที่แตกออกมาเป็นคอลัมน์ <process>__<หน่วย> แต่ไม่มี knob ตัวไหนอ่าน
    #   ไม่ใช่ความผิดพลาดเสมอไป (แรงดันสปัตเตอร์ กำลังพลาสมา ฯลฯ เป็นคนละปริมาณ)
    #   แต่ต้องเห็นว่ามีอะไรอยู่บ้าง ไม่งั้น "หน่วยสะกดต่าง" จะซ่อนอยู่ในนี้แบบเคส Μl
    _unread: dict[str, int] = {}
    for c in (c for c in pr_out.columns if "__" in c):
        u = c.split("__", 1)[1]
        if u not in _CONSUMED_UNITS:
            n = int(pr_out[c].notna().sum())
            if n:
                _unread[u] = _unread.get(u, 0) + n
    if _unread:
        _rows = pd.DataFrame(sorted(({"หน่วย": u, "จำนวนค่า": n} for u, n in _unread.items()),
                                    key=lambda r: -r["จำนวนค่า"]))
        gate.review("unit-not-consumed",
                    f"มีค่า {sum(_unread.values())} ค่าใน {len(_unread)} หน่วย ที่แตกเป็นคอลัมน์แล้ว "
                    f"แต่ไม่มี knob ตัวไหนอ่าน -- ถ้าหน่วยไหนเป็นตัวสะกดอีกแบบของหน่วยที่ใช้อยู่ "
                    f"ให้เพิ่มลง UNIT_FIX ที่เหลือคือปริมาณที่ยังไม่ได้ทำเป็น feature", _rows)

    cols = ["device_id", "is_coated", "n_coating_layers",
            "coat_htl", "coat_psk", "coat_etl", "anneal_htl", "anneal_psk",
            "htl_mod_kind", "psk_mod_kind"]
    device_layer_flags = dev[cols]

    # ---------------------------------------------- §6 รวมเวลาเป็นวินาที
    _ptypes = sorted({c.split("__")[0] for c in pr_out.columns if "__" in c})
    for pt in _ptypes:
        parts = [(f"{pt}__{u}", SEC_PER[u]) for u in ["sec", "min", "hr"]
                 if f"{pt}__{u}" in pr_out.columns]
        if len(parts) > 1 or (parts and parts[0][0].split("__")[1] != "sec"):
            s = pd.Series(0.0, index=pr_out.index)
            any_val = pd.Series(False, index=pr_out.index)
            for col, mult in parts:
                s = s + pr_out[col].fillna(0) * mult
                any_val = any_val | pr_out[col].notna()
            pr_out[f"{pt}__time_s"] = s.where(any_val)

    # §6.1 AntiSolvent: ปริมาตร ml/uL เป็น knob เดียวกัน · เวลาลบ = ก่อนจบ spin
    vol = pd.Series(np.nan, index=pr_out.index)
    if "AntiSolvent__uL" in pr_out.columns:
        vol = pr_out["AntiSolvent__uL"].copy()
    if "AntiSolvent__ml" in pr_out.columns:
        vol = vol.fillna(pr_out["AntiSolvent__ml"] * 1000)
    pr_out["AntiSolvent__vol_uL"] = vol
    t = (pr_out["AntiSolvent__sec"] if "AntiSolvent__sec" in pr_out.columns
         else pd.Series(np.nan, index=pr_out.index))
    pr_out["AntiSolvent__timing_s"] = t
    pr_out["AntiSolvent__timing_ref"] = pd.Series(
        np.where(t < 0, "before_end", "after_start"), index=pr_out.index).where(t.notna())

    # ---------------------------------------------------------------- §7 ฝั่ง Y
    Y, extra = _build_Y(tp, gate)

    # §7.x เสถียรภาพ
    stab = tp["stability"].copy()
    stab["ret"] = pd.to_numeric(stab["retention_pct_parsed"], errors="coerce")
    stab["t_hr"] = pd.to_numeric(stab["time_hr"], errors="coerce")
    stab["Tp"] = pd.to_numeric(stab["temperature_c_parsed"], errors="coerce")
    stab = stab.dropna(subset=["ret"])
    stab["degradation_rate"] = (100 - stab["ret"]) / stab["t_hr"]
    stab["temp_bin"] = pd.cut(stab["Tp"], [0, 30, 60, 100],
                              labels=["~25C", "45-50C", "65-85C"])
    stab_Y = (stab[stab["temp_bin"] == "~25C"]
              .groupby("device_id").agg(retention_25c=("ret", "max"),
                                        degradation_rate=("degradation_rate", "min")))
    gate.note(f"§7 Y {Y.shape} | retention @25C {len(stab_Y)} devices")

    # ------------------------------------------------- §8.5 องค์ประกอบ PSK
    psk_composition, comp_dev, jm = _build_composition(tp, lay, gate)

    # ------------------------------------------------- §8.6 etl_family
    _etl = lay[(lay["layer_id"] == 6) & lay["material"].notna()]
    _dup = _etl.groupby("device_id").size()
    if (_dup > 1).any():
        gate.review("etl-multi-row",
                    f"ชั้น L6 มีหลายแถวใน {int((_dup > 1).sum())} device -- ใช้แถวแรก "
                    f"(notebook เดิมหยุดที่จุดนี้)",
                    _etl[_etl["device_id"].isin(_dup[_dup > 1].index)]
                    [["device_id", "layer_id", "material", "material_family"]])
        _etl = _etl.drop_duplicates("device_id")
    arch_dev = (_etl.set_index("device_id")[["material_family"]]
                .rename(columns={"material_family": "etl_family"}))
    arch_dev["etl_family"] = arch_dev["etl_family"].fillna(
        _etl.set_index("device_id")["material"])

    # ------------------- §8.7 ซิงก์ value_parsed ให้ตาม value_after_unit_fix
    _pp = tp["properties"]
    _v = pd.to_numeric(_pp["value"], errors="coerce")
    _vc = pd.to_numeric(_pp["value_after_unit_fix"], errors="coerce")
    _vp = pd.to_numeric(_pp["value_parsed"], errors="coerce")
    _need = (_vc.notna() & _v.notna() & ~np.isclose(_vc, _v, rtol=1e-9)
             & _vp.notna() & ~np.isclose(_vp, _vc, rtol=1e-9))
    _used = set(TARGETS) | {"VTFL", "Trap density", "Rs", "Rrec"}
    gate.require(not bool(_pp.loc[_need, "base_property"].isin(_used).any()),
                 "order-Y-before-sync",
                 "มีแถว target/mediator ถูกแก้หน่วย -- ต้องซิงก์ก่อนสร้าง Y")
    if _need.any():
        _pp.loc[_need, "value_parsed"] = _vc[_need]
        _pp.loc[_need, "value_note"] = (
            _pp.loc[_need, "value_note"].fillna("")
            + " | recheck §8.7: ซิงก์ value_parsed = value_after_unit_fix (m2 -> cm2 x1e4)"
        ).str.strip(" |")
        gate.note(f"§8.7 ซิงก์ value_parsed {int(_need.sum())} แถว")

    # ------------------------------------------- §8.8 optical band gap (Tauc)
    for _, o in OV.prop_fix_bandgap(paths).iterrows():
        m = (_pp["device_id"] == o["device_id"]) & (_pp["property"] == o["property"])
        if gate.expect_one(int(m.sum()), "prop-fix-bandgap",
                           f"{o['device_id']} / {o['property']}") != "apply":
            continue
        _pp.loc[m, "value_parsed"] = float(o["ค่าที่ถูก"])
        _pp.loc[m, "value_note"] = (_pp.loc[m, "value_note"].fillna("")
                                    + " | " + str(o["note"])).str.strip(" |")

    # ------------------------------------------- §8.8b แก้ตามการตรวจย้อนกลับ
    _apply_property_audits(_pp, paths, gate)

    # ------------------------------------------- §8.9 typo ปริมาณสารตั้งต้น
    _apply_amount_fix(tp, paths, gate, psk_composition, comp_dev, jm)

    # ---------------------------------------------------------------- §9 ฝั่ง X
    X_dev = _build_X(pr_out, device_layer_flags, gate)

    target_table = (device_layer_flags.set_index("device_id")
                    .join(Y).join(stab_Y).join(X_dev).join(comp_dev).join(arch_dev))
    gate.require(len(target_table) == N_DEV and target_table.index.is_unique,
                 "target-shape",
                 f"target_table ต้อง {N_DEV} แถว unique (ได้ {len(target_table)})")
    gate.raise_if_blocked()
    gate.note(f"§9 target_table {target_table.shape}")

    # ---------------------------------------------------------------- §9.5 ด่านตรวจ
    _final_gates(tp, target_table, paths, gate, rec)

    # ---------------------------------------------------------------- §9.6 ของใหม่
    _inventory(tp, pr_out, psk_composition, paths, gate)

    return {"tp": tp, "lay": lay, "pr_out": pr_out, "target_table": target_table,
            "device_layer_flags": device_layer_flags, "psk_composition": psk_composition,
            "N_DEV": N_DEV}


# ===========================================================================
# §7 -- ฝั่ง Y

def _build_Y(tp: dict, gate: GateReport):
    prop = tp["properties"].dropna(subset=["value_parsed"]).copy()
    prop["paper"] = prop["device_id"].str.rsplit("_Sample", n=1).str[0]

    # ชื่อที่ pipeline อ้างถึงต้องมีอยู่จริง -- ถ้าหายแปลว่าชื่อ canonical เปลี่ยน
    # (เคยเกิดจริง: "Trap Density" D ใหญ่ ทำให้คอลัมน์ว่างทั้งชุดแบบไม่มีสัญญาณ)
    _missing = [b for b in TARGETS + MEDIATOR if not (prop["base_property"] == b).any()]
    if _missing:
        _near = sorted({str(b) for b in prop["base_property"].dropna().unique()
                        for m in _missing if m.lower()[:4] in str(b).lower()})
        gate.review("name-missing",
                    f"ชื่อที่ pipeline อ้างถึงไม่มีในข้อมูลแล้ว: {_missing} "
                    f"| ชื่อที่คล้ายกัน: {_near} -> เพิ่มลง BASE_ALIAS ใน stage2_prepare")

    # ---- JV: ต้องเป็นชุดเดียวกันทั้ง variant และทิศทางสแกน ----
    # RS กับ FS คือการวัดคนละครั้งของเซลล์เดียวกัน max อิสระต่อ property จะหยิบข้ามทิศทาง
    _jv = prop[prop["base_property"].isin(TARGETS)].copy()
    _p = _jv["property"].fillna("")
    _jv["variant"] = np.where(_p.str.contains("hampion"), "champion",
                              np.where(_p.str.contains("verage"), "average", "plain"))
    _jv["scan_k"] = _jv["scan"].fillna("-")

    keys = [(v, s) for v in _VAR_ORDER for s in _SCAN_ORDER]
    piv = {}
    for v, s in keys:
        sub = _jv[(_jv["variant"] == v) & (_jv["scan_k"] == s)]
        if len(sub):
            piv[(v, s)] = (sub.pivot_table(index="device_id", columns="base_property",
                                           values="value_parsed", aggfunc="max")
                           .reindex(columns=TARGETS))
    rows = []
    for d in sorted(_jv["device_id"].unique()):
        pick, r = None, None
        for k in keys:
            if (k in piv and d in piv[k].index
                    and piv[k].loc[d, ["Voc", "Jsc", "FF"]].notna().all()):
                pick, r = k, piv[k].loc[d]
                break
        if pick is None:
            pick = ("mixed", "-")
            r = (_jv[_jv["device_id"] == d]
                 .pivot_table(index="device_id", columns="base_property",
                              values="value_parsed", aggfunc="max")
                 .reindex(columns=TARGETS).loc[d])
        _row = {c.lower(): r[c] for c in TARGETS}
        # บางไฟล์กรอก PCE ของชุดเดียวกันหลายค่า -> เลือกค่าที่เข้ากับ voc*jsc*ff/100 ที่สุด
        if pick[0] != "mixed" and all(pd.notna(_row[c]) for c in ["voc", "jsc", "ff"]):
            _cand = _jv[(_jv["device_id"] == d) & (_jv["variant"] == pick[0])
                        & (_jv["scan_k"] == pick[1])
                        & (_jv["base_property"] == "PCE")]["value_parsed"]
            if _cand.nunique() > 1:
                _t = _row["voc"] * _row["jsc"] * _row["ff"] / 100
                _row["pce"] = min(_cand.unique(), key=lambda x: abs(x - _t))
        rows.append({"device_id": d, "jv_variant": pick[0], "jv_scan": pick[1], **_row})
    jv = pd.DataFrame(rows).set_index("device_id")
    gate.note(f"§7 jv_variant {jv['jv_variant'].value_counts().to_dict()} | "
              f"jv_scan {jv['jv_scan'].value_counts().to_dict()}")

    # ---- mediator ----
    _med_src = prop[prop["base_property"].isin(MED_PIVOT)].copy()

    # Rs หน่วย Ω/sq = ความต้านทานแผ่นของฟิล์ม ไม่ใช่ series resistance ของเซลล์ (ต่างกัน ~4000 เท่า)
    _sheet_m = (_med_src["base_property"] == "Rs") & (
        _med_src["unit_corrected"].fillna(_med_src["unit"]).astype(str)
        .str.replace(" ", "").str.lower().str.contains("/sq|persq", regex=True))
    sheet_resistance_htl = (_med_src[_sheet_m].groupby("device_id")["value_parsed"].max()
                            .rename("sheet_resistance_htl"))
    _med_src = _med_src[~_sheet_m]

    # Hole กับ Electron trap density เป็นคนละปริมาณ (วัดจากคนละอุปกรณ์) ห้ามเฉลี่ยรวม
    _tp_all = _med_src[_med_src["base_property"] == "Trap density"].copy()
    _is_e = _tp_all["carrier"].astype(str).str.lower().eq("electron")
    trap_density_electron = (_tp_all[_is_e].groupby("device_id")["value_parsed"].max()
                             .rename("trap_density_electron"))
    _trap_hole = (_tp_all[~_is_e].groupby("device_id")["value_parsed"].max()
                  .rename("trap_density"))

    med = (_med_src[_med_src["base_property"] != "Trap density"]
           .pivot_table(index="device_id", columns="base_property",
                        values="value_parsed", aggfunc="median"))
    med.columns = [c.lower().replace(" ", "_") for c in med.columns]
    med = med.join(_trap_hole, how="outer").join(trap_density_electron, how="outer")

    # ---- วินิจฉัยความเทียบได้ ----
    _vtfl_dev = (_med_src[_med_src["base_property"] == "VTFL"]
                 .groupby("device_id")["value_parsed"].median())
    _defect = (prop[prop["base_property"] == "Defect density"]
               .groupby("device_id")["value_parsed"].max().rename("defect_density_reported"))

    _diag = pd.DataFrame(index=sorted(set(_trap_hole.index) | set(_defect.index)))
    _diag["_vtfl"] = _vtfl_dev.reindex(_diag.index)
    _diag["_nt"] = _trap_hole.reindex(_diag.index).fillna(_defect.reindex(_diag.index))
    _diag["trap_implied_thickness_nm"] = [
        _implied_L_nm(v, n) for v, n in zip(_diag["_vtfl"], _diag["_nt"])]
    _Ln = _diag["trap_implied_thickness_nm"]
    _diag["trap_scale_flag"] = np.where(_Ln.isna(), "no_vtfl",
                                        np.where(_Ln.between(*L_OK_NM), "ok", "scale_suspect"))
    trap_diag = _diag[["trap_implied_thickness_nm", "trap_scale_flag"]]

    # Rrec: ปัญหาสเกลเป็นคุณสมบัติของ *เปเปอร์* (นิยาม/หน่วยที่ใช้) ไม่ใช่ของอุปกรณ์
    _rr = _med_src[_med_src["base_property"] == "Rrec"].groupby("device_id")["value_parsed"].median()
    if len(_rr):
        _lo = np.log10(_rr.replace(0, np.nan))
        _rr_paper = pd.Series(_paper(_lo.index), index=_lo.index)
        _paper_lo = _lo.groupby(_rr_paper).median()
        _sus = set(_paper_lo.index[(_paper_lo - _paper_lo.median()).abs() > 2])
        rrec_diag = pd.Series(np.where(_rr_paper.isin(_sus), "scale_suspect", "ok"),
                              index=_lo.index, name="rrec_scale_flag")
        gate.require(bool(rrec_diag.groupby(_rr_paper).nunique().eq(1).all()),
                     "rrec-flag", "ธง Rrec ไม่สม่ำเสมอภายในเปเปอร์")
        if _sus:
            gate.note(f"§7 Rrec scale_suspect {len(_sus)} เปเปอร์: {sorted(_sus)}")
    else:
        rrec_diag = pd.Series(dtype=object, name="rrec_scale_flag")

    # HI: นิยาม hysteresis index ต่างกันรายเปเปอร์ -> เทียบกับ (PCE_RS - PCE_FS)/PCE_RS
    _hi_rep = prop[prop["base_property"] == "HI"].groupby("device_id")["value_parsed"].median()
    _scan = (prop[(prop["base_property"] == "PCE") & prop["scan"].notna()]
             .dropna(subset=["value_parsed"])
             .pivot_table(index="device_id", columns="scan", values="value_parsed", aggfunc="max"))
    hi_flag = pd.Series(dtype=object, name="hi_def_flag")
    if {"RS", "FS"}.issubset(_scan.columns):
        _calc = ((_scan["RS"] - _scan["FS"]) / _scan["RS"] * 100).dropna()
        _cmp = pd.concat([_hi_rep.rename("rep"), _calc.rename("calc")], axis=1).dropna()
        if len(_cmp):
            _cmp["paper"] = _paper(_cmp.index)
            _bad = {p for p, g in _cmp.groupby("paper")
                    if (np.sign(g["rep"]) != np.sign(g["calc"])).any()}
            _all_pap = pd.Series(_paper(_hi_rep.index), index=_hi_rep.index)
            hi_flag = pd.Series(np.where(_all_pap.isin(_bad), "def_suspect", "ok"),
                                index=_hi_rep.index, name="hi_def_flag")
            if _bad:
                gate.note(f"§7 HI def_suspect {len(_bad)} เปเปอร์: {sorted(_bad)}")

    _afm = prop[prop["base_property"] == "AFM rms"].dropna(subset=["value_parsed"])
    afm_layer = _afm.groupby("device_id")["layer"].first().fillna("ไม่ระบุ").rename("afm_layer")

    Y = (jv.join(med).join(_defect).join(trap_diag).join(rrec_diag)
         .join(sheet_resistance_htl).join(hi_flag).join(afm_layer))
    return Y, {}


# ===========================================================================
# §8.5 -- องค์ประกอบ PSK

def _build_composition(tp: dict, lay: pd.DataFrame, gate: GateReport):
    psk = (lay.loc[lay["layer_type"] == "PSK", ["device_id", "layer_id", "material"]]
           .rename(columns={"material": "layer_material"}))
    jm = psk.merge(tp["materials"], on=["device_id", "layer_id"], how="left")
    jm["prec"] = jm["material"].map(P.canon_precursor)
    jm = jm.dropna(subset=["prec"]).copy()
    jm = jm[~jm["prec"].isin(P.VOLATILE)]
    jm["ufam"] = jm["unit"].map(P.unit_family)
    jm["amt"] = pd.to_numeric(jm["amount"], errors="coerce")

    prec_comp, prec_status = {}, {}
    for d, g in jm.groupby("device_id"):
        fams = set(g["ufam"].dropna())
        bad = g[g["ufam"].isna() | g["amt"].isna()]
        if len(bad):
            prec_status[d] = "ตกหล่น: " + ", ".join(sorted(set(bad["prec"])))
        elif len(fams) != 1:
            prec_status[d] = f"หน่วยปนตระกูล {sorted(fams)}"
        elif not any(P.ION[p].get("B") for p in g["prec"]):
            prec_status[d] = "ไม่มี B-site (สูตรไม่ครบ)"
        else:
            fam, A, X = fams.pop(), {}, {}
            conv = P.MOLAR if fam == "molar" else P.MASS
            for _, r in g.iterrows():
                mol = r["amt"] * conv[str(r["unit"]).strip().lower()]
                if fam == "mass":
                    mol /= P.MW[r["prec"]]
                spec = P.ION[r["prec"]]
                for i, mlt in spec.get("A", {}).items():
                    A[i] = A.get(i, 0) + mol * mlt
                for i, mlt in spec.get("X", {}).items():
                    X[i] = X.get(i, 0) + mol * mlt
            sA, sX = sum(A.values()), sum(X.values())
            prec_comp[d] = ({k: v / sA for k, v in A.items()},
                            {k: v / sX for k, v in X.items()}, "Pb")
            prec_status[d] = f"ok ({fam})"

    TOL = 0.03
    rows = []
    for _, r in psk.iterrows():
        d = r["device_id"]
        f_res, p_res = P.composition_from_formula(r["layer_material"]), prec_comp.get(d)
        row = {"device_id": d, "layer_material": r["layer_material"],
               "precursor_status": prec_status.get(d, "ไม่มีแถว precursor")}
        if f_res:
            A, X, b = f_res
            row["comp_source"] = "formula"
            if p_res:
                pA, pX, _ = p_res
                diff = [k for k in set(A) | set(X) | set(pA) | set(pX)
                        if abs({**A, **X}.get(k, 0) - {**pA, **pX}.get(k, 0)) > TOL]
                row["cross_check"] = "ตรง" if not diff else "ต่าง: " + ", ".join(sorted(diff))
        elif p_res:
            A, X, b = p_res
            row["comp_source"] = "precursor"
            row["cross_check"] = "-- (สูตรแตกไม่ได้)"
        else:
            row["comp_source"] = "ไม่มีข้อมูล"
            rows.append(row)
            continue
        row["b_site"] = b
        row.update({f"a_{k}": round(v, 3) for k, v in A.items()})
        row.update({f"x_{k}": round(v, 3) for k, v in X.items()})
        rows.append(row)

    psk_composition = pd.DataFrame(rows)
    front = ["device_id", "comp_source", "cross_check", "b_site"]
    psk_composition = psk_composition[
        [c for c in front if c in psk_composition.columns]
        + [c for c in psk_composition.columns if c not in front]]

    # ไอออนที่ไม่อยู่ในสูตร = สัดส่วน 0 จริง ไม่ใช่ missing (pure iodide -> x_Br = 0)
    _cd = psk_composition.drop_duplicates("device_id").set_index("device_id")
    comp_dev = _cd.reindex(columns=["a_Cs", "a_FA", "x_Br"])
    _ok = _cd["comp_source"] != "ไม่มีข้อมูล"
    comp_dev.loc[_ok] = comp_dev.loc[_ok].fillna(0.0)

    n_ok = int((psk_composition["comp_source"] != "ไม่มีข้อมูล").sum())
    gate.note(f"§8.5 องค์ประกอบ {n_ok}/{psk_composition['device_id'].nunique()} devices")
    # สองแหล่งขัดกัน: สูตรที่พิมพ์ไว้ในชื่อชั้น กับ สารตั้งต้นที่กรอกใน Materials
    #   pipeline เลือกใช้ "สูตร" เสมอเมื่อแตกได้ แล้วเก็บผลการเทียบไว้ในคอลัมน์ cross_check
    #   แต่เดิมไม่มีใครรายงาน -> ค่าที่ขัดกันไหลเข้า a_Cs/a_FA/x_Br โดยไม่มีสัญญาณ
    _conf = psk_composition[psk_composition["cross_check"].fillna("").str.startswith("ต่าง")]
    if len(_conf):
        _u = _conf.drop_duplicates("device_id")
        gate.review("composition-conflict",
                    f"สูตรที่เขียนกับสารตั้งต้นที่กรอกให้สัดส่วนไม่ตรงกันใน {len(_u)} device "
                    f"-- ค่าที่ใช้อยู่มาจาก 'สูตร' ส่วน 'สารตั้งต้น' บอกอีกอย่าง "
                    f"ต้องตัดสินว่าจะเชื่อแหล่งไหน (กระทบ x_Br ซึ่งเป็นตัวควบคุมหลัก)",
                    _u[["device_id", "layer_material", "cross_check",
                        "a_Cs", "a_FA", "x_Br"]])

    _fail = psk_composition.loc[psk_composition["comp_source"] == "ไม่มีข้อมูล",
                                ["device_id", "layer_material", "precursor_status"]]
    if len(_fail):
        gate.review("composition-missing",
                    f"แตกองค์ประกอบ PSK ไม่ได้ {len(_fail)} device -- a_Cs/a_FA/x_Br จะว่าง "
                    f"(เพิ่มสารตั้งต้นใน parsers.MW/ION หรือแก้หน่วยที่ต้นทาง)", _fail)
    return psk_composition, comp_dev, jm


# ===========================================================================
# §8.8b -- แก้ property ตามการตรวจย้อนกลับ

def _apply_property_audits(_pp: pd.DataFrame, paths: Paths, gate: GateReport) -> None:
    for _, o in OV.sign_fix(paths).iterrows():
        m = (_pp["device_id"] == o["device_id"]) & (_pp["property"] == o["property"])
        if gate.expect_one(int(m.sum()), "sign-fix",
                           f"{o['device_id']} / {o['property']}") != "apply":
            continue
        _pp.loc[m, ["value_parsed", "value_after_unit_fix"]] = float(o["ค่าที่ถูก"])
        _pp.loc[m, "value_note"] = (_pp.loc[m, "value_note"].fillna("")
                                    + " | " + str(o["note"])).str.strip(" |")

    # VBM ที่รายงานเป็น "ขนาด" โดยละเครื่องหมายลบ -- แยกจาก offset ด้วยขนาด
    #   0-2 eV  = offset จาก Fermi (E_F - E_VBM ซึ่งเป็นบวกตามนิยาม)
    #   3-7 eV  = ค่าสัมบูรณ์อ้างอิงสุญญากาศที่ลืมใส่เครื่องหมาย
    _v = pd.to_numeric(_pp["value_parsed"], errors="coerce")
    _vbm_mag = (_pp["base_property"] == "VBM") & _v.between(3, 7)
    if _vbm_mag.any():
        for c in ["value_parsed", "value_after_unit_fix"]:
            if c in _pp.columns:
                _pp.loc[_vbm_mag, c] = -_v[_vbm_mag]
        _pp.loc[_vbm_mag, "value_note"] = (
            _pp.loc[_vbm_mag, "value_note"].fillna("")
            + " | recheck §8.8b: VBM รายงานเป็นขนาด (ไม่ใส่เครื่องหมาย) ขนาด 3-7 eV "
              "= ระดับอ้างอิงสุญญากาศ ใส่เครื่องหมายลบให้ (audit ข้อ C)").str.strip(" |")
        gate.note(f"§8.8b ใส่เครื่องหมายลบให้ VBM: {int(_vbm_mag.sum())} แถว")

    # ระดับแฟร์มีก็ถูกรายงานเป็น "ขนาด" แบบเดียวกัน -- วงการเขียน work function เป็นบวก
    # แต่ระดับที่อ้างอิงสุญญากาศต้องเป็นลบ · ตรวจไขว้ได้จาก VBM ของเครื่องเดียวกัน
    # (ยืนยันกับ d3ta05069f: E_F -4.54 กับ VBM -5.21 ห่างกัน 0.67 eV ซึ่งเป็นช่วงปกติของ NiOx)
    _v2 = pd.to_numeric(_pp["value_parsed"], errors="coerce")
    _ef_mag = (_pp["base_property"] == "Fermi level") & _v2.between(3, 7)
    if _ef_mag.any():
        for c in ["value_parsed", "value_after_unit_fix"]:
            if c in _pp.columns:
                _pp.loc[_ef_mag, c] = -_v2[_ef_mag]
        _pp.loc[_ef_mag, "value_note"] = (
            _pp.loc[_ef_mag, "value_note"].fillna("")
            + " | recheck §8.8b: ระดับแฟร์มีรายงานเป็นขนาด (work function) ขนาด 3-7 eV "
              "= ระดับอ้างอิงสุญญากาศ ใส่เครื่องหมายลบให้").str.strip(" |")
        gate.note(f"§8.8b ใส่เครื่องหมายลบให้ระดับแฟร์มี: {int(_ef_mag.sum())} แถว")

    _vbm = ((_pp["base_property"] == "VBM")
            & pd.to_numeric(_pp["value_parsed"], errors="coerce").gt(0))
    _pp.loc[_vbm, "base_property"] = "VBM offset from EF"
    _pp.loc[_vbm, "value_note"] = (
        _pp.loc[_vbm, "value_note"].fillna("")
        + " | recheck §8.8b: เป็น E_F - E_VBM (บวก) คนละปริมาณกับ VBM อ้างอิงสุญญากาศ "
          "(audit ข้อ D)").str.strip(" |")

    # ค่าแถบพลังงาน > 2.5 eV เป็นของ HTL ไม่ใช่ perovskite
    _bg = (_pp["base_property"].isin(["Bandgap", "optical band gap", "Energy gap"])
           & pd.to_numeric(_pp["value_parsed"], errors="coerce").gt(2.5)
           & _pp["layer"].isna())
    _pp.loc[_bg, "layer"] = "HTL"
    _pp.loc[_bg, "value_note"] = (
        _pp.loc[_bg, "value_note"].fillna("")
        + " | recheck §8.8b: ค่า > 2.5 eV เป็นของ HTL ไม่ใช่ perovskite เติม layer ให้ "
          "(audit ข้อ J)").str.strip(" |")

    # WF ที่ค่า < 3 และหน่วยเป็น V = ไม่ใช่ work function สัมบูรณ์ (น่าจะเป็น CPD)
    _wf_low = (_pp["base_property"].isin(["WF", "Work Function"])
               & pd.to_numeric(_pp["value_parsed"], errors="coerce").lt(3))
    if _wf_low.any():
        _pp.loc[_wf_low, "base_property"] = "Surface potential (CPD)"
        _pp.loc[_wf_low, "value_note"] = (
            _pp.loc[_wf_low, "value_note"].fillna("")
            + " | recheck §8.8b: ค่า < 3 และหน่วยเป็น V = ไม่ใช่ work function สัมบูรณ์ "
              "(น่าจะเป็น CPD/ผลต่าง) แยกชื่อไว้ -- ต้องยืนยันกับเปเปอร์").str.strip(" |")
        gate.note(f"§8.8b แยก WF ที่ไม่ใช่ค่าสัมบูรณ์: {int(_wf_low.sum())} แถว")

    # mobility: remarks ที่ระบุ stack ไม่มี perovskite = hole-only device ของ HTL
    _mob = _pp["base_property"].astype(str).str.contains("mobil", case=False, na=False)
    _rk = _pp["remarks"].fillna("")
    _stack_no_psk = (_rk.str.contains(r"^\s*(?:ITO|FTO)\s*/", regex=True, na=False)
                     & ~_rk.str.contains(r"Pb|perovskite|PSK", case=False, regex=True, na=False))
    _mob_htl = _mob & _stack_no_psk & _pp["layer"].isna()
    _pp.loc[_mob_htl, "layer"] = "HTL"
    _pp.loc[_mob_htl, "value_note"] = (
        _pp.loc[_mob_htl, "value_note"].fillna("")
        + " | recheck §8.8b: remarks ระบุ stack ที่ไม่มี perovskite = hole-only device "
          "ของ HTL (audit ข้อ K)").str.strip(" |")
    _mob_unk = _mob & _pp["layer"].isna()
    _pp.loc[_mob_unk, "value_note"] = (
        _pp.loc[_mob_unk, "value_note"].fillna("")
        + " | recheck §8.8b: ยังชี้ไม่ได้ว่าเป็น mobility ของชั้นไหน — ห้ามเทียบข้ามเปเปอร์ "
          "(audit ข้อ K)").str.strip(" |")
    gate.note(f"§8.8b mobility: เติม layer=HTL {int(_mob_htl.sum())} แถว | "
              f"ยังไม่ทราบชั้น {int(_mob_unk.sum())} แถว")


# ===========================================================================
# §8.9 -- typo ปริมาณสารตั้งต้น + คำนวณองค์ประกอบใหม่

def _apply_amount_fix(tp: dict, paths: Paths, gate: GateReport,
                      psk_composition: pd.DataFrame, comp_dev: pd.DataFrame,
                      jm: pd.DataFrame) -> None:
    _mm = tp["materials"]
    for _, o in OV.material_amount_fix(paths).iterrows():
        m = (_mm["device_id"].astype(str).str.startswith(str(o["device_id_prefix"]))
             & (_mm["material"] == o["material"]) & (_mm["layer_id"] == o["layer_id"]))
        n_exp = int(o["n_แถวที่คาด"])
        what = f"{o['device_id_prefix']} / {o['material']} L{o['layer_id']}"
        if int(m.sum()) != n_exp:
            gate.review("amount-fix-count",
                        f"overrides/material_amount_fix.csv คาด {n_exp} แถว "
                        f"เจอ {int(m.sum())} -- ข้ามไป: {what}")
            continue
        _cur = pd.to_numeric(_mm.loc[m, "amount"], errors="coerce")
        if not bool((_cur == float(o["ค่าดิบ"])).all()):
            gate.review("amount-fix-stale",
                        f"ค่าดิบไม่ใช่ {o['ค่าดิบ']} แล้ว (เจอ {sorted(_cur.unique())}) "
                        f"-- ข้ามไป: {what}")
            continue
        good = float(o["ค่าที่ถูก"])
        _mm.loc[m, "amount"] = str(good)      # คอลัมน์นี้เป็นข้อความ ต้องเขียนเป็นสตริง
        if "amount_parsed" in _mm.columns:
            _mm.loc[m, "amount_parsed"] = good
        if "amount_note" in _mm.columns:
            _mm.loc[m, "amount_note"] = str(o["note"])
        gate.note(f"§8.9 แก้ {o['material']} {o['ค่าดิบ']} -> {good}: {int(m.sum())} แถว")

        # §8.5 รันไปแล้ว -> ต้อง patch psk_composition + comp_dev ตรงนี้
        for d in sorted(_mm.loc[m, "device_id"].unique()):
            g = jm[jm["device_id"] == d].copy()
            if g.empty:
                continue
            g.loc[g["prec"] == o["material"], "amt"] = good
            A, X = {}, {}
            for _, r in g.iterrows():
                u = str(r["unit"]).strip().lower()
                if u not in P.MOLAR:
                    A = X = {}
                    break
                mol = r["amt"] * P.MOLAR[u]
                spec = P.ION[r["prec"]]
                for i, k in spec.get("A", {}).items():
                    A[i] = A.get(i, 0) + mol * k
                for i, k in spec.get("X", {}).items():
                    X[i] = X.get(i, 0) + mol * k
            if not A or not X:
                gate.review("amount-fix-recompute",
                            f"คำนวณองค์ประกอบใหม่ของ {d} ไม่ได้ (หน่วยไม่ใช่ตระกูล molar)")
                continue
            sA, sX = sum(A.values()), sum(X.values())
            A = {k: round(v / sA, 3) for k, v in A.items()}
            X = {k: round(v / sX, 3) for k, v in X.items()}
            _pm = psk_composition["device_id"] == d
            for ion, val in A.items():
                psk_composition.loc[_pm, f"a_{ion}"] = val
            for ion, val in X.items():
                psk_composition.loc[_pm, f"x_{ion}"] = val
            psk_composition.loc[_pm, "precursor_status"] = (
                f"ok (molar) | §8.9: {o['material']} {o['ค่าดิบ']}->{good} (typo ในเปเปอร์)")
            comp_dev.loc[d, ["a_Cs", "a_FA", "x_Br"]] = [
                A.get("Cs", 0), A.get("FA", 0), X.get("Br", 0)]


# ===========================================================================
# §9 -- ฝั่ง X (knob ระดับ device)

def _build_X(pr_out: pd.DataFrame, device_layer_flags: pd.DataFrame,
             gate: GateReport) -> pd.DataFrame:
    # "NiOx synthesis" = สูตรทำผง ไม่ใช่ขั้นตอนสร้างเซลล์ -> confound 100% กับเปเปอร์เดียว
    _cell = pr_out[pr_out["source_sheet"] == "Process"]
    _lidn = pd.to_numeric(_cell["layer_id"], errors="coerce")
    _pskp, _htlp = _cell[_lidn == 4], _cell[_lidn == 2]

    def _knob(df, ptype, col, agg):
        if col not in df.columns:
            return pd.Series(dtype=float)
        g = df[df["process_type"] == ptype].groupby("device_id")[col]
        # min_count=1: กลุ่ม NaN ล้วนต้องได้ NaN ไม่ใช่ 0 (ไม่งั้นอ่านเป็น "อบ 0 วินาที")
        return g.sum(min_count=1) if agg == "sum" else getattr(g, agg)()

    X_dev = pd.DataFrame(index=device_layer_flags.set_index("device_id").index)

    # ใช้ "ขั้นสุดท้ายจริง" ตาม step_order ทั้ง RPM และเวลา ไม่ใช่เดาว่าขั้นสุดท้าย = RPM สูงสุด
    _sp = _pskp[(_pskp["process_type"] == "Spin coating")
                & _pskp.get("Spin coating__RPM", pd.Series(dtype=float)).notna()] \
        if "Spin coating__RPM" in _pskp.columns else _pskp.iloc[0:0]
    if len(_sp):
        _rpm_last = (_sp.sort_values("step_order").groupby("device_id").tail(1)
                     .set_index("device_id")["Spin coating__RPM"])
        _rpm_max = _sp.groupby("device_id")["Spin coating__RPM"].max()
        _mis = _rpm_last[(_rpm_last - _rpm_max.reindex(_rpm_last.index)).abs() > 1e-9]
        if len(_mis):
            gate.note(f"§9 {len(_mis)} device ที่ขั้นสุดท้ายไม่ใช่ RPM สูงสุด -> ใช้ค่าขั้นสุดท้าย")
    else:
        _rpm_last = pd.Series(dtype=float)
    X_dev["psk_spin_rpm"] = _rpm_last
    X_dev["psk_anneal_C"] = _knob(_pskp, "Annealing", "Annealing__C", "max")
    X_dev["psk_anneal_time_s"] = _knob(_pskp, "Annealing", "Annealing__time_s", "sum")
    X_dev["antisolvent_vol_uL"] = _knob(_pskp, "AntiSolvent", "AntiSolvent__vol_uL", "max")
    X_dev["antisolvent_timing_s"] = _knob(_pskp, "AntiSolvent", "AntiSolvent__timing_s", "first")
    X_dev["antisolvent_timing_ref"] = (_pskp.dropna(subset=["AntiSolvent__timing_s"])
                                       .groupby("device_id")["AntiSolvent__timing_ref"].first()
                                       if "AntiSolvent__timing_s" in _pskp.columns
                                       else pd.Series(dtype=object))
    X_dev["htl_anneal_C"] = _knob(_htlp, "Annealing", "Annealing__C", "max")

    # จังหวะหยด antisolvent: ค่าลบ = วินาทีก่อนจบ -> แปลงให้เป็นแกน "นับจากเริ่ม spin" เหมือนกันหมด
    if "Spin coating__sec" in _pskp.columns:
        _spin = (_pskp[(_pskp["process_type"] == "Spin coating")
                       & _pskp["Spin coating__sec"].notna()]
                 .sort_values("step_order").groupby("device_id").tail(1)
                 .set_index("device_id")["Spin coating__sec"])
        _spin_total = (_pskp[(_pskp["process_type"] == "Spin coating")
                             & _pskp["Spin coating__sec"].notna()]
                       .groupby("device_id")["Spin coating__sec"].sum(min_count=1))
    else:
        _spin = _spin_total = pd.Series(dtype=float)
    X_dev["psk_spin_last_s"] = _spin
    _t = X_dev["antisolvent_timing_s"]
    X_dev["antisolvent_drop_s"] = _t.where(_t >= 0, _spin.reindex(X_dev.index) + _t)
    X_dev["antisolvent_drop_frac_of_last"] = X_dev["antisolvent_drop_s"] / X_dev["psk_spin_last_s"]
    X_dev["psk_spin_total_s"] = _spin_total
    X_dev["antisolvent_drop_frac_of_total"] = (X_dev["antisolvent_drop_s"]
                                               / X_dev["psk_spin_total_s"])

    # of_total เป็นตัวหลัก: of_last บังคับให้บาง device ได้ 1.00 พอดี = หยดตอนหยุดปั่น (เป็นไปไม่ได้)
    _edge = X_dev.index[X_dev["antisolvent_drop_frac_of_total"] >= 0.999]
    if len(_edge):
        gate.review("drop-frac-edge",
                    f"antisolvent_drop_frac_of_total แตะ 1.00 ที่ {list(_edge)} "
                    f"= หยดตอนหยุดปั่น -- การตีความจุดอ้างอิงใช้ไม่ได้กับเครื่องพวกนี้")
    _chk = X_dev.dropna(subset=["antisolvent_drop_s", "psk_spin_total_s"])
    _bad_drop = _chk[~((_chk["antisolvent_drop_s"] > 0)
                       & (_chk["antisolvent_drop_s"] <= _chk["psk_spin_total_s"]))]
    if len(_bad_drop):
        gate.review("drop-out-of-range",
                    f"จุดหยด antisolvent หลุดนอกช่วงโปรแกรม spin {len(_bad_drop)} device",
                    _bad_drop.reset_index()[["device_id", "antisolvent_drop_s",
                                             "psk_spin_total_s", "antisolvent_timing_s"]])
    return X_dev


# ===========================================================================
# §9.5 -- ด่านตรวจรวมศูนย์

def _final_gates(tp: dict, target_table: pd.DataFrame, paths: Paths,
                 gate: GateReport, rec: pd.DataFrame) -> None:
    _pp = tp["properties"]
    _v = pd.to_numeric(_pp["value"], errors="coerce")
    _vc = pd.to_numeric(_pp["value_after_unit_fix"], errors="coerce")
    _vp = pd.to_numeric(_pp["value_parsed"], errors="coerce")

    # 1) ทุกการแก้ค่าต้องมีบันทึกเหตุผล -- invariant ของโค้ดเราเอง
    _diff = _vc.notna() & _vp.notna() & ~np.isclose(_vp, _vc, rtol=1e-9)
    _unexp = _diff & (_pp["value_note"].fillna("").str.strip() == "")
    gate.require(not bool(_unexp.any()), "value-unexplained",
                 f"value_parsed ต่างจาก value_after_unit_fix โดยไม่มี value_note {int(_unexp.sum())} แถว")

    # 2) ช่วงค่าที่เป็นไปได้ทางฟิสิกส์ -- ดักได้ทุก property โดยไม่ต้องรู้ชื่อล่วงหน้า
    bad = []
    for bp, (lo, hi, why) in OV.phys_range(paths).items():
        m = (_pp["base_property"] == bp) & _vp.notna() & ((_vp < lo) | (_vp > hi))
        for i in _pp.index[m]:
            bad.append({"device_id": _pp.at[i, "device_id"], "property": _pp.at[i, "property"],
                        "value_parsed": _vp[i], "ช่วงที่ควรเป็น": f"{lo:g} ถึง {hi:g}",
                        "เหตุผล": why})
    _vac = (_pp["base_property"].isin(["VBM", "Fermi level", "VB"])
            & _vp.notna() & (_vp > 0))
    for i in _pp.index[_vac]:
        bad.append({"device_id": _pp.at[i, "device_id"], "property": _pp.at[i, "property"],
                    "value_parsed": _vp[i], "ช่วงที่ควรเป็น": "< 0",
                    "เหตุผล": "ระดับพลังงานอ้างอิงสุญญากาศต้องเป็นลบ"})
    if bad:
        gate.review("phys-range",
                    f"ค่าหลุดช่วงที่เป็นไปได้ทางฟิสิกส์ {len(bad)} แถว -- ต้องตรวจกับต้นฉบับ "
                    f"(ค่ายังอยู่ในไฟล์ ไม่ได้ถูกลบ)", pd.DataFrame(bad))
    else:
        gate.note("§9.5 ด่านตรวจช่วงค่าทางฟิสิกส์: ผ่านทุกแถว")

    # 3) ค่าที่กู้จาก description ต้องเข้าถึง target_table จริง
    #    (notebook เดิมเช็คเลขตายตัวของ 2 เครื่อง -- ตรงนี้เช็คทุกเครื่องที่มี override)
    _knob_any = ["psk_spin_rpm", "psk_anneal_C", "htl_anneal_C"]
    _miss = [d for d in rec["device_id"].unique() if d in target_table.index
             and target_table.loc[d, _knob_any].isna().all()]
    if _miss:
        gate.review("recovered-knob-lost",
                    f"ค่าที่กู้จาก description ไม่ปรากฏใน target_table: {_miss}")
    else:
        gate.note("§9.5 ด่านตรวจค่าที่กู้คืน: ผ่าน")

    # 4) ทุกแถว mobility ต้องบอกชั้นได้ หรือติดธงว่ายังไม่รู้ -- ห้ามเงียบ
    _m = _pp["base_property"].astype(str).str.contains("mobil", case=False, na=False)
    _ok = (_pp.loc[_m, "layer"].notna()
           | _pp.loc[_m, "value_note"].fillna("").str.contains("audit ข้อ K"))
    gate.require(bool(_ok.all()), "mobility-unflagged",
                 "มีแถว mobility ที่ไม่ระบุชั้นและไม่ติดธง")

    # 5) ชื่อคอลัมน์จังหวะหยดต้องไม่กำกวมว่าหารด้วยอะไร
    gate.require("antisolvent_drop_frac" not in target_table.columns, "frac-name",
                 "ชื่อ antisolvent_drop_frac กำกวม -- ต้องใช้ _of_total / _of_last")
    _f = target_table["antisolvent_drop_frac_of_total"].dropna()
    if len(_f):
        _out = _f[~((_f > 0) & (_f < 0.999))]
        if len(_out):
            gate.review("frac-range",
                        f"antisolvent_drop_frac_of_total หลุดช่วง (0,1) {len(_out)} device",
                        _out.rename("frac").reset_index())
        else:
            gate.note(f"§9.5 จังหวะหยด of_total {len(_f)} dev ในช่วง "
                      f"{_f.min():.3f}-{_f.max():.3f}")


# ===========================================================================
# §9.6 -- ทะเบียนของใหม่ที่กติกายังไม่รู้จัก

def _inventory(tp: dict, pr_out: pd.DataFrame, psk_composition: pd.DataFrame,
               paths: Paths, gate: GateReport) -> None:
    inv_path = paths.audit / "known_inventory.json"

    def _uniq(s):
        return sorted({str(x).strip() for x in s.dropna() if str(x).strip()})

    # ชั้น PSK ไม่นับใน material_family เพราะสูตรเคมีมี psk_composition ครอบไว้แล้ว
    _lay_fn = tp["layers"][tp["layers"]["layer_type"].isin(["HTL", "ETL", "Anode", "Cathode"])]
    _ps = psk_composition.set_index("device_id")["precursor_status"].astype(str)
    inv_now = {
        "material_ชั้นฟังก์ชัน": _uniq(_lay_fn["material"]),
        "material_ที่ยังไม่มี_family": _uniq(_lay_fn.loc[_lay_fn["material_family"].isna(),
                                                        "material"]),
        "solvent_system": _uniq(tp["materials"].get("solvent_system", pd.Series(dtype=object))),
        "สารตั้งต้น_PSK_ที่คำนวณไม่ได้": _uniq(_ps[~_ps.str.startswith("ok")]),
        "ชื่อ_property": _uniq(tp["properties"]["property"]),
        "base_property": _uniq(tp["properties"]["base_property"]),
        "process_type": _uniq(pr_out["process_type"]),
        "environment": _uniq(tp["stability"]["environment"]),
    }

    inv_old = (json.loads(inv_path.read_text(encoding="utf-8"))
               if inv_path.exists() else None)
    if inv_old is None:
        gate.note("§9.6 ยังไม่มี snapshot เดิม -- บันทึกครั้งแรก (รอบหน้าจะเทียบให้)")
        rows_new = []
    else:
        rows_new = [{"หมวด": k, "ของใหม่": v}
                    for k in inv_now for v in sorted(set(inv_now[k]) - set(inv_old.get(k, [])))]

    _amb = tp["properties"][tp["properties"]["base_property"].isin(LAYER_DEPENDENT)
                            & tp["properties"]["layer"].isna()]
    gate.note(f"§9.6 property ที่ค่าขึ้นกับชั้นแต่ยังไม่ระบุชั้น: {len(_amb)} แถว")

    if rows_new:
        nw = pd.DataFrame(rows_new)
        paths.audit.mkdir(parents=True, exist_ok=True)
        nw.to_csv(paths.audit / "new_inventory_items.csv", index=False, encoding="utf-8-sig")
        gate.review("new-inventory",
                    f"เจอของที่ไม่เคยเห็นมาก่อน {len(nw)} รายการ -- ไล่ดูว่ากฎเดิมครอบหรือยัง "
                    f"(รายการเต็มที่ processed/audit/new_inventory_items.csv)",
                    nw.groupby("หมวด")["ของใหม่"]
                    .agg(n="size", ตัวอย่าง=lambda s: " | ".join(list(s)[:6])).reset_index())
    elif inv_old is not None:
        gate.note("§9.6 ไม่มีของใหม่ -- กฎเดิมยังครอบชุดข้อมูลนี้ครบ")

    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(json.dumps(inv_now, ensure_ascii=False, indent=1), encoding="utf-8")


# ===========================================================================
# §10 -- เขียน prepared_v2 + verify

def write(state: dict, paths: Paths, gate: GateReport) -> None:
    tp, lay, pr_out = state["tp"], state["lay"], state["pr_out"]
    target_table = state["target_table"]
    device_layer_flags = state["device_layer_flags"]
    N_DEV = state["N_DEV"]
    V2 = paths.prepared_v2
    V2.mkdir(parents=True, exist_ok=True)

    # 1) layers + layer_kind -- map ตรงจาก layer_id ห้าม merge
    #    (มี device ที่ layer 7 สองแถว PCBM+BCP -- merge บน (device_id, layer_id) จะคูณแถว)
    _lay_out = lay.copy()
    _kind = _lay_out["layer_id"].map(
        lambda i: "main" if i in MAIN_IDS else "coating" if i in COATING_IDS else "other")
    _lay_out["layer_kind"] = _kind.where(_lay_out["material"].notna())
    for _lid, _col in MOD_KIND.items():
        _adv = device_layer_flags.loc[device_layer_flags[_col] == "additive", "device_id"]
        _m = (_lay_out["layer_id"] == _lid) & _lay_out["device_id"].isin(_adv)
        _lay_out.loc[_m, "layer_kind"] = "additive"
    gate.require(len(_lay_out) == len(lay), "layers-inflated", "layers แถวพอง")
    _lay_out.to_csv(V2 / "layers.csv", index=False)

    # 2) process
    n_flag = int((pr_out["param_note"] != "").sum())
    if n_flag:
        gate.review("param-note-left",
                    f"เหลือ {n_flag} แถวที่ parameter กับ unit จับคู่ไม่ได้ "
                    f"-- ค่าส่วนที่จับคู่ไม่ได้ไม่เข้า knob (ดูคอลัมน์ param_note ใน process.csv)",
                    pr_out.loc[pr_out["param_note"] != "",
                               ["device_id", "layer_id", "process_type",
                                "parameter", "unit", "param_note"]])
    pr_out.to_csv(V2 / "process.csv", index=False)

    # 3) devices + flags
    tp["devices"].merge(device_layer_flags, on="device_id", how="left").to_csv(
        V2 / "devices.csv", index=False)

    # 4) ตารางที่เหลือ copy ตรง
    for name in ["materials", "properties", "stability", "vendors"]:
        tp[name].to_csv(V2 / f"{name}.csv", index=False)

    # 5) ตารางที่ขั้นนี้สร้าง
    target_table.to_csv(V2 / "target_table.csv")
    device_layer_flags.to_csv(V2 / "device_layer_flags.csv", index=False)
    state["psk_composition"].to_csv(V2 / "psk_composition.csv", index=False)

    # ---- verify จากไฟล์บนดิสก์ ----
    _chk_lay = pd.read_csv(V2 / "layers.csv")
    gate.require(len(_chk_lay) == len(lay), "disk-layers", "layers บนดิสก์แถวไม่ตรง")
    _chk_tt = pd.read_csv(V2 / "target_table.csv")
    gate.require(len(_chk_tt) == N_DEV and _chk_tt["device_id"].is_unique, "disk-target",
                 f"target_table บนดิสก์ต้อง {N_DEV} แถว unique (ได้ {len(_chk_tt)})")

    _hv = _chk_tt.dropna(subset=["voc", "jsc", "ff", "pce"])
    _e = (_hv["voc"] * _hv["jsc"] * _hv["ff"] / 100 - _hv["pce"]).abs()
    _e = _e.set_axis(_hv["device_id"].values)
    _over = _e[_e > 0.2]
    _new = sorted(set(_over.index) - OV.pce_mismatch_known(paths))
    if _new:
        gate.review("pce-mismatch",
                    f"pce != voc*jsc*ff/100 เกิน 0.2 และยังไม่มีคำอธิบาย {len(_new)} เครื่อง "
                    f"-- เปิดเปเปอร์ยืนยันแล้วเพิ่มลง overrides/pce_mismatch_known.csv",
                    pd.DataFrame({"device_id": _new,
                                  "err": [round(_e[d], 3) for d in _new]}))
    gate.note(f"§10 gate pce=voc*jsc*ff/100: max_err {_e.max():.3f} | "
              f"เกิน 0.2: {len(_over)} เครื่อง ({len(_new)} ตัวที่ยังไม่มีคำอธิบาย)")

    # knob ต้องไม่มีค่าที่มาจากชีตสังเคราะห์ (confound 100% กับเปเปอร์เดียว)
    _cell = pr_out[pr_out["source_sheet"] == "Process"]
    _lidn = pd.to_numeric(_cell["layer_id"], errors="coerce")
    if "Annealing__C" in _cell.columns:
        _ref = (_cell[(_lidn == 2) & (_cell["process_type"] == "Annealing")]
                .groupby("device_id")["Annealing__C"].max())
        _now = _chk_tt.set_index("device_id")["htl_anneal_C"].dropna()
        gate.require(bool(_now.round(6).equals(_ref.reindex(_now.index).round(6))),
                     "niox-confound",
                     "htl_anneal_C ไม่ตรงกับค่าที่คำนวณจาก source_sheet='Process' อย่างเดียว")
    gate.note(f"เขียน {V2}")
    gate.raise_if_blocked()
