"""A second reader for an extraction: no person needed to say which rows to trust.

The extractor's drafts are ordinary workbooks, and ``Rechecker`` already knows how to read a paper and check every cell
of a workbook against it. So the second reader is a ``Rechecker`` (ideally on another model than the extractor) run on
the drafts. Each extracted row then has two independent readings, and the outcome per row is

    confirmed    every cell of the row was found in the paper and equals the draft (the comparison rules of recheck)
    disputed     the second reader read a different value or unit for some cell
    unverified   the second reader could not find the row / a cell, or its evidence is not on the page it cites

The final status of a row (``final_status``) is ``accepted`` only when the second reader confirmed it and the first
pass did not mark it ``revise``; everything else is ``held`` with the reason. A row whose number the model read off a plot
is ``human_read``: the draft keeps the estimate, a person reads the plot again, and the second reader is not asked about it.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..recheck.rechecker import Rechecker
from ..sources.paper import Paper
from ..sources.workbook import load_workbook
from .extractor import ExtractResult, draft_stem, row_id, write_drafts

log = logging.getLogger(__name__)

# worst first: one disputed cell makes the row disputed
_ORDER = ("mismatch", "unit_mismatch", "review", "not_found", "needs_si", "not_checked", "match")


@dataclass
class RowVerdict:
    row_id: str
    verdict: str  # confirmed | disputed | unverified
    detail: str = ""  # what the second reader saw: "Values: draft 19.6 / paper 19.9"


@dataclass
class Verification:
    verdicts: dict[str, RowVerdict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    model: str = ""
    cost_usd: float = 0.0


def _row_verdict(rid: str, findings) -> RowVerdict:
    if not findings:
        return RowVerdict(rid, "unverified", "the second reader gave no result for this row")
    worst = min(findings, key=lambda f: _ORDER.index(f.status) if f.status in _ORDER else 0)
    if all(f.status == "match" for f in findings):
        return RowVerdict(rid, "confirmed")
    bad = [f for f in findings if f.status in ("mismatch", "unit_mismatch")]
    if bad:
        return RowVerdict(rid, "disputed", "; ".join(
            f"{f.column}: draft {f.recorded} {f.recorded_unit or ''} / second reader {f.paper_value} {f.paper_unit or ''}".replace("  ", " ")
            for f in bad)[:400])
    return RowVerdict(rid, "unverified", f"{worst.status}: {worst.detail}"[:300])


class Verifier:
    """``rechecker``: the second reader (its runtime decides the model; give it another model than the extractor's)."""

    def __init__(self, rechecker: Rechecker):
        self.rechecker = rechecker

    def verify(self, paper: Paper, result: ExtractResult) -> Verification:
        """Read the paper again against the draft rows of ``result``; one verdict per row. A row marked human_read (a plot
        estimate) is not given to the second reader: a person reads that plot again, two estimates would settle nothing."""
        out = Verification(model=self.rechecker.model)
        if not result.rows:
            out.errors.append("no draft rows to verify")
            return out
        keep = {row_id(i, r) for i, r in enumerate(result.rows)
                if not (result.verdicts.get(row_id(i, r)) and result.verdicts[row_id(i, r)].choice == "human_read")}
        if not keep:
            return out
        with tempfile.TemporaryDirectory() as tmp:  # the drafts without the plot rows: what the second reader is shown
            files, locations = write_drafts(result, Path(tmp), draft_stem(paper), keep=keep)
            devices = [load_workbook(f) for f in files]
        report = self.rechecker.recheck(paper, devices)
        out.errors, out.cost_usd = list(report.errors), report.usage.cost_usd
        by_row: dict[tuple[str, str, int], list] = {}
        for f in report.findings:
            by_row.setdefault((f.device, f.sheet, f.excel_row), []).append(f)
        sheets = {d.name: d.sheets for d in devices}
        for i, r in enumerate(result.rows):
            rid = row_id(i, r)
            if rid not in keep:
                continue
            loc = locations.get(rid)
            rows = sheets.get(loc[0], {}).get(loc[1], []) if loc else []
            if not loc or loc[2] >= len(rows):
                out.verdicts[rid] = RowVerdict(rid, "unverified", "the row is not in the draft workbooks")
                continue
            out.verdicts[rid] = _row_verdict(rid, by_row.get((loc[0], loc[1], rows[loc[2]].excel_row), []))
        return out


def final_status(first, source: str | None, v: RowVerdict | None) -> tuple[str, str, str]:
    """(final, confidence, why) of one row from the first pass (``first``: its decision Verdict or None), the row's
    ``source`` (text | table | figure) and the second reader's verdict. final is accepted | held | human_read."""
    confidence = "two readers"
    if first is not None and first.choice == "human_read":
        return "human_read", "", first.reason
    if first is not None and first.choice == "revise":
        return "held", "", f"first pass: revise ({first.reason})"
    if v is None:
        return "held", "", "not verified"
    if v.verdict == "confirmed":
        return "accepted", confidence, ""
    return "held", "", f"second reader: {v.verdict}" + (f" ({v.detail})" if v.detail else "")
