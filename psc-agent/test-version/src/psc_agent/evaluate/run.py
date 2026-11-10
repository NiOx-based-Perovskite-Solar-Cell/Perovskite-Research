"""Run the extractor on papers that already have hand-entered workbooks, and score what it extracted against them.

The model sees only the PDF. The hand-entered workbooks are loaded afterwards, for comparison only.
Each paper writes ``<out>/<paper>/``: ``extracted/`` (drafts in the Raw Data layout, review.csv, extraction.json),
``cells.csv`` (one line per hand-entered cell with its verdict) and ``eval.json``; a rerun skips finished papers.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..extract import Extractor
from ..sources.pairing import PaperPair
from ..sources.paper import load_paper
from ..sources.workbook import load_workbook
from .audit import Auditor
from .compare import Cell, compare_devices

log = logging.getLogger(__name__)
CELL_FIELDS = [f.name for f in fields(Cell)]


@dataclass
class PaperEval:
    key: str
    pdf: str
    cells: list[Cell]
    devices: list[dict]  # which extracted device was paired with which workbook, and how alike their stacks are
    usage: dict
    errors: list[str]
    seconds: float


def _write(folder: Path, ev: PaperEval) -> None:
    with open(folder / "cells.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, CELL_FIELDS)
        w.writeheader()
        w.writerows(asdict(c) for c in ev.cells)
    meta = {k: v for k, v in asdict(ev).items() if k != "cells"}
    (folder / "eval.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def load_eval(folder: Path) -> PaperEval | None:
    try:
        meta = json.loads((folder / "eval.json").read_text(encoding="utf-8"))
        with open(folder / "cells.csv", newline="", encoding="utf-8-sig") as fh:
            null_keys = ("gt", "gt_unit", "ex", "ex_unit", "audit_verdict", "audit_resolved", "audit_evidence", "audit_explanation")
            cells = []
            for r in csv.DictReader(fh):
                d = dict(r)
                d["likely"] = d.get("likely") == "True"
                d["core"] = d.get("core") == "True"
                for k in null_keys:
                    if k in d:
                        d[k] = d[k] or None
                cells.append(Cell(**d))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return PaperEval(cells=cells, **meta)


def evaluate_paper(extractor: Extractor, pair: PaperPair, out: str | Path, *, tol: float = 0.01,
                   auditor: Auditor | None = None, use_si: bool = True) -> PaperEval:
    folder = Path(out) / pair.key
    started = time.time()
    folder.mkdir(parents=True, exist_ok=True)  # the extractor creates it only when it gets as far as writing
    paper = load_paper(pair.pdf, si=pair.si if use_si else ())
    res = extractor.extract(paper, folder / "extracted")
    record = [load_workbook(p) for p in pair.workbooks]
    extracted = [load_workbook(f) for f in res.files]
    cells, pairing = compare_devices(record, extracted, pages=paper.pages, tol=tol)
    if auditor:
        cells = auditor.audit(paper, cells, usage=res.usage)
    ev = PaperEval(pair.key, pair.pdf.name, cells, pairing, res.usage.model_dump(), list(res.errors),
                   round(time.time() - started, 1))
    _write(folder, ev)
    return ev


def rescore_paper(pair: PaperPair, out: str | Path, *, tol: float = 0.01, use_si: bool = True) -> PaperEval | None:
    """Score the extraction a finished run saved (``<out>/<paper>/extracted/*.xlsx``) again with the current comparison
    rules. No model is called; usage, errors and time of the original run are kept. None if the paper has no saved run."""
    folder = Path(out) / pair.key
    old = load_eval(folder)
    files = sorted((folder / "extracted").glob("*.xlsx"),
                   key=lambda f: int(m.group(1)) if (m := re.search(r"_Sample(\d+)\.xlsx$", f.name)) else 0)
    if old is None or not files:
        return None
    paper = load_paper(pair.pdf, si=pair.si if use_si else ())
    cells, pairing = compare_devices([load_workbook(p) for p in pair.workbooks], [load_workbook(f) for f in files],
                                     pages=paper.pages, tol=tol)
    ev = PaperEval(pair.key, pair.pdf.name, cells, pairing, old.usage, old.errors, old.seconds)
    _write(folder, ev)
    return ev


def evaluate_pairs(extractor: Extractor, pairs: list[PaperPair], out: str | Path, *, workers: int = 1, force: bool = False,
                   tol: float = 0.01, auditor: Auditor | None = None, use_si: bool = True) -> list[PaperEval]:
    """Evaluate every pair (finished ones are reloaded unless ``force``); results keep the order of ``pairs``."""

    def one(pair: PaperPair) -> PaperEval:
        folder = Path(out) / pair.key
        done = None if force else load_eval(folder)
        if done:
            if auditor and any(c.kind == "value" and c.status in ("mismatch", "unit_mismatch", "review") and not c.audit_verdict for c in done.cells):
                log.info("%s: auditing existing eval", pair.key)
                paper = load_paper(pair.pdf, si=pair.si if use_si else ())
                done.cells = auditor.audit(paper, done.cells)
                _write(folder, done)
            log.info("%s: reusing %s", pair.key, folder / "eval.json")
            return done
        try:
            ev = evaluate_paper(extractor, pair, out, tol=tol, auditor=auditor, use_si=use_si)
        except Exception as e:  # one bad paper must not stop the batch
            log.exception("%s failed", pair.key)
            ev = PaperEval(pair.key, pair.pdf.name, [], [], {}, [f"{type(e).__name__}: {e}"], 0.0)
        c = [x for x in ev.cells if x.status != "extra" and x.kind == "value"]
        log.info("%s: %d devices, %d/%d hand-entered values match, %.0fs", pair.key, len(ev.devices),
                 sum(x.status == "match" for x in c), len(c), ev.seconds)
        return ev

    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(one, pairs))
    return [one(p) for p in pairs]
