"""โหลด override จากไฟล์ CSV ใน pipeline/overrides/

notebook เดิม hardcode การแก้รายเครื่องไว้ในโค้ด (PARAM_FIX, DESC_RECOVER, PROP_FIX, ...)
พร้อม `assert m.sum() == 1` ผลคือ:
  - เพิ่มเคสใหม่ = ต้องแก้โค้ด
  - device ที่ถูกถอดออกจากชุดข้อมูล = ทั้ง pipeline ตาย

ตรงนี้ย้ายมาเป็นไฟล์ข้อมูล เพิ่มเคสใหม่คือเพิ่มบรรทัดใน CSV
และการจับคู่ใช้ gate.expect_one() ซึ่งข้ามให้เมื่อไม่เจอ (ขึ้น review) และหยุดเมื่อกำกวม
"""

from __future__ import annotations

import pandas as pd

from .paths import Paths


def _read(paths: Paths, name: str) -> pd.DataFrame:
    f = paths.overrides / name
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    return df.dropna(how="all")


def param_fix(paths: Paths) -> pd.DataFrame:
    """(device_id, layer_id, ค่าดิบ) -> ค่าที่ถูก"""
    df = _read(paths, "param_fix.csv")
    if not df.empty:
        df["ค่าดิบ"] = df["ค่าดิบ"].astype(str).str.strip()
        df["ค่าที่ถูก"] = df["ค่าที่ถูก"].astype(str)
    return df


def desc_recover(paths: Paths) -> pd.DataFrame:
    """(device_id, layer_id, step_order) -> parameter, unit"""
    df = _read(paths, "desc_recover.csv")
    if not df.empty:
        df["parameter"] = df["parameter"].astype(str)
        df["unit"] = df["unit"].astype(str)
    return df


def prop_value_fix(paths: Paths) -> pd.DataFrame:
    return _read(paths, "prop_value_fix.csv")


def prop_fix_bandgap(paths: Paths) -> pd.DataFrame:
    return _read(paths, "prop_fix_bandgap.csv")


def sign_fix(paths: Paths) -> pd.DataFrame:
    return _read(paths, "sign_fix.csv")


def prop_add(paths: Paths) -> pd.DataFrame:
    return _read(paths, "prop_add.csv")


def prop_name_fix(paths: Paths) -> pd.DataFrame:
    """แถวที่มีค่าแต่ไม่ได้กรอกชื่อ -- ตั้งชื่อให้ตามหลักฐานจากเปเปอร์"""
    df = _read(paths, "prop_name_fix.csv")
    if not df.empty:
        df["value"] = df["value"].astype(str).str.strip()
        df["unit"] = df["unit"].astype(str).str.strip()
    return df


def stab_temp_fix(paths: Paths) -> pd.DataFrame:
    return _read(paths, "stab_temp_fix.csv")


def material_amount_fix(paths: Paths) -> pd.DataFrame:
    return _read(paths, "material_amount_fix.csv")


def base_alias_recheck(paths: Paths) -> dict[str, str]:
    df = _read(paths, "base_alias_recheck.csv")
    return dict(zip(df["alias"], df["canonical"])) if not df.empty else {}


def phys_range(paths: Paths) -> dict[str, tuple[float, float, str]]:
    df = _read(paths, "phys_range.csv")
    return {r["base_property"]: (float(r["ต่ำสุด"]), float(r["สูงสุด"]), str(r["เหตุผล"]))
            for _, r in df.iterrows()} if not df.empty else {}


def pce_mismatch_known(paths: Paths) -> set[str]:
    df = _read(paths, "pce_mismatch_known.csv")
    return set(df["device_id"].astype(str)) if not df.empty else set()
