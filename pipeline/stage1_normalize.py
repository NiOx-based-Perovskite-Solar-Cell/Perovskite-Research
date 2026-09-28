"""ขั้น 1 -- Raw Data/*.xlsx  ->  processed/normalize/

พอร์ตจาก notebooks/old_eda/explore_reschema.ipynb (cell 2-27)
ขั้นนี้ dynamic อยู่แล้วตั้งแต่ต้น: ชีตที่ไม่รู้จักแต่หน้าตาเหมือน Process จะถูกพับเข้า
ตาราง process ให้เอง ส่วนคอลัมน์/ชีตที่ไม่รู้จักจริงจะถูกรายงาน ไม่ทิ้งเงียบ
"""

from __future__ import annotations

import os
import re

import pandas as pd

from .gates import GateReport
from .paths import Paths

# ชื่อคอลัมน์ Stability เขียนไม่เหมือนกันข้ามเปเปอร์ -> map เข้าชุดเดียว
STABILITY_MAP = {
    "environment": "environment",
    "encapsulation": "encapsulation",
    "temperature (°c)": "temperature_c",
    "temperature (c)": "temperature_c",
    "temperature (celsius)": "temperature_c",
    "humidity (%)": "humidity_pct",
    "relative humidity (%)": "humidity_pct",
    "time (hr)": "time_hr",
    "retention (%)": "retention_pct",
    "normalize pce (%)": "retention_pct",
    "final normalize pce (%)": "retention_pct",
    "t80 (hr)": "t80_hr",
    "initial pce (%)": "initial_pce",
    "final pce (%)": "final_pce",
}

STABILITY_COLS = ["environment", "encapsulation", "temperature_c",
                  "humidity_pct", "time_hr", "retention_pct", "t80_hr",
                  "initial_pce", "final_pce"]

KNOWN_SHEETS = {"Layer", "Vender", "Materials", "Process", "Properties", "Stability"}

TABLE_ORDER = ["devices", "layers", "vendors", "materials",
               "process", "properties", "stability"]


def parse_device_id(path: str):
    """filename -> (device_id, doi_raw, sample_no)"""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"[_.]?[Ss]ample\s*(\d+)$", stem)
    sample_no = int(m.group(1)) if m else None
    doi_raw = stem[: m.start()] if m else stem
    return stem, doi_raw, sample_no


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    # pandas >= 3.0 อ่านคอลัมน์ข้อความเป็น dtype "str" ไม่ใช่ object
    # -> เช็ค is_string_dtype ด้วย ไม่งั้นการ trim ถูกข้ามเงียบ ๆ ("NiOx " != "NiOx")
    for c in df.columns:
        if pd.api.types.is_string_dtype(df[c]) or df[c].dtype == object:
            df[c] = (df[c].astype(str)
                     .str.replace(r"\s+", " ", regex=True)
                     .str.strip()
                     .replace({"nan": pd.NA, "": pd.NA}))
    return df


def _is_process_like(cols) -> bool:
    s = {str(c).strip() for c in cols}
    return {"StepOrder", "ProcessType"} <= s


def _cat(frames):
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(paths: Paths, gate: GateReport) -> dict[str, pd.DataFrame]:
    gate.stage = "stage1"
    files = [str(p) for p in paths.raw_workbooks()]
    if not gate.require(bool(files), "no-workbook", f"ไม่มีไฟล์ .xlsx ใน {paths.raw}"):
        return {}
    gate.note(f"อ่าน {len(files)} workbook จาก {paths.raw}")

    devices, layers, vendors, materials, process, properties, stability = (
        [], [], [], [], [], [], [])
    drift = []

    for f in files:
        device_id, doi_raw, sample_no = parse_device_id(f)
        devices.append({"device_id": device_id, "doi_raw": doi_raw,
                        "sample_no": sample_no, "source_file": os.path.basename(f)})
        xl = pd.ExcelFile(f)

        lay = _clean(pd.read_excel(f, "Layer")).dropna(how="all")
        lay.insert(0, "device_id", device_id)
        layers.append(lay)

        ven = _clean(pd.read_excel(f, "Vender")).dropna(how="all")
        ven.insert(0, "device_id", device_id)
        vendors.append(ven)

        mat = _clean(pd.read_excel(f, "Materials")).dropna(how="all")
        mat.insert(0, "device_id", device_id)
        materials.append(mat)

        # Process + ชีตอื่นที่หน้าตาเหมือน Process (เช่น "NiOx synthesis")
        # source_sheet เก็บที่มาไว้ ขั้น 3 จะกรองชีตสังเคราะห์ออกจาก knob ด้วยคอลัมน์นี้
        for sh in xl.sheet_names:
            if sh == "Process" or (sh not in KNOWN_SHEETS
                                   and _is_process_like(pd.read_excel(f, sh, nrows=0).columns)):
                pro = _clean(pd.read_excel(f, sh)).dropna(how="all")
                pro.insert(0, "source_sheet", sh)
                pro.insert(0, "device_id", device_id)
                process.append(pro)

        prp = _clean(pd.read_excel(f, "Properties")).dropna(how="all")
        prp.insert(0, "device_id", device_id)
        properties.append(prp)

        stb = pd.read_excel(f, "Stability")
        for c in stb.columns:
            if str(c).strip().lower() not in STABILITY_MAP:
                drift.append({"device_id": device_id, "ชนิด": "คอลัมน์ Stability ที่ไม่รู้จัก",
                              "ของที่เจอ": str(c).strip(), "ผล": "ถูกทิ้ง"})
        stb.columns = [STABILITY_MAP.get(str(c).strip().lower(), str(c).strip())
                       for c in stb.columns]
        stb = _clean(stb).dropna(how="all")
        stb.insert(0, "device_id", device_id)
        stability.append(stb)

        for sh in xl.sheet_names:
            if sh not in KNOWN_SHEETS and not _is_process_like(
                    pd.read_excel(f, sh, nrows=0).columns):
                drift.append({"device_id": device_id, "ชนิด": "ชีตที่ไม่รู้จัก",
                              "ของที่เจอ": sh, "ผล": "ไม่ถูกอ่าน"})

    if drift:
        gate.review("schema-drift",
                    f"เจอโครงชีต/คอลัมน์ที่กติกายังไม่รู้จัก {len(drift)} รายการ "
                    f"-- ข้อมูลส่วนนั้นไม่ได้เข้าระบบ",
                    pd.DataFrame(drift))

    devices = pd.DataFrame(devices)
    layers = _cat(layers).rename(columns={
        "LayerID": "layer_id", "LayerType": "layer_type",
        "Material": "material", "Thickness (nm)": "thickness_nm"})
    vendors = _cat(vendors).rename(columns={
        "Material": "material", "%Purity": "purity", "Vender": "vendor"})
    materials = _cat(materials).rename(columns={
        "LayerID": "layer_id", "Material": "material", "Type": "type",
        "Solvent": "solvent", "Amount": "amount", "Unit": "unit"})
    process = _cat(process).rename(columns={
        "LayerID": "layer_id", "StepOrder": "step_order",
        "ProcessType": "process_type", "Descriptions": "description",
        "Parameter": "parameter", "Unit": "unit", "Remarks": "note"})
    properties = _cat(properties).rename(columns={
        "Properties": "property", "Values": "value",
        "Errors": "error", "Unit": "unit", "Remarks": "remarks"})
    if "error" not in properties.columns:
        properties["error"] = pd.NA
    stability = _cat(stability).reindex(columns=["device_id"] + STABILITY_COLS)

    tables = {"devices": devices, "layers": layers, "vendors": vendors,
              "materials": materials, "process": process,
              "properties": properties, "stability": stability}

    gate.require(len(devices) == len(files), "device-count",
                 f"devices {len(devices)} แถว != workbook {len(files)} ไฟล์")
    gate.require(devices["device_id"].is_unique, "device-dup",
                 "device_id ซ้ำ -- ชื่อไฟล์ใน Raw Data ต้องไม่ซ้ำกัน")
    _check_doi_spelling(devices, gate)

    for name in TABLE_ORDER:
        gate.note(f"{name:12s} {tables[name].shape}")
    return tables


def _check_doi_spelling(devices: pd.DataFrame, gate: GateReport) -> None:
    """เปเปอร์ฉบับเดียวกันที่ชื่อไฟล์เขียน DOI ต่างกัน = ถูกนับเป็น 2 เปเปอร์

    DOI ใน repo นี้เขียนไม่สม่ำเสมอ (10.1002_solr.202400534 กับ 10_1002_solr_202400534
    กับ 10.1002admi.202500231 ที่ไม่มีตัวคั่น) ตัวคั่นและตัวพิมพ์ใหญ่เล็กไม่มีความหมายใน DOI
    -> เทียบด้วยคีย์ที่ตัดอักขระพิเศษออก ถ้าคีย์เดียวกันมีหลายการสะกด แปลว่า
       ข้อมูลชุดเดียวถูกใส่เข้ามาซ้ำ ซึ่งจะทำให้เปเปอร์นั้นมีน้ำหนักเป็น 2 เท่าในทุกการวิเคราะห์
    """
    d = devices.copy()
    d["doi_key"] = d["doi_raw"].str.lower().str.replace(r"[^0-9a-z]", "", regex=True)
    dup = d.groupby("doi_key")["doi_raw"].nunique()
    keys = dup[dup > 1].index
    if not len(keys):
        return
    rows = (d[d["doi_key"].isin(keys)]
            .sort_values(["doi_key", "device_id"])[["doi_key", "doi_raw", "device_id"]])
    gate.review("doi-spelling-dup",
                f"เปเปอร์ {len(keys)} ฉบับมีชื่อไฟล์ที่เขียน DOI ต่างกัน "
                f"รวม {len(rows)} device -- เปเปอร์พวกนี้จะถูกนับ 2 รอบ "
                f"ต้องลบชุดที่ซ้ำออกจาก Raw Data (pipeline ไม่ลบให้เอง)", rows)


def write(tables: dict[str, pd.DataFrame], paths: Paths) -> None:
    paths.normalize.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(paths.normalize / f"{name}.csv", index=False)
