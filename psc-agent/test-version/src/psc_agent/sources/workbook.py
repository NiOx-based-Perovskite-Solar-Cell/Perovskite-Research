"""Hand-entered device workbooks: one .xlsx per device, named ``<doi>_Sample<N>.xlsx``."""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import openpyxl

Source = Union[str, os.PathLike, bytes]

# Columns that identify a row. They are shown to the model; every other column is hidden,
# re-extracted from the paper and compared.
KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "Layer": ("LayerID", "LayerType", "Material"),
    "Vender": ("Material",),
    "Materials": ("LayerID", "Material", "Type"),
    "Process": ("LayerID", "StepOrder", "ProcessType"),
    "Properties": ("Properties",),
    "Stability": ("Environment",),
}
ALWAYS_KEY = ("Remarks",)  # curator notes (conditions, figure refs), not a paper value
UNIT_COLUMN = "Unit"
UNIT_PAIRED = ("Amount", "Parameter", "Values", "Errors")  # their unit lives in the Unit column

# The layout new workbooks are written in (the most common headers in Raw Data).
STANDARD_HEADERS: dict[str, tuple[str, ...]] = {
    "Layer": ("LayerID", "LayerType", "Material", "Thickness (nm)"),
    "Vender": ("Material", "%Purity", "Vender"),
    "Materials": ("LayerID", "Material", "Type", "Solvent", "Amount", "Unit"),
    "Process": ("LayerID", "StepOrder", "ProcessType", "Descriptions", "Parameter", "Unit"),
    "Properties": ("Properties", "Values", "Errors", "Unit", "Remarks"),
    "Stability": ("Environment", "Encapsulation", "Temperature (°C)", "Humidity (%)", "Time (hr)", "Retention (%)",
                  "T80 (hr)"),
}
_PLAIN_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE]-?\d+)?$")

_SAMPLE = re.compile(r"[_.]?[Ss]ample\s*(\d+)$")
_HEADER_UNIT = re.compile(r"\(([^()]+)\)\s*$")


def parse_device_name(filename: str) -> tuple[str, str, int | None]:
    """``10.1002_x.123_Sample2.xlsx`` -> (stem, doi_raw, sample_no)."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _SAMPLE.search(stem)
    return stem, (stem[: m.start()] if m else stem), (int(m.group(1)) if m else None)


def header_unit(column: str) -> str | None:
    """Unit written in a header: ``Thickness (nm)`` -> nm, ``%Purity`` -> %."""
    m = _HEADER_UNIT.search(column)
    if m:
        return m.group(1).strip()
    return "%" if column.startswith("%") else None


def is_process_like(columns) -> bool:
    return {"StepOrder", "ProcessType"} <= set(columns)


@dataclass
class Row:
    sheet: str
    excel_row: int  # 1-based row number as Excel shows it
    cells: dict[str, object]  # header -> value; blanks are None
    key_columns: tuple[str, ...]

    @property
    def keys(self) -> dict[str, object]:
        return {c: self.cells[c] for c in self.key_columns if self.cells.get(c) is not None}

    @property
    def value_columns(self) -> list[str]:
        """Columns re-extracted from the paper. The Unit column travels with its value column."""
        return [c for c in self.cells if c not in self.key_columns and c != UNIT_COLUMN]

    def unit_of(self, column: str, cells: dict | None = None) -> str | None:
        """Unit of ``column`` from this row's Unit column, or from the header.

        ``cells`` replaces the recorded cells, e.g. with the values re-extracted from the paper."""
        cells = self.cells if cells is None else cells
        if column in UNIT_PAIRED and UNIT_COLUMN in self.cells:
            u = cells.get(UNIT_COLUMN)
            return None if u is None else str(u)
        return header_unit(column)


@dataclass
class Device:
    name: str  # workbook stem
    doi_raw: str
    sample_no: int | None
    sheets: dict[str, list[Row]]

    def layer_stack(self) -> str:
        return " / ".join(
            f"{r.cells.get('LayerType')}: {r.cells['Material']}"
            for r in self.sheets.get("Layer", [])
            if r.cells.get("Material") is not None
        )


def load_workbook(src: Source, name: str | None = None) -> Device:
    """Read one device workbook from a path or raw bytes (``name`` = file name, required for bytes)."""
    if isinstance(src, bytes):
        if not name:
            raise ValueError("name (the workbook file name) is required when loading from bytes")
        wb = openpyxl.load_workbook(io.BytesIO(src), data_only=True, read_only=True)
    else:
        name = name or os.path.basename(src)
        wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
    try:
        sheets = {ws.title.strip(): _read_sheet(ws.title.strip(), ws) for ws in wb.worksheets}
    finally:
        wb.close()
    stem, doi_raw, sample_no = parse_device_name(name)
    return Device(stem, doi_raw, sample_no, {k: v for k, v in sheets.items() if v})


def _read_sheet(title: str, ws) -> list[Row]:
    it = ws.iter_rows(min_row=1, values_only=True)
    header = next(it, None) or ()
    cols = [(i, str(h).strip()) for i, h in enumerate(header) if h is not None and str(h).strip()]
    names = [c for _, c in cols]
    if title in KEY_COLUMNS:
        base = KEY_COLUMNS[title]
    elif is_process_like(names):
        base = KEY_COLUMNS["Process"]
    else:
        base = tuple(names[:1])
    keys = tuple(c for c in names if c in base or c in ALWAYS_KEY)
    rows = []
    for n, values in enumerate(it, start=2):
        cells = {c: _clean(values[i] if i < len(values) else None) for i, c in cols}
        if any(v is not None for v in cells.values()):
            rows.append(Row(title, n, cells, keys))
    return rows


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def write_workbook(sheets: dict[str, list[dict]], path) -> Path:
    """Write one device workbook in the STANDARD_HEADERS layout. Plain numbers become numbers, like the
    hand-entered files; anything else ("3000, 30", "50-60") stays text."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, header in STANDARD_HEADERS.items():
        ws = wb.create_sheet(name)
        ws.append(list(header))
        for row in sheets.get(name, []):
            ws.append([_cell_out(row.get(h)) for h in header])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _cell_out(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str) and _PLAIN_NUMBER.match(v.strip()):
        f = float(v)
        return int(f) if f.is_integer() and "e" not in v.lower() else f
    return v
