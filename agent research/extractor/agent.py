"""Agent สกัดข้อมูล: paper text -> prompt -> LLM -> JSON ที่ validate แล้ว"""
from __future__ import annotations

import json
import re

from .llm import LLM
from .schema import FIELD_NAMES, json_spec

SYSTEM = """You are an expert in perovskite solar cells extracting device data from a paper.
Rules: report only values stated in the paper (text, tables, SI); never guess;
use null when absent; one entry per distinct device group; return JSON only."""


def build_prompt(paper_text: str) -> str:
    return f"Extract every device group using exactly this JSON shape:\n{json_spec()}\n\nPAPER:\n{paper_text}"


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)       # ทนกรณี model ใส่ ```json ครอบ
    return json.loads(m.group(0)) if m else {"devices": []}


def validate(obj: dict) -> list[dict]:
    """ตัดฟิลด์แปลกปลอม เติมฟิลด์ที่ขาดเป็น None"""
    out = []
    for d in obj.get("devices", []):
        row = {k: d.get(k) for k in FIELD_NAMES}
        row["label"] = d.get("label", "")
        out.append(row)
    return out


def extract_paper(llm: LLM, doi_raw: str, paper_text: str) -> list[dict]:
    raw = llm.complete(SYSTEM, build_prompt(paper_text), meta={"doi_raw": doi_raw})
    # TODO: retry/repair เมื่อ parse พัง, เก็บ raw ไว้ดีบัก
    return validate(parse_json(raw))
