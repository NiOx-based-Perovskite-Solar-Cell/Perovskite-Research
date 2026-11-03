"""Recheck whole folders: pair PDFs with workbooks, run each paper, write JSON + CSV.

Layout of ``out_dir``::

    pairing.csv          which PDF was used for which workbooks, plus what could not be paired
    papers/<doi_key>.json  one PaperReport per paper (existing files are skipped unless force=True)
    findings.csv         every finding of every paper in papers/, worst status first
"""
from __future__ import annotations

import csv
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..sources.pairing import Pairing, PaperPair, doi_key, pair_folders
from .rechecker import Rechecker
from .report import STATUSES, Finding, PaperReport

log = logging.getLogger(__name__)
FINDING_COLUMNS = ["paper", "doi", *[n for n, f in Finding.model_fields.items() if not f.exclude]]


def recheck_folders(
    rechecker: Rechecker,
    raw_dir,
    pdf_dir,
    out_dir,
    *,
    only: list[str] | None = None,
    limit: int | None = None,
    workers: int = 1,
    force: bool = False,
    should_stop=None,
) -> list[PaperReport]:
    """Run every paired paper (or those whose DOI is in ``only``); returns this run's reports.
    ``should_stop()`` is checked before and after each paper: a stopped paper is not saved, so a later
    run redoes it instead of skipping a partial report."""
    out = Path(out_dir)
    (out / "papers").mkdir(parents=True, exist_ok=True)
    pairing = pair_folders(raw_dir, pdf_dir)
    write_pairing_csv(pairing, out / "pairing.csv")

    wanted = {doi_key(k) for k in only or []}
    todo = [p for p in pairing.pairs if not wanted or p.key in wanted]
    if not force:
        todo = [p for p in todo if not (out / "papers" / f"{p.key}.json").exists()]
    todo = todo[:limit] if limit else todo
    log.info("%d paper(s) to recheck", len(todo))

    def run(pair: PaperPair) -> PaperReport | None:
        if should_stop and should_stop():
            return None
        try:
            report = rechecker.recheck(pair.pdf, pair.workbooks)
        except Exception:  # one bad paper must not stop the batch
            log.exception("%s failed", pair.pdf.name)
            return None
        if should_stop and should_stop():
            return None
        _write_atomic(out / "papers" / f"{pair.key}.json", report.model_dump_json(indent=2))
        log.info("%s: %s, $%.4f", pair.pdf.name, report.counts(), report.usage.cost_usd)
        return report

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        reports = [r for r in pool.map(run, todo) if r is not None]

    all_reports = [PaperReport.model_validate_json(p.read_text(encoding="utf-8"))
                   for p in sorted((out / "papers").glob("*.json"))]
    write_findings_csv(all_reports, out / "findings.csv")
    return reports


def write_findings_csv(reports: list[PaperReport], path) -> None:
    rows = sorted((r for rep in reports for r in rep.records()),
                  key=lambda r: (STATUSES.index(r["status"]), r["paper"], r["device"], r["sheet"],
                                 r["excel_row"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=FINDING_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_pairing_csv(pairing: Pairing, path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["doi_key", "status", "pdf", "workbooks", "note"])
        for p in pairing.pairs:
            notes = []
            if p.pdf_doi:
                notes.append(f"workbook names differ from the PDF's DOI {p.pdf_doi} by one character")
            if p.duplicate_pdfs:
                notes.append("duplicate PDF(s) ignored: " + ", ".join(d.name for d in p.duplicate_pdfs))
            w.writerow([p.key, "paired_typo" if p.pdf_doi else "paired", p.pdf.name, len(p.workbooks),
                        "; ".join(notes)])
        for k, wbs in sorted(pairing.workbooks_without_pdf.items()):
            w.writerow([k, "no_pdf", "", len(wbs), ", ".join(x.name for x in wbs)])
        for pdf in pairing.unmatched_pdfs:
            w.writerow(["", "no_workbook", pdf.name, 0, "DOI not found among workbooks"])


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
