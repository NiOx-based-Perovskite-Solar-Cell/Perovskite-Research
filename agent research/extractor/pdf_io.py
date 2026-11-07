"""อ่านข้อความจาก PDF + แมป DOI -> ไฟล์ PDF

ชื่อไฟล์ใน `original paper/` ไม่ใช่ DOI จึงต้องมี manifest (papers_manifest.csv)
ที่คอลัมน์ pdf_file ต้องกรอกเอง (หรือเขียนตัวเดาชื่อเอาทีหลัง)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PAPER_DIR = ROOT / "original paper"
MANIFEST = Path(__file__).resolve().parents[1] / "papers_manifest.csv"


def load_manifest() -> pd.DataFrame:
    return pd.read_csv(MANIFEST, dtype=str).fillna("")


def pdf_path(doi_raw: str) -> Path | None:
    m = load_manifest()
    row = m[m["doi_raw"] == doi_raw]
    if row.empty or not row.iloc[0]["pdf_file"]:
        return None
    return PAPER_DIR / row.iloc[0]["pdf_file"]


def read_pdf_text(path: Path, max_chars: int = 120_000) -> str:
    """TODO(จริง): ใช้ PyMuPDF (pip install pymupdf) / หรือส่ง PDF เข้า model ตรง ๆ
    ตารางและ SI คือจุดที่ข้อความดิบพังบ่อย ควรประเมินแยก"""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise RuntimeError("ติดตั้ง pymupdf ก่อน: pip install pymupdf") from e
    with fitz.open(path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    return text[:max_chars]
