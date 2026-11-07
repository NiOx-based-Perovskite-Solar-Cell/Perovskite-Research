"""เทียบผลสกัดกับค่าที่คนกรอก: จับคู่อุปกรณ์ -> ตัดสินทีละฟิลด์ -> สรุปความตรง"""
from __future__ import annotations

import math
import re

import pandas as pd

from .schema import FIELDS, Field


def _norm_str(s) -> str:
    return re.sub(r"[^a-z0-9.]", "", str(s).lower()) if s is not None and not _isnan(s) else ""


def _isnan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def judge(f: Field, truth, pred) -> str:
    """คืน 'match' | 'mismatch' | 'missed' (คนกรอกมี agent ไม่เจอ) | 'extra' (agent เจอ คนไม่ได้กรอก) | 'both_empty'"""
    t_empty = truth is None or _isnan(truth) or truth == ""
    p_empty = pred is None or _isnan(pred) or pred == ""
    if t_empty and p_empty:
        return "both_empty"
    if t_empty:
        return "extra"
    if p_empty:
        return "missed"
    if f.kind == "num":
        t, p = float(truth), float(pred)
        return "match" if abs(t - p) <= max(f.abs_tol, f.rel_tol * abs(t)) else "mismatch"
    return "match" if _norm_str(truth) == _norm_str(pred) else "mismatch"


def match_devices(truth_rows: pd.DataFrame, preds: list[dict]) -> list[tuple]:
    """จับคู่แบบโลภด้วยระยะห่าง PCE. TODO: ใช้ Hungarian + ชื่อวัสดุ/label ช่วย
    คืน [(truth_row|None, pred|None)] — ไม่มีคู่ = ตกหล่น/เกิน"""
    pairs, free = [], list(range(len(preds)))
    for _, tr in truth_rows.iterrows():
        best, bd = None, 1e9
        for i in free:
            pp, tp = preds[i].get("pce"), tr.get("pce")
            d = abs(float(pp) - float(tp)) if pp is not None and not _isnan(tp) else 1e8
            if d < bd:
                best, bd = i, d
        if best is not None:
            free.remove(best)
            pairs.append((tr, preds[best]))
        else:
            pairs.append((tr, None))
    pairs += [(None, preds[i]) for i in free]
    return pairs


def compare_paper(truth_rows: pd.DataFrame, preds: list[dict]) -> pd.DataFrame:
    recs = []
    for tr, pr in match_devices(truth_rows, preds):
        for f in FIELDS:
            recs.append({
                "device_id": None if tr is None else tr["device_id"],
                "label": None if pr is None else pr.get("label"),
                "field": f.name,
                "truth": None if tr is None else tr.get(f.name),
                "pred": None if pr is None else pr.get(f.name),
                "verdict": judge(f, None if tr is None else tr.get(f.name), None if pr is None else pr.get(f.name)),
            })
    return pd.DataFrame(recs)


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    """ต่อฟิลด์: precision/recall/accuracy ของค่าที่ 'คนกรอกไว้'
    accuracy = match / (match+mismatch+missed) — สัดส่วนที่คนกรอกแล้ว agent ได้ตรง"""
    d = detail[detail.verdict != "both_empty"]
    g = d.groupby(["field", "verdict"]).size().unstack(fill_value=0)
    for c in ["match", "mismatch", "missed", "extra"]:
        if c not in g:
            g[c] = 0
    g["accuracy"] = g["match"] / (g["match"] + g["mismatch"] + g["missed"]).clip(lower=1)
    return g[["match", "mismatch", "missed", "extra", "accuracy"]].round(3)
