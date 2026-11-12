"""What the recheck model sees and must return: task text, JSON schemas, answer types.
The instructions themselves are skills (skills/recheck-task, evidence-rules, ...), not code."""
from __future__ import annotations

import json

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..core.evidence import EvidenceRow
from ..sources.workbook import UNIT_COLUMN, Device, Row

COLUMN_HINTS = {
    "Thickness (nm)": "layer thickness in nm",
    "%Purity": "purity in %",
    "Vender": "supplier / manufacturer",
    "Solvent": "solvent or mixture with ratio, e.g. DMF:DMSO (4:1 v/v)",
    "Amount": "amount or concentration of this material; number(s) only",
    "Unit": "unit(s) of this row's numbers as the paper writes them",
    "Descriptions": "short description of what the step does",
    "Parameter": 'the step\'s numeric settings, e.g. spin coating "4000, 30", annealing "100, 10"',
    "Values": "the reported value; number only",
    "Errors": "the reported +/- spread or standard deviation; number only",
    "Encapsulation": "Y or N",
}


def row_id(ref: str, row: Row) -> str:
    return f"{ref}:{row.sheet}:{row.excel_row}"


def fill_columns(row: Row) -> list[str]:
    return row.value_columns + ([UNIT_COLUMN] if UNIT_COLUMN in row.cells else [])


def row_keys(row: Row) -> str:
    return " | ".join(f"{k}={v}" for k, v in row.keys.items())


def extraction_task(refs: dict[str, Device], rows: list[tuple[str, Row]]) -> tuple[str, list[str]]:
    """Task text listing devices and rows (values hidden), plus every column the answer may fill."""
    lines = ["DEVICES (all from this paper; tell them apart by their layer stacks; if two look identical, "
             "say so in paper_device):"]
    lines += [f"{ref} = {d.name} | {d.layer_stack() or 'layer stack not recorded'}" for ref, d in refs.items()]
    columns: list[str] = []
    sheets: dict[str, list[tuple[str, Row]]] = {}
    for ref, row in rows:
        sheets.setdefault(row.sheet, []).append((ref, row))
    for sheet, items in sheets.items():
        cols = list(dict.fromkeys(c for _, r in items for c in fill_columns(r)))
        columns += [c for c in cols if c not in columns]
        keys = list(dict.fromkeys(c for _, r in items for c in r.key_columns))
        lines += ["", f'SHEET "{sheet}" | key columns: {", ".join(keys)} | fill: {", ".join(cols)}']
        lines += [f"  {c}: {COLUMN_HINTS[c]}" for c in cols if c in COLUMN_HINTS]
        lines.append("ROWS:")
        lines += [f"{row_id(ref, r)} | {row_keys(r)}" for ref, r in items]
    lines += ["", "Return JSON: devices = one {device_id, paper_device} per device above (paper_device = how the paper "
              "names or describes it); rows = one entry per row_id above with found, values (one {column, value} "
              "for each fill column of its sheet), page, source, evidence, note, key_issue."]
    return "\n".join(lines), columns


def _nullable(t: str) -> dict:
    return {"type": [t, "null"]}


def extraction_schema(columns: list[str]) -> dict:
    row = {
        "type": "object",
        "additionalProperties": False,
        "required": ["row_id", "found", "values", "page", "source", "evidence", "note", "key_issue"],
        "properties": {
            "row_id": {"type": "string"},
            "found": {"type": "boolean"},
            "values": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["column", "value"],
                "properties": {"column": {"type": "string", "enum": columns}, "value": _nullable("string")}}},
            "page": _nullable("integer"),
            "source": {"type": "string", "enum": ["text", "table", "figure", "none"]},
            "evidence": _nullable("string"),
            "note": _nullable("string"),
            "key_issue": _nullable("string"),
        },
    }
    device = {"type": "object", "additionalProperties": False, "required": ["device_id", "paper_device"],
              "properties": {"device_id": {"type": "string"}, "paper_device": _nullable("string")}}
    return {"name": "recheck_rows", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["devices", "rows"],
        "properties": {"devices": {"type": "array", "items": device}, "rows": {"type": "array", "items": row}}}}


def judge_task(items: list[dict]) -> str:
    return "Decide for each item:\n" + "\n".join(json.dumps(i, ensure_ascii=False) for i in items)


JUDGE_CHOICES = ("same", "paper_more_detail", "different")


def judge_schema() -> dict:
    verdict = {"type": "object", "additionalProperties": False, "required": ["id", "choice", "p", "reason"],
               "properties": {"id": {"type": "string"}, "choice": {"type": "string", "enum": list(JUDGE_CHOICES)},
                              "p": {"type": "number"}, "reason": {"type": "string"}}}
    return {"name": "judge", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["verdicts"],
        "properties": {"verdicts": {"type": "array", "items": verdict}}}}


class RowAnswer(EvidenceRow):
    row_id: str
    found: bool = True
    key_issue: str | None = None


class DeviceAnswer(BaseModel):
    device_id: str
    paper_device: str | None = None


class ExtractionAnswer(BaseModel):
    devices: list[DeviceAnswer] = []
    rows: list[RowAnswer]


class JudgeVerdict(BaseModel):
    """same: one meaning. paper_more_detail: they agree and the paper adds something the record leaves out.
    different: they contradict each other. ``p`` is the judge's probability that its choice is right."""

    id: str
    choice: Literal["same", "paper_more_detail", "different"]
    p: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _legacy_same(cls, v):  # answers written as {"same": true | false}, before the three-way choice
        if isinstance(v, dict) and "choice" not in v and "same" in v:
            v = {**v, "choice": "same" if v["same"] else "different"}
            v.pop("same")
        return v


class JudgeAnswer(BaseModel):
    verdicts: list[JudgeVerdict]


AUDIT_CHOICES = ("human_correct", "agent_correct", "both_correct", "neither", "unclear")


def audit_task(items: list[dict]) -> str:
    return ("Audit each item against the paper above. \"recorded\" was typed by a person, \"agent\" was extracted from the paper:\n"
            + "\n".join(json.dumps(i, ensure_ascii=False) for i in items))


def audit_schema() -> dict:
    audit = {"type": "object", "additionalProperties": False, "required": ["id", "verdict", "p", "evidence", "page", "explanation"],
             "properties": {"id": {"type": "string"}, "verdict": {"type": "string", "enum": list(AUDIT_CHOICES)},
                            "p": {"type": "number"}, "evidence": {"type": ["string", "null"]},
                            "page": {"type": ["integer", "null"]}, "explanation": {"type": "string"}}}
    return {"name": "recheck_audit", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["audits"],
        "properties": {"audits": {"type": "array", "items": audit}}}}


class AuditItem(BaseModel):
    id: str
    verdict: Literal["human_correct", "agent_correct", "both_correct", "neither", "unclear"]
    p: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None
    page: int | None = None
    explanation: str = ""


class AuditAnswer(BaseModel):
    audits: list[AuditItem] = Field(default_factory=list)
