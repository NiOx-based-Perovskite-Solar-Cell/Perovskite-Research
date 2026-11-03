"""Recheck one paper's device workbooks against the paper.

Blind re-extraction: the model (role "recheck") sees each row's key columns but never the recorded values,
so it cannot simply agree with them. Values come back with page and evidence; the after_answer hooks check the
evidence (QuoteCheckHook), the code compares (units, rounding, tolerance), and text that still differs goes
to the judge role.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from ..core.hooks import HookBlock, QuoteCheckHook
from ..core.openrouter import OpenRouterClient, OpenRouterError
from ..core.runtime import Runtime, Usage
from ..sources.paper import Paper, load_paper, paper_parts
from ..sources.workbook import Device, Row, Source, is_process_like, load_workbook
from .compare import compare_values
from .prompts import (
    ExtractionAnswer,
    JudgeAnswer,
    extraction_schema,
    extraction_task,
    judge_schema,
    judge_task,
    row_id,
    row_keys,
)
from .report import DeviceInfo, Finding, PaperReport

log = logging.getLogger(__name__)

# Sheets re-extracted in one call. The paper is resent with every call (a cache breakpoint makes
# repeats cheap on Anthropic/Gemini); smaller groups keep each answer short and accurate.
SHEET_GROUPS = (("Layer", "Vender", "Materials"), ("Process",), ("Properties", "Stability"))
FAILURES = (OpenRouterError, ValueError, HookBlock)


class Rechecker:
    """Reusable and thread-safe across papers; share one instance in a backend.

    Give ``model`` (an OpenRouter id with image input and structured output) or a ready ``runtime``
    (models per role, skills, hooks). Without a runtime the hooks default to QuoteCheckHook.
    ``rel_tolerance`` applies to values from text and tables, ``figure_tolerance`` to values read off plots;
    rounding to either side's precision always counts as equal.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        runtime: Runtime | None = None,
        client: OpenRouterClient | None = None,
        api_key: str | None = None,
        cache_dir=None,
        judge: bool = True,
        judge_model: str | None = None,
        skills_dirs=(),
        hooks=None,
        dpi: int = 150,
        all_pages: bool = False,
        max_tokens: int = 32000,
        rel_tolerance: float = 0.001,
        figure_tolerance: float = 0.05,
        extra: dict | None = None,
    ):
        if runtime is None:
            if not model:
                raise ValueError("give a model id or a runtime")
            runtime = Runtime({"default": model, **({"judge": judge_model} if judge_model else {})}, client=client,
                              api_key=api_key, cache_dir=cache_dir, skills_dirs=skills_dirs,
                              hooks=[QuoteCheckHook()] if hooks is None else hooks, max_tokens=max_tokens, extra=extra)
        self.rt = runtime
        self.judge = judge
        self.dpi = dpi
        self.all_pages = all_pages
        self.rel_tolerance = rel_tolerance
        self.figure_tolerance = figure_tolerance

    @property
    def model(self) -> str:
        return self.rt.model_for("recheck")

    @property
    def judge_model(self) -> str | None:
        return self.rt.model_for("judge") if self.judge else None

    def recheck(self, paper: Paper | Source, workbooks: Iterable[Device | Source]) -> PaperReport:
        """Compare every workbook of one paper with that paper. Workbooks given as bytes must be
        loaded first with ``load_workbook(data, name=...)`` so their device names are known."""
        paper = paper if isinstance(paper, Paper) else load_paper(paper)
        devices = sorted((w if isinstance(w, Device) else load_workbook(w) for w in workbooks),
                         key=lambda d: (d.sample_no is None, d.sample_no or 0, d.name))
        if not devices:
            raise ValueError("no workbooks given")
        refs = {f"D{i}": d for i, d in enumerate(devices, 1)}
        report = PaperReport(paper=paper.name, doi=paper.doi, model=self.model, judge_model=self.judge_model,
                             devices=[DeviceInfo(ref=r, device=d.name) for r, d in refs.items()])
        run = {"kind": "recheck", "paper": paper.name, "devices": len(devices)}
        self.rt.hooks.emit("run_start", run=run, runtime=self.rt)
        parts = paper_parts(paper, dpi=self.dpi, all_pages=self.all_pages)

        for group in _sheet_groups(devices):
            rows = [(ref, row) for ref, d in refs.items() for s in group for row in d.sheets.get(s, [])]
            answer, error = None, ""
            try:
                answer = self._extract(paper, parts, refs, rows, report.usage)
            except FAILURES as e:
                error = f"{'/'.join(group)}: {e}"
                report.errors.append(error)
                log.error("%s: %s", paper.name, error)
            report.findings += self._findings(refs, rows, answer, error)
            if answer:
                named = {a.device_id: a.paper_device for a in answer.devices}
                for info in report.devices:
                    info.paper_device = info.paper_device or named.get(info.ref)

        if self.judge:
            self._judge(report)
        self.rt.hooks.emit("run_end", run=run, summary=report.counts())
        return report

    def _extract(self, paper: Paper, parts, refs, rows, usage: Usage) -> ExtractionAnswer:
        task, columns = extraction_task(refs, rows)
        answer = self.rt.ask_json("recheck", [*parts, {"type": "text", "text": task}], ExtractionAnswer,
                                  schema=extraction_schema(columns), usage=usage)
        self.rt.hooks.emit("after_answer", kind="recheck", paper=paper, rows=answer.rows)
        return answer

    def _findings(self, refs, rows: list[tuple[str, Row]], answer: ExtractionAnswer | None, error: str):
        by_id = {a.row_id.strip(): a for a in answer.rows} if answer else {}
        out: list[Finding] = []
        for ref, row in rows:
            a = by_id.get(row_id(ref, row))
            got = {c.column: c.value for c in a.values} if a else {}
            keys = row_keys(row)
            tol = self.figure_tolerance if a and a.source == "figure" else self.rel_tolerance
            for col in row.value_columns:
                rec = row.cells.get(col)
                f = dict(device=refs[ref].name, device_ref=ref, sheet=row.sheet, excel_row=row.excel_row,
                         column=col, keys=keys, recorded=None if rec is None else str(rec),
                         recorded_unit=row.unit_of(col))
                if a is None or not a.found:
                    if rec is not None:
                        status, detail = (("not_checked", error or "model returned no answer for this row") if a is None
                                          else ("not_found", _join("row not found in paper", a.note)))
                        out.append(Finding(**f, status=status, detail=detail))
                    continue
                pv, pu = got.get(col), row.unit_of(col, got)
                v = compare_values(rec, f["recorded_unit"], pv, pu, tol=tol)
                if v is None:
                    continue
                status, detail, judgeable = v.status, v.detail, v.judgeable
                if a.quote_ok is False and status != "not_found":  # checkpoint: evidence not on the cited page
                    status, judgeable = "review", False
                    detail = _join(f"evidence quote not found on page {a.page}; paper value unverified", detail)
                out.append(Finding(**f, paper_value=pv, paper_unit=pu, status=status, detail=_join(detail, a.note),
                                   page=a.page, source=a.source, evidence=a.evidence, judgeable=judgeable))
            if a and a.key_issue:
                out.append(Finding(device=refs[ref].name, device_ref=ref, sheet=row.sheet, excel_row=row.excel_row,
                                   column="(row keys)", keys=keys, recorded=keys, status="review",
                                   detail=a.key_issue, page=a.page, source=a.source, evidence=a.evidence))
        return out

    def _judge(self, report: PaperReport) -> None:
        pending = [(i, f) for i, f in enumerate(report.findings) if f.status == "review" and f.judgeable]
        if not pending:
            return
        items = [{"id": str(i), "field": f"{f.sheet} / {f.column}", "row": f.keys,
                  "recorded": _join(f.recorded, f.recorded_unit, sep=" "),
                  "paper": _join(f.paper_value, f.paper_unit, sep=" "), "paper_evidence": f.evidence}
                 for i, f in pending]
        try:
            answer = self.rt.ask_json("judge", judge_task(items), JudgeAnswer, schema=judge_schema(),
                                      usage=report.usage)
        except FAILURES as e:
            report.errors.append(f"judge: {e}")
            log.error("%s: judge failed: %s", report.paper, e)
            return
        verdicts = {v.id: v for v in answer.verdicts}
        for i, f in pending:
            v = verdicts.get(str(i))
            if v:
                f.status = "match" if v.same else "mismatch"
                f.detail = _join(f.detail, f"judge: {v.reason}")


def _sheet_groups(devices: list[Device]) -> list[list[str]]:
    """SHEET_GROUPS restricted to sheets present; extra process-like sheets (e.g. "NiOx synthesis")
    join Process, any other unknown sheet gets a call of its own."""
    present: dict[str, bool] = {}
    for d in devices:
        for s, rows in d.sheets.items():
            present.setdefault(s, is_process_like(rows[0].cells))
    groups = [list(g) for g in SHEET_GROUPS]
    for s, process_like in present.items():
        if not any(s in g for g in groups):
            (next(g for g in groups if "Process" in g).append(s) if process_like else groups.append([s]))
    return [[s for s in g if s in present] for g in groups if any(s in present for s in g)]


def _join(*parts, sep: str = "; ") -> str:
    return sep.join(str(p) for p in parts if p)
