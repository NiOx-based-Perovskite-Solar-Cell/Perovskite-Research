"""Builders for small workbooks and PDFs used by the tests."""
from __future__ import annotations

from pathlib import Path

import fitz
import openpyxl

LAYER = [("LayerID", "LayerType", "Material", "Thickness (nm)"),
         (1, "Anode", "ITO", None),
         (2, "HTL", "NiOx", 20),
         (3, "PSK treatment", None, None)]
PROCESS = [("LayerID", "StepOrder", "ProcessType", "Descriptions", "Parameter", "Unit"),
           (1, 1, "Wash", "DI water, IPA", 20, "min"),
           (2, 1, "SpinCoating", None, "3000, 40", "RPM, s")]
PROPERTIES = [("Properties", "Values", "Errors", "Unit", "Remarks"),
              ("Champion PCE", 22.93, None, "%", "Figure 4b"),
              ("Champion Voc", 1.124, None, "V", None),
              ("Champion Jsc", 24.01, None, "mA cm-2", None),
              ("Champion FF", 84.98, None, "%", None),
              ("Average PCE", None, None, "%", None)]
STABILITY = [("Environment", "Encapsulation", "Temperature (°C)", "Humidity (%)", "Time (hr)", "Retention (%)"),
             ("Ambient air storage", "N", 25, "50-60", 1000, 85)]
NOTES = [("Item", "Value"), ("Glovebox O2", "<0.1 ppm")]  # unknown sheet: gets a call of its own


def write_workbook(path: Path, sheets: dict[str, list[tuple]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(list(r))
    wb.save(path)
    return path


def write_pdf(path: Path, text: str, *, image: bool = True, pages: int = 2, page_texts: list[str] | None = None) -> Path:
    """Page 1 gets ``text``; later pages get ``page_texts[i - 1]`` or a filler sentence."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        body = text if i == 0 else (page_texts[i - 1] if page_texts and i - 1 < len(page_texts) else f"Page {i + 1} body text.")
        page.insert_textbox(fitz.Rect(72, 72, 540, 760), body, fontsize=10)
        if image and i == 0:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), 0)
            pix.clear_with(200)
            page.insert_image(fitz.Rect(72, 200, 172, 300), pixmap=pix)
    doc.save(path)
    return path
