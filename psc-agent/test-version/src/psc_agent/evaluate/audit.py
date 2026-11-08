"""Auditor agent: inspects non-matching cells between hand-entered workbooks and extracted data.

Whenever a cell's status is not 'match' (mismatch, unit_mismatch, review, not_found),
the auditor examines the paper text/figures and both candidate values to determine:
- Who is right? (extractor_correct, human_correct, equivalent, ambiguous, si_only)
- What is the definitive resolved value?
- Verbatim quote, page, and explanation.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..core.hooks import HookBlock
from ..core.openrouter import OpenRouterError
from ..core.runtime import Runtime, Usage
from ..sources.paper import Paper, paper_parts
from .compare import Cell

log = logging.getLogger(__name__)
FAILURES = (OpenRouterError, ValueError, HookBlock)

AuditVerdictChoice = Literal[
    "extractor_correct", "human_correct", "equivalent", "ambiguous", "si_only"
]


class ItemAudit(BaseModel):
    id: str
    verdict: AuditVerdictChoice
    resolved_value: str | None = None
    resolved_unit: str | None = None
    evidence: str | None = None
    page: int | None = None
    explanation: str = ""


class AuditAnswer(BaseModel):
    audits: list[ItemAudit] = Field(default_factory=list)


def _schema() -> dict:
    return {
        "name": "discrepancy_audit",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["audits"],
            "properties": {
                "audits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "verdict", "resolved_value", "resolved_unit", "evidence", "page", "explanation"],
                        "properties": {
                            "id": {"type": "string"},
                            "verdict": {
                                "type": "string",
                                "enum": ["extractor_correct", "human_correct", "equivalent", "ambiguous", "si_only"],
                            },
                            "resolved_value": {"type": ["string", "null"]},
                            "resolved_unit": {"type": ["string", "null"]},
                            "evidence": {"type": ["string", "null"]},
                            "page": {"type": ["integer", "null"]},
                            "explanation": {"type": "string"},
                        },
                    },
                }
            },
        },
    }


class Auditor:
    """Inspects non-matching cells and resolves discrepancies against the paper."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        batch_size: int = 10,
        dpi: int = 150,
        all_pages: bool = False,
        images: bool = True,
        rule_si: bool = True,
    ):
        self.rt = runtime
        self.batch_size = batch_size
        self.dpi = dpi
        self.all_pages = all_pages
        self.images = images
        self.rule_si = rule_si

    def audit(
        self,
        paper: Paper,
        cells: list[Cell],
        *,
        usage: Usage | None = None,
        only_discrepancies: bool = True,
    ) -> list[Cell]:
        """Inspects cells whose status is not 'match' (or all cells if not only_discrepancies).
        Updates Cell.audit_verdict, audit_resolved, audit_evidence, audit_explanation in place
        and returns the cells.
        """
        candidates: list[tuple[int, Cell]] = []
        for i, c in enumerate(cells):
            if c.kind != "value" or c.status == "extra":
                continue
            if only_discrepancies and c.status == "match":
                continue
            candidates.append((i, c))

        if not candidates:
            return cells

        to_query: list[tuple[int, Cell]] = []
        for i, c in candidates:
            # Rule optimization: if it was not found and proven not to exist in the manuscript text at all,
            # it lives in SI; resolve directly without burning LLM tokens.
            if self.rule_si and c.status == "not_found" and c.likely is False:
                c.audit_verdict = "si_only"
                c.audit_resolved = None
                c.audit_evidence = None
                c.audit_explanation = "Value not present in manuscript text (in Supporting Information or curator note)."
            else:
                to_query.append((i, c))

        if not to_query:
            return cells

        parts = paper_parts(paper, dpi=self.dpi, all_pages=self.all_pages, images=self.images)

        # Process in batches
        for chunk_idx in range(0, len(to_query), self.batch_size):
            chunk = to_query[chunk_idx : chunk_idx + self.batch_size]
            items_text = []
            for item_num, (orig_idx, c) in enumerate(chunk):
                gt_str = f"{c.gt} {c.gt_unit or ''}".strip() or "(empty)"
                ex_str = f"{c.ex} {c.ex_unit or ''}".strip() or "(empty)"
                item_id = f"item_{orig_idx}"
                items_text.append(
                    f"[{item_id}]\n"
                    f"  Device: {c.device} (paired with extracted {c.ex_device})\n"
                    f"  Sheet: {c.sheet} | Row: {c.row} | Column: {c.column}\n"
                    f"  Recorded (Human GT): {gt_str}\n"
                    f"  Extracted (Agent):   {ex_str}\n"
                    f"  Status: {c.status} ({c.detail or 'no detail'})\n"
                )

            task = (
                "AUDIT DISCREPANCIES:\n"
                "Examine each discrepancy between the human-entered record and the extracted value against the paper.\n"
                "For each item, determine who is right (or if they are equivalent), cite the evidence quote + page, "
                "give the resolved value, and explain the difference.\n\n"
                + "\n".join(items_text)
                + "\nReturn JSON: audits list."
            )

            try:
                ans = self.rt.ask_json(
                    "auditor",
                    [*parts, {"type": "text", "text": task}],
                    AuditAnswer,
                    schema=_schema(),
                    usage=usage,
                )
                by_id = {a.id.strip(): a for a in ans.audits}
                for item_num, (orig_idx, c) in enumerate(chunk):
                    item_id = f"item_{orig_idx}"
                    # Try matching by item_id or simple number or order
                    res = by_id.get(item_id) or by_id.get(str(orig_idx)) or (ans.audits[item_num] if item_num < len(ans.audits) else None)
                    if res:
                        c.audit_verdict = res.verdict
                        c.audit_resolved = f"{res.resolved_value} {res.resolved_unit or ''}".strip() if res.resolved_value else None
                        c.audit_evidence = f"p.{res.page} {res.evidence}" if res.page and res.evidence else (res.evidence or None)
                        c.audit_explanation = res.explanation
            except FAILURES as e:
                log.error("%s: audit chunk %d failed: %s", paper.name, chunk_idx, e)
                for orig_idx, c in chunk:
                    c.audit_verdict = c.audit_verdict or "ambiguous"
                    c.audit_explanation = f"Audit failed: {e}"

        return cells

