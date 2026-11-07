"""โหลดค่าที่คนกรอกเอง (processed/normalize/ = ผลอ่าน Raw Data ยังไม่ผ่านการล้าง) เป็นตารางระดับอุปกรณ์

ใช้ normalize ไม่ใช่ prepared_v2 เพราะต้องการ 'สิ่งที่คนกรอก' ก่อนถูกแก้ ค่าเท่านั้นที่ตัดสิน
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

NORM = Path(__file__).resolve().parents[2] / "processed" / "normalize"
PROP_MAP = {"Champion PCE": "pce", "Champion Voc": "voc", "Champion Jsc": "jsc", "Champion FF": "ff"}
LAYER_MAP = {"HTL": "htl", "HTL treatment": "htl_treatment", "HTL Treatment": "htl_treatment",
             "ETL": "etl", "PSK": "perovskite"}


def load_truth() -> pd.DataFrame:
    dev = pd.read_csv(NORM / "devices.csv", dtype=str) if (NORM / "devices.csv").exists() else None
    props = pd.read_csv(NORM / "properties.csv", dtype=str)
    layers = pd.read_csv(NORM / "layers.csv", dtype=str)

    p = props[props["property"].isin(PROP_MAP)].copy()
    p["field"] = p["property"].map(PROP_MAP)
    p["value"] = pd.to_numeric(p["value"], errors="coerce")
    wide = p.pivot_table(index="device_id", columns="field", values="value", aggfunc="first")

    l = layers[layers["layer_type"].isin(LAYER_MAP)].copy()
    l["field"] = l["layer_type"].map(LAYER_MAP)
    lw = l.pivot_table(index="device_id", columns="field", values="material", aggfunc="first")

    out = wide.join(lw, how="outer").reset_index()
    out["doi_raw"] = out["device_id"].str.replace(r"_Sample\d+$", "", regex=True)
    return out
