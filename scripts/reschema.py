"""
reschema.py — ETL: Perovskite raw .xlsx workbooks -> normalized relational dataset.

Each workbook in `Raw Data/` is ONE device (solar cell sample) and is named
`<doi>_Sample<N>.xlsx`. Every workbook holds 6 sheets:
    Layer, Vender (vendor), Materials, Process, Properties, Stability

This script flattens all workbooks into tidy, normalized tables linked by a
single key `device_id` (the filename stem). It writes three representations:
    processed/csv/*.csv        tidy long tables (analysis / version control)
    processed/perovskite.sqlite   relational DB with foreign keys (SQL queries)
    processed/json/<device>.json  nested per-device document (archival / sharing)

Run:  python scripts/reschema.py
"""
from __future__ import annotations
import glob
import json
import os
import re
import sqlite3
import pandas as pd

RAW_DIR = "Raw Data"
OUT_DIR = "processed"

# --- canonical column maps -------------------------------------------------
# Stability headers vary in casing/wording across papers; map them to one set.
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

# The 6 sheets every workbook is expected to have. Any *extra* sheet whose
# header looks like Process steps (has StepOrder + ProcessType) is treated as
# additional process/synthesis steps and folded into the `process` table
# (this project is process-centric). Anything else triggers a drift warning
# so new data is never silently dropped.
KNOWN_SHEETS = {"Layer", "Vender", "Materials", "Process",
                "Properties", "Stability"}


def parse_device_id(path: str):
    """filename -> (device_id, doi_raw, sample_no)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"[_.]?[Ss]ample\s*(\d+)$", stem)
    sample_no = int(m.group(1)) if m else None
    doi_raw = stem[: m.start()] if m else stem
    return stem, doi_raw, sample_no


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    # normalize whitespace/newlines inside string cells
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = (df[c].astype(str)
                          .str.replace(r"\s+", " ", regex=True)
                          .str.strip()
                          .replace({"nan": pd.NA, "": pd.NA}))
    return df


def _is_process_like(cols) -> bool:
    s = {str(c).strip() for c in cols}
    return {"StepOrder", "ProcessType"} <= s


def build_tables(warn=True):
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.xlsx")))
    devices, layers, vendors, materials, process, properties, stability = (
        [], [], [], [], [], [], [])
    warnings = []

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

        # Process + any extra process-like sheet (e.g. "NiOx synthesis").
        # `source_sheet` keeps provenance so folded-in steps stay traceable.
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
                warnings.append(f"{device_id} [Stability]: unmapped column "
                                f"'{str(c).strip()}' (dropped)")
        stb.columns = [STABILITY_MAP.get(str(c).strip().lower(), str(c).strip())
                       for c in stb.columns]
        stb = _clean(stb).dropna(how="all")
        stb.insert(0, "device_id", device_id)
        stability.append(stb)

        # flag any sheet we neither know nor recognized as process-like
        for sh in xl.sheet_names:
            if sh not in KNOWN_SHEETS and not _is_process_like(
                    pd.read_excel(f, sh, nrows=0).columns):
                warnings.append(f"{device_id}: unknown sheet '{sh}' (ignored)")

    if warn and warnings:
        print("⚠️  SCHEMA DRIFT (data preserved where possible):")
        for w in warnings:
            print("   -", w)

    def cat(frames):
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    devices = pd.DataFrame(devices)
    layers = cat(layers).rename(columns={
        "LayerID": "layer_id", "LayerType": "layer_type",
        "Material": "material", "Thickness (nm)": "thickness_nm"})
    vendors = cat(vendors).rename(columns={
        "Material": "material", "%Purity": "purity", "Vender": "vendor"})
    materials = cat(materials).rename(columns={
        "LayerID": "layer_id", "Material": "material", "Type": "type",
        "Solvent": "solvent", "Amount": "amount", "Unit": "unit"})
    process = cat(process).rename(columns={
        "LayerID": "layer_id", "StepOrder": "step_order",
        "ProcessType": "process_type", "Descriptions": "description",
        "Parameter": "parameter", "Unit": "unit", "Remarks": "note"})
    properties = cat(properties).rename(columns={
        "Properties": "property", "Values": "value",
        "Errors": "error", "Unit": "unit", "Remarks": "remarks"})
    if "error" not in properties.columns:
        properties["error"] = pd.NA
    stability = cat(stability).reindex(
        columns=["device_id"] + STABILITY_COLS)

    return {"devices": devices, "layers": layers, "vendors": vendors,
            "materials": materials, "process": process,
            "properties": properties, "stability": stability}


def write_csv(tables):
    d = os.path.join(OUT_DIR, "csv")
    os.makedirs(d, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(os.path.join(d, f"{name}.csv"), index=False)


def write_sqlite(tables):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "perovskite.sqlite")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    for name, df in tables.items():
        df.to_sql(name, con, if_exists="replace", index=False)
    con.commit()
    con.close()
    return path


def write_json(tables):
    d = os.path.join(OUT_DIR, "json")
    os.makedirs(d, exist_ok=True)
    for _, dev in tables["devices"].iterrows():
        did = dev["device_id"]
        doc = {"device_id": did, "doi_raw": dev["doi_raw"],
               "sample_no": dev["sample_no"], "source_file": dev["source_file"]}
        for t in ["layers", "vendors", "materials", "process",
                  "properties", "stability"]:
            sub = tables[t][tables[t]["device_id"] == did].drop(columns=["device_id"])
            doc[t] = json.loads(sub.to_json(orient="records"))
        with open(os.path.join(d, f"{did}.json"), "w") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)


# --- ML-ready feature table -----------------------------------------------
# The recommended downstream format: ONE row per device (sample), saved as CSV.
# This is what pandas.read_csv() -> scikit-learn / PyTorch consume directly.
# It is BUILT FROM the normalized long tables (the source of truth) so it can
# always be regenerated / extended with new features.

def _num(series: pd.Series) -> pd.Series:
    """Coerce a column of mixed strings to float (strip units like '%', units)."""
    return pd.to_numeric(
        series.astype(str).str.extract(r"(-?\d+\.?\d*)")[0], errors="coerce")


def _canonical_metric(name: str):
    """Map a free-text Property name to a canonical ML feature, else None."""
    n = str(name).lower()
    if "pce" in n:
        return "pce"
    if "voc" in n:
        return "voc"
    if "jsc" in n:
        return "jsc"
    if re.search(r"\bff\b", n) or n.endswith(" ff") or "fill factor" in n:
        return "ff"
    if "band gap" in n or "bandgap" in n or "energy gap" in n:
        return "bandgap"
    return None


def build_ml_table(tables: dict) -> pd.DataFrame:
    """One row per device: identifiers + structural + process + JV features."""
    feat = tables["devices"][["device_id", "doi_raw", "sample_no"]].copy()

    # --- structural features from the layer stack ---
    lay = tables["layers"]
    stack = (lay.dropna(subset=["material"])
                .groupby("device_id")
                .agg(n_layers=("layer_id", "nunique")))
    # one column per standard functional layer -> its material
    types = {"HTL": "htl_material", "ETL": "etl_material",
             "PSK": "perovskite", "Anode": "anode", "Cathode": "cathode"}
    for ltype, col in types.items():
        m = (lay[lay["layer_type"] == ltype]
             .dropna(subset=["material"])
             .groupby("device_id")["material"].first().rename(col))
        stack = stack.join(m)
    feat = feat.merge(stack, on="device_id", how="left")

    # --- process features (this project is process-centric) ---
    pr = tables["process"]
    ptype = pr["process_type"].fillna("").str.lower()
    proc = pr.assign(_pt=ptype).groupby("device_id").agg(
        n_process_steps=("step_order", "count"))
    proc["n_spin_coating"] = (pr.assign(_pt=ptype)[ptype.str.contains("spin")]
                              .groupby("device_id").size())
    proc["has_antisolvent"] = (pr.assign(_pt=ptype)[ptype.str.contains("antisolvent")]
                               .groupby("device_id").size().gt(0))
    feat = feat.merge(proc, on="device_id", how="left")
    feat["n_spin_coating"] = feat["n_spin_coating"].fillna(0).astype(int)
    feat["has_antisolvent"] = feat["has_antisolvent"].fillna(False)

    # --- JV / target metrics: pivot canonical properties wide (champion = max) ---
    p = tables["properties"].copy()
    p["metric"] = p["property"].map(_canonical_metric)
    p["val"] = _num(p["value"])
    jv = (p.dropna(subset=["metric", "val"])
            .pivot_table(index="device_id", columns="metric",
                         values="val", aggfunc="max"))
    jv.columns = [f"{c}_max" for c in jv.columns]
    feat = feat.merge(jv, on="device_id", how="left")

    return feat


def write_ml_table(tables: dict) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "ml_dataset.csv")
    build_ml_table(tables).to_csv(path, index=False)
    return path


if __name__ == "__main__":
    tables = build_tables()

    # PRIMARY, recommended output for ML/DL: one model-ready CSV (1 row/device).
    ml_path = write_ml_table(tables)

    # Source-of-truth tidy tables (still CSV) so features can be regenerated.
    write_csv(tables)

    print(f"{len(tables['devices'])} devices, "
          f"{tables['devices']['doi_raw'].nunique()} publications\n")
    print("normalized tables:")
    for name, df in tables.items():
        print(f"  {name:12s} {df.shape}")
    ml = build_ml_table(tables)
    print(f"\nML table   -> {ml_path}   shape={ml.shape}")
    print("columns:", list(ml.columns))

    # Optional alternative representations (not the recommended path):
    #   write_sqlite(tables)   # relational DB, for ad-hoc SQL joins
    #   write_json(tables)     # nested per-device documents, for archival
