#!/usr/bin/env python3
"""Pull The Perovskite Database (NOMAD) devices that match the project filter, and flatten them to one CSV.

Filter (same as notebooks/nomad_lab/compare_.ipynb, the 12 Oct 2026 snapshot):
    1. data.perovskite.composition_a_ions = "Cs; FA; MA"                         (server side)
    2. results...solar_cell.hole_transport_layer any of NiOx, NiO-c, NiO-np         (server side)
    3. data.cell.architecture = "pin"                                             (server side)
    4. has Efficiency, or T80, or both PCE initial and PCE end of experiment       (column `pass4`)

    python scripts/fetch_nomad.py                      # download today's snapshot, then flatten it
    python scripts/fetch_nomad.py --from-cache FILE    # flatten an earlier snapshot again, no download

Writes (nothing is overwritten except files carrying today's date):
    notebooks/nomad_lab/cache/nomad_csfama_nio_pin_<YYYYMMDD>.json   raw archives, as the API returned them
    notebooks/nomad_lab/cache/nomad_all_dois_<YYYYMMDD>.json         every DOI in NOMAD with its device count
    processed/nomad/nomad_csfama_nio_pin_<YYYYMMDD>.csv              one row per device, every field
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "notebooks/nomad_lab/cache"
OUT = ROOT / "processed/nomad"
API = "https://nomad-lab.eu/prod/v1/api/v1"
SCHEMA = "perovskite_solar_cell_database.schema.PerovskiteSolarCell"
SC = "results.properties.optoelectronic.solar_cell"
FILTER_1_3 = {
    "section_defs.definition_qualified_name:all": [SCHEMA],
    f"data.perovskite.composition_a_ions#{SCHEMA}": "Cs; FA; MA",
    f"{SC}.hole_transport_layer:any": ["NiOx", "NiO-c", "NiO-np"],
    f"data.cell.architecture#{SCHEMA}": "pin",
}
SECTIONS = ["ref", "cell", "substrate", "htl", "perovskite", "perovskite_deposition", "etl",
            "backcontact", "add", "encapsulation", "jv", "stabilised", "eqe", "stability"]
REQUIRED = {"metadata": {"entry_id": "*", "upload_id": "*"},
            "data": {s: "*" for s in SECTIONS},
            "results": {"properties": {"optoelectronic": "*"}}}


# ------------------------------------------------------------------ download
def _session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=8, backoff_factor=3, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"], respect_retry_after_header=True)))
    return s


def download_archives(session, page_size: int = 25) -> list[dict]:
    body = {"owner": "visible", "query": FILTER_1_3, "required": REQUIRED, "pagination": {"page_size": page_size}}
    out = []
    while True:
        r = session.post(f"{API}/entries/archive/query", json=body, timeout=300)
        r.raise_for_status()
        j = r.json()
        out += [d["archive"] for d in j["data"]]
        after = j["pagination"].get("next_page_after_value")
        print(f"  archives: {len(out)} / {j['pagination'].get('total')}", flush=True)
        if not after or not j["data"]:
            return out
        body["pagination"]["page_after_value"] = after


def all_dois(session) -> list[list]:
    rows, after = [], None
    while True:
        pag = {"page_size": 10000, **({"page_after_value": after} if after else {})}
        body = {"owner": "visible", "query": {"section_defs.definition_qualified_name:all": [SCHEMA]},
                "pagination": {"page_size": 0},
                "aggregations": {"doi": {"terms": {"quantity": f"data.ref.DOI_number#{SCHEMA}", "pagination": pag}}}}
        r = session.post(f"{API}/entries/query", json=body, timeout=300)
        r.raise_for_status()
        t = r.json()["aggregations"]["doi"]["terms"]
        rows += [[b["value"], b["count"]] for b in t["data"]]
        after = t.get("pagination", {}).get("next_page_after_value")
        if not after or not t["data"]:
            return rows


# ------------------------------------------------------------------ flatten
MISSING = {"", "unknown", "nan", "none", "n/a", "na"}


def known(v) -> bool:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    if isinstance(v, (bool, int, float)):
        return True
    return any(p.strip().lower() not in MISSING for p in re.split(r"\||;|>>", str(v)))


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def doi_key(s) -> str:
    s = str(s).replace("﻿", "").strip().lower()
    m = re.search(r"10[._]\d{4,9}.*", s)
    return re.sub(r"[^0-9a-z]", "", m.group(0) if m else s)


def _leaves(d: dict, prefix: str, out: dict) -> None:
    for k, v in d.items():
        if k.startswith("m_"):
            continue
        key = f"{prefix}.{k}"
        if isinstance(v, dict):
            _leaves(v, key, out)
        elif isinstance(v, list):
            out[key] = " | ".join(str(x) for x in v)
        else:
            out[key] = v


def br_fraction(c_ions, c_coef, long_form) -> float:
    """X-site Br fraction: from the structured ion/coefficient fields, else from the long-form formula."""
    ions = [p.strip() for p in str(c_ions or "").split(";") if p.strip()]
    coefs = [num(p) for p in str(c_coef or "").split(";") if p.strip()]
    if ions and len(ions) == len(coefs) and all(not math.isnan(c) for c in coefs) and sum(coefs) > 0:
        return sum(c for i, c in zip(ions, coefs) if i == "Br") / sum(coefs)
    m = re.search(r"Pb[\d.]*((?:(?:Br|Cl|I|F)\d*\.?\d*)+)$", str(long_form or ""))
    if not m:
        return math.nan
    parts = re.findall(r"(Br|Cl|I|F)(\d*\.?\d*)", m.group(1))
    tot = sum(float(c) if c else 1.0 for _, c in parts)
    return sum(float(c) if c else 1.0 for h, c in parts if h == "Br") / tot if tot else math.nan


def flatten(archives: list[dict], ours_keys: set[str]) -> pd.DataFrame:
    rows = []
    for a in archives:
        row = {"entry_id": a["metadata"]["entry_id"], "upload_id": a["metadata"].get("upload_id")}
        _leaves({k: v for k, v in a["data"].items() if isinstance(v, dict)}, "data", row)
        sc = (((a.get("results") or {}).get("properties") or {}).get("optoelectronic") or {}).get("solar_cell") or {}
        _leaves(sc, "sc", row)
        rows.append(row)
    df = pd.DataFrame(rows)
    col = lambda c: df[c] if c in df else pd.Series([None] * len(df))  # noqa: E731
    lead = pd.DataFrame({
        "doi": col("data.ref.DOI_number"),
        "doi_key": col("data.ref.DOI_number").map(doi_key),
        "method": col("data.ref.extraction_method").fillna("manual"),
        "year": pd.to_datetime(col("data.ref.publication_date"), errors="coerce", utc=True).dt.year,
        "composition": col("data.perovskite.composition_long_form"),
        "br_frac": [br_fraction(c, k, f) for c, k, f in zip(col("data.perovskite.composition_c_ions"),
                                                             col("data.perovskite.composition_c_ions_coefficients"),
                                                             col("data.perovskite.composition_long_form"))],
        "htl_stack": col("data.htl.stack_sequence"),
        "pce": col("sc.efficiency").map(num),
        "voc_V": col("sc.open_circuit_voltage").map(num),
        "jsc_mAcm2": col("sc.short_circuit_current_density").map(num) / 10,  # NOMAD stores A/m2
        "ff_pct": col("sc.fill_factor").map(num) * 100,
        "t80_h": col("data.stability.PCE_T80").map(num),
        "pce_init": col("data.stability.PCE_initial_value").map(num),
        "pce_end": col("data.stability.PCE_end_of_experiment").map(num),
        "stab_time_h": col("data.stability.time_total_exposure").map(num),
    })
    lead["pass4"] = lead.pce.notna() | lead.t80_h.notna() | (lead.pce_init.notna() & lead.pce_end.notna())
    lead["has_stab"] = lead.t80_h.notna() | (lead.pce_init.notna() & lead.pce_end.notna())
    lead["in_ours"] = lead.doi_key.isin(ours_keys)
    return pd.concat([df[["entry_id", "upload_id"]], lead, df.drop(columns=["entry_id", "upload_id"])], axis=1)


def ours_doi_keys() -> set[str]:
    dev = pd.read_csv(ROOT / "processed/prepared_v2/devices.csv")
    return set(dev["doi_raw"].map(doi_key))


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-cache", type=Path, help="flatten this snapshot JSON instead of downloading")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.from_cache:
        snap = json.loads(args.from_cache.read_text())
        stamp = snap["downloaded_at"][:10].replace("-", "")
    else:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%d")
        s = _session()
        print("downloading filtered archives (conditions 1-3) ...", flush=True)
        snap = {"downloaded_at": now.isoformat(timespec="seconds"), "query": FILTER_1_3, "items": download_archives(s)}
        (CACHE / f"nomad_csfama_nio_pin_{stamp}.json").write_text(json.dumps(snap))
        print("downloading the DOI list of the whole database ...", flush=True)
        dois = {"downloaded_at": snap["downloaded_at"], "items": all_dois(s)}
        (CACHE / f"nomad_all_dois_{stamp}.json").write_text(json.dumps(dois))
        print(f"whole database: {sum(c for _, c in dois['items']):,} devices with a DOI, {len(dois['items']):,} DOIs")

    df = flatten(snap["items"], ours_doi_keys())
    path = OUT / f"nomad_csfama_nio_pin_{stamp}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    f = df[df.pass4]
    print(f"snapshot {snap['downloaded_at']}: conditions 1-3 {len(df)} devices / {df.doi_key.nunique()} papers; "
          f"condition 4 {len(f)} devices / {f.doi_key.nunique()} papers "
          f"(manual {int((f.method == 'manual').sum())} dev / {f[f.method == 'manual'].doi_key.nunique()} papers, "
          f"LLM {int((f.method != 'manual').sum())} dev / {f[f.method != 'manual'].doi_key.nunique()} papers); "
          f"papers shared with ours {f[f.in_ours].doi_key.nunique()}")
    print(f"wrote {path.relative_to(ROOT)} ({df.shape[0]} rows x {df.shape[1]} columns)")


if __name__ == "__main__":
    main()
