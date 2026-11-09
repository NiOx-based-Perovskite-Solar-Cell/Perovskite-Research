"""``psc-agent run``: every paper goes through the same short line of specialised agents, cheapest step first.

    1. SI reader  (si_reader)  a model reads the paper's own text: does it refer to supporting information? No: skip the
                               SI. It also pulls out the SI address when the paper prints one (that link is enough).
                               Without a reader (``--no-si-reader``) a text rule (no model) answers the first question.
    2. SI link    (no model)   a publisher whose supplementary page follows from the DOI (ACS, Wiley): that link is enough.
    3. SI agent   (si_finder)  only for the rest: searches the web, answers with the SI addresses (or downloads the file
                               through the person's Chrome). SI files already in ``SI/`` are used as they are.
    4. then ONE reading of the paper together with its SI, by the agent that fits the paper:
         extract  (mapper, extract, decision)   a paper with no workbooks: drafts them
         recheck  (recheck, judge)              a paper with hand-entered workbooks: checks them against the paper

Each agent is its own role with its own skills and its own model (``Runtime`` maps role -> model), so they can differ;
this run gives all of them the same model unless told otherwise. Nothing is read twice: the SI is settled before the
extract / recheck agent sees the paper.

Outputs under ``<out>``: ``si_report.csv`` + ``si_links.md`` (what to download by hand), ``papers/<key>.json`` and
``findings.csv`` (recheck), ``extract/<key>/`` (drafts + review.csv), ``run_summary.csv`` (one line per paper).
"""
from __future__ import annotations

import csv
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .extract import Extractor
from .recheck.batch import _write_atomic, write_findings_csv
from .recheck.rechecker import Rechecker
from .recheck.report import PaperReport
from .sources.pairing import SI_DIR, doi_key, local_si, pair_folders
from .sources.paper import load_paper
from .sources.si import SiFinder, SiReader, SiResult, intake_key, si_stage, write_si_reports

log = logging.getLogger(__name__)


@dataclass
class Item:
    key: str
    pdf: Path
    workbooks: list[Path]  # hand-entered workbooks: recheck them; none: extract the paper
    si: list[Path] = field(default_factory=list)  # SI files already in <pdf folder>/SI/


@dataclass
class Outcome:
    item: Item
    mode: str  # recheck | extract
    si: SiResult
    counts: dict = field(default_factory=dict)  # recheck: findings per status; extract: rows per decision
    errors: list[str] = field(default_factory=list)
    cost: float = 0.0  # USD as the client reports it (0 = not priced)
    seconds: float = 0.0


def collect(raw_dir, pdf_dir, *, only: list[str] | None = None, limit: int | None = None, mode: str = "auto") -> list[Item]:
    """The papers to run. ``mode``: auto = recheck the papers that have workbooks and extract the PDFs that have none;
    recheck / extract = only one of the two groups."""
    pairing = pair_folders(raw_dir, pdf_dir)
    items: list[Item] = []
    if mode in ("auto", "recheck"):
        items += [Item(p.key, p.pdf, list(p.workbooks), list(p.si)) for p in pairing.pairs]
    if mode in ("auto", "extract"):
        for pdf in pairing.unmatched_pdfs:
            key = intake_key(load_paper(pdf), pdf)
            items.append(Item(key, pdf, [], local_si(pdf_dir, key)))
    if only:
        wanted = [doi_key(o) for o in only]
        items = [i for i in items if any(w in i.key for w in wanted)]
    return items[:limit] if limit else items


def run_item(item: Item, *, finder: SiFinder | None, extractor: Extractor, rechecker: Rechecker, out, use_si: bool = True,
             reader: SiReader | None = None) -> Outcome:
    started = time.time()
    out = Path(out)
    have = item.si if use_si else []
    paper = load_paper(item.pdf, si=have)
    si = si_stage(paper, item.key, item.pdf.parent / SI_DIR, have, finder if use_si else None, reader if use_si else None)
    if si.status == "found" and si.files:  # downloaded a moment ago: the paper is read together with it, once
        paper = load_paper(item.pdf, si=si.files)
    mode = "recheck" if item.workbooks else "extract"
    outcome = Outcome(item, mode, si, cost=si.usage.cost_usd)
    try:
        if mode == "recheck":
            report = rechecker.recheck(paper, item.workbooks)
            (out / "papers").mkdir(parents=True, exist_ok=True)
            _write_atomic(out / "papers" / f"{item.key}.json", report.model_dump_json(indent=2))
            outcome.counts, outcome.errors, outcome.cost = report.counts(), list(report.errors), outcome.cost + report.usage.cost_usd
        else:
            res = extractor.extract(paper, out / "extract" / item.key[:60])
            outcome.counts, outcome.errors, outcome.cost = res.counts(), list(res.errors), outcome.cost + res.usage.cost_usd
    except Exception as e:  # one bad paper must not stop the others
        log.exception("%s failed", item.key)
        outcome.errors.append(f"{type(e).__name__}: {e}")
    outcome.seconds = round(time.time() - started, 1)
    log.info("%s: %s, SI %s, %s%s", item.key, mode, si.status, outcome.counts or "-", f", {len(outcome.errors)} error(s)" if outcome.errors else "")
    return outcome


def run_items(items: list[Item], *, finder: SiFinder | None, extractor: Extractor, rechecker: Rechecker, out, use_si: bool = True,
              workers: int = 1, reader: SiReader | None = None) -> list[Outcome]:
    """Run every item (in the order given), write the reports, return the outcomes."""
    out = Path(out)
    one = lambda it: run_item(it, finder=finder, extractor=extractor, rechecker=rechecker, out=out, use_si=use_si,  # noqa: E731
                              reader=reader)
    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            outcomes = list(pool.map(one, items))
    else:
        outcomes = [one(it) for it in items]
    write_outputs(outcomes, out)
    return outcomes


def write_outputs(outcomes: list[Outcome], out) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    si_dir = (outcomes[0].item.pdf.parent / SI_DIR) if outcomes else out / SI_DIR
    write_si_reports([(o.item.key, o.item.pdf.name, o.si) for o in outcomes], out, si_dir)
    reports = [PaperReport.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted((out / "papers").glob("*.json"))] \
        if (out / "papers").is_dir() else []
    if reports:
        write_findings_csv(reports, out / "findings.csv")
    with open(out / "run_summary.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["paper", "pdf", "mode", "si_status", "si_files", "si_links", "result", "errors", "cost_usd", "seconds"])
        for o in outcomes:
            w.writerow([o.item.key, o.item.pdf.name, o.mode, o.si.status, "; ".join(f.name for f in o.si.files), "\n".join(o.si.links),
                        json.dumps(o.counts, ensure_ascii=False), " | ".join(o.errors), round(o.cost, 4), o.seconds])
