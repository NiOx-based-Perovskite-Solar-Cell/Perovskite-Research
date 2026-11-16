"""Recheck result types. All pydantic, so a backend can return ``report.model_dump()`` as JSON."""
from __future__ import annotations

from collections import Counter
from typing import Literal, get_args

from pydantic import BaseModel, Field

from ..core.runtime import Usage

# worst first
# needs_si: the recorded value is not in the main text and no SI was loaded: it probably comes from the SI
# paper_more_detail: the record and the paper agree, but the paper says more (a ratio, a condition) than the record holds
Status = Literal["mismatch", "unit_mismatch", "missing_in_record", "not_found", "needs_si", "review", "not_checked",
                 "paper_more_detail", "match"]
STATUSES: tuple[str, ...] = get_args(Status)


class Finding(BaseModel):
    """One checked cell (or, with column "(row keys)", a disagreement about a row's identity)."""

    device: str  # workbook stem, e.g. 10.1002_smtd.202300241_Sample2
    device_ref: str  # D1, D2, ... as named in the prompt
    sheet: str
    excel_row: int
    column: str
    keys: str  # the row's identity, e.g. "LayerID=4 | Material=PbI2"
    recorded: str | None = None
    recorded_unit: str | None = None
    paper_value: str | None = None
    paper_unit: str | None = None
    status: Status
    detail: str = ""
    page: int | None = None
    source: str | None = None  # text | table | figure | none
    evidence: str | None = None
    si_link: str = ""  # where to download the SI, on rows the SI could settle (needs_si, not_found)
    si_save_as: str = ""  # the file name to save it under, in the SI/ folder next to the PDFs
    # the auditor's second reading of a cell where the record and the agent disagree (None = not audited)
    audit: str | None = None  # human_correct | agent_correct | both_correct | neither | unclear
    audit_p: float | None = None  # the auditor's probability that its verdict is right
    audit_page: int | None = None
    audit_evidence: str | None = None  # the quote the auditor decided on (for human_correct: where the recorded value is)
    audit_quote_ok: bool | None = None  # the quote is on that page (checked by code)
    audit_note: str = ""
    judgeable: bool = Field(default=False, exclude=True)


class DeviceInfo(BaseModel):
    ref: str
    device: str
    paper_device: str | None = None  # how the model matched it to the paper


class PaperReport(BaseModel):
    paper: str
    doi: str | None = None
    model: str
    judge_model: str | None = None
    devices: list[DeviceInfo] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    errors: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = Counter(f.status for f in self.findings)
        return {s: c[s] for s in STATUSES if c[s]}

    def records(self) -> list[dict]:
        """Flat rows, worst status first, for CSV or a table view."""
        order = sorted(self.findings, key=lambda f: (STATUSES.index(f.status), f.device, f.sheet, f.excel_row))
        return [{"paper": self.paper, "doi": self.doi, **f.model_dump()} for f in order]
