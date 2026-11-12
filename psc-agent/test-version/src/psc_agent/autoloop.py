"""``psc-agent loop``: papers in, trusted rows out, with no person in the loop.

    1. SI           the SI reader / publisher pattern / (SI files already in SI/), as in ``run``
    2. extract      the extractor drafts the workbooks; a sheet group that failed (a 503) is asked again (``--rounds``)
    3. second read  a Rechecker, ideally on ANOTHER model, reads the paper against every cell of the drafts (extract.verify)
    4. final        a row is ``accepted`` only when the second reader confirmed it; the rest is ``held`` with the reason.
                    A number the model read off a plot is ``human_read``: the draft keeps the estimate, but the row is never
                    accepted and no second model reads the plot; a person reads it again (``human_read.csv`` is that list)
    5. gate         accepted rows go to ``<out>/staged/`` only when the pipeline has been measured against the hand-entered
                    workbooks (``--calibrate``) and its precision is at least ``--min-precision`` with these very models
    6. audit sample a few percent of the accepted rows are written to ``audit_sample.csv`` for an occasional look: it blocks nothing

What it cannot do: values that are only in an SI that could not be downloaded stay missing (held or absent), and numbers
read off a plot are estimates (``human_read``): the draft has them, a person reads the plot again, nothing stages them.

Outputs under ``<out>``: ``provenance.csv`` (every extracted row: both readers, final status, models), ``held.csv``, ``human_read.csv``, ``staged/*.xlsx``
(gate open), ``audit_sample.csv``, ``loop_summary.csv``, ``REPORT.md``, and per paper ``papers/<key>/`` (drafts, accepted drafts).
``calibration.json`` (``--calibration``, default ./calibration.json) is what ``--calibrate`` writes and the gate reads.
"""
from __future__ import annotations

import csv
import json
import logging
import random
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .evaluate.compare import compare_devices
from .evaluate.report import pct, stats
from .extract import Extractor
from .extract.extractor import FIGURE_NUMBER_COLUMNS, draft_stem, row_id, write_drafts
from .extract.verify import Verification, Verifier, final_status
from .runner import Item, settle_si
from .sources.paper import Paper
from .sources.si import SiFinder, SiReader
from .sources.workbook import load_workbook

log = logging.getLogger(__name__)
MIN_CELLS = 100  # a calibration over fewer hand-entered values found than this proves nothing
PROVENANCE_FIELDS = ["paper", "device", "sheet", "row_id", "values", "page", "source", "evidence", "first_pass", "first_reason",
                     "second_reader", "second_detail", "final", "confidence", "why", "extractor_model", "verifier_model"]


@dataclass
class PaperLoop:
    key: str
    si_status: str = ""
    rounds: int = 0
    records: list[dict] = field(default_factory=list)  # one provenance record per extracted row
    draft_files: list[str] = field(default_factory=list)  # everything extracted
    accepted_files: list[str] = field(default_factory=list)  # only the accepted rows
    errors: list[str] = field(default_factory=list)
    cost: float = 0.0
    seconds: float = 0.0
    paper: Paper | None = None

    @property
    def accepted(self) -> int:
        return sum(r["final"] == "accepted" for r in self.records)

    @property
    def held(self) -> int:
        return sum(r["final"] == "held" for r in self.records)

    @property
    def human(self) -> int:
        return sum(r["final"] == "human_read" for r in self.records)


def _confirmed(v: Verification) -> int:
    return sum(x.verdict == "confirmed" for x in v.verdicts.values())


def process_paper(item: Item, *, finder: SiFinder | None, reader: SiReader | None, extractor: Extractor, verifier: Verifier,
                  out, rounds: int = 2) -> PaperLoop:
    """One paper through steps 1-4. Never raises: a failure is recorded in ``errors``."""
    started = time.time()
    work = Path(out) / "papers" / item.key[:40]
    loop = PaperLoop(item.key)
    try:
        paper, si = settle_si(item, finder=finder, reader=reader)
        loop.paper, loop.si_status, loop.cost = paper, si.status, si.usage.cost_usd
        res = extractor.extract(paper, work / "extract")
        while loop.rounds < rounds and (res.failed_groups or not res.devices):
            loop.rounds += 1
            if res.devices:
                res = extractor.fill_missing(paper, work / "extract", res)
            else:  # the paper map failed: nothing to fill, start again
                loop.cost += res.usage.cost_usd
                res = extractor.extract(paper, work / "extract")
        loop.cost += res.usage.cost_usd
        loop.errors += res.errors
        if not res.devices:
            return loop
        best: Verification | None = None
        for _ in range(max(1, rounds)):  # a failed call of the second reader is asked again
            ver = verifier.verify(paper, res)
            loop.cost += ver.cost_usd
            if best is None or _confirmed(ver) > _confirmed(best):
                best = ver
            if not ver.errors:
                break
        loop.errors += [f"second reader: {e}" for e in best.errors]
        keep: set[str] = set()
        for i, r in enumerate(res.rows):
            rid = row_id(i, r)
            first, second = res.verdicts.get(rid), best.verdicts.get(rid)
            final, confidence, why = final_status(first, r.source, second)
            if final == "accepted":
                keep.add(rid)
            loop.records.append({
                "paper": item.key, "device": r.device, "sheet": r.sheet, "row_id": rid,
                "values": json.dumps(r.cells(), ensure_ascii=False), "page": r.page, "source": r.source, "evidence": r.evidence,
                "first_pass": first.choice if first else "", "first_reason": first.reason if first else "",
                "second_reader": second.verdict if second else "", "second_detail": second.detail if second else "",
                "final": final, "confidence": confidence, "why": why,
                "extractor_model": extractor.rt.model_for("extract"), "verifier_model": best.model})
        loop.draft_files = list(res.files)
        loop.accepted_files, _ = write_drafts(res, work / "accepted", draft_stem(paper), keep=keep)
    except Exception as e:  # one bad paper must not stop the others
        log.exception("%s failed", item.key)
        loop.errors.append(f"{type(e).__name__}: {e}")
    loop.seconds = round(time.time() - started, 1)
    return loop


# ---------------------------------------------------------------- the gate

def gate_status(calibration: Path, min_precision: float, extract_model: str, verifier_model: str) -> tuple[bool, str]:
    """May accepted rows be staged? Only with a calibration made with these models whose precision reaches the minimum."""
    try:
        cal = json.loads(Path(calibration).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, f"no calibration ({calibration}): run `psc-agent loop --calibrate` on papers that have hand-entered workbooks"
    if (cal.get("extract_model"), cal.get("verifier_model")) != (extract_model, verifier_model):
        return False, (f"calibrated with {cal.get('extract_model')} + {cal.get('verifier_model')}, not {extract_model} + {verifier_model}: "
                       "calibrate again")
    accepted = cal.get("accepted") or {}
    precision, found = accepted.get("accuracy_when_found"), accepted.get("found") or 0
    if precision is None or found < MIN_CELLS:
        return False, f"the calibration compared only {found} hand-entered values (at least {MIN_CELLS} needed): calibrate on more papers"
    if precision < min_precision:
        return False, f"precision of the accepted rows is {pct(precision)}, below the required {pct(min_precision)}"
    return True, f"precision of the accepted rows {pct(precision)} (>= {pct(min_precision)}) over {found} values, calibrated {cal.get('date')}"


def calibrate(items: list[Item], *, finder, reader, extractor: Extractor, verifier: Verifier, out, calibration: Path,
              rounds: int = 2, workers: int = 1) -> dict:
    """Run steps 1-4 on papers that HAVE hand-entered workbooks and measure what the loop would have staged against them.
    ``all_rows`` is the extractor alone (what staging without the second reader would give), ``accepted`` the rows both
    readers agreed on. Writes ``calibration``."""
    loops = _run(items, finder, reader, extractor, verifier, out, rounds, workers)
    all_cells, acc_cells, papers = [], [], 0
    for item, loop in zip(items, loops):
        if loop.paper is None or not loop.draft_files:
            continue
        record = [load_workbook(p) for p in item.workbooks]
        papers += 1
        all_cells += compare_devices(record, [load_workbook(f) for f in loop.draft_files], pages=loop.paper.pages)[0]
        acc_cells += compare_devices(record, [load_workbook(f) for f in loop.accepted_files], pages=loop.paper.pages)[0] if loop.accepted_files else []
    value = lambda cells: stats([c for c in cells if c.kind == "value"])  # noqa: E731
    cal = {"date": date.today().isoformat(), "papers": papers, "extract_model": extractor.rt.model_for("extract"),
           "verifier_model": verifier.rechecker.model, "all_rows": value(all_cells), "accepted": value(acc_cells),
           "cost_usd": round(sum(lp.cost for lp in loops), 4)}
    Path(calibration).write_text(json.dumps(cal, indent=2), encoding="utf-8")
    return cal


# ---------------------------------------------------------------- the loop

def _run(items, finder, reader, extractor, verifier, out, rounds, workers) -> list[PaperLoop]:
    one = lambda it: process_paper(it, finder=finder, reader=reader, extractor=extractor, verifier=verifier, out=out, rounds=rounds)  # noqa: E731
    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(one, items))
    return [one(it) for it in items]


def audit_sample(records: list[dict], fraction: float, minimum: int = 5, seed: int = 0) -> list[dict]:
    """A reproducible sample of the accepted rows for an occasional human look (``human_ok`` is left blank)."""
    accepted = [r for r in records if r["final"] == "accepted"]
    n = min(len(accepted), max(minimum, round(fraction * len(accepted))))
    return random.Random(seed).sample(accepted, n)


def run_loop(items: list[Item], *, finder, reader, extractor: Extractor, verifier: Verifier, out, calibration: Path,
             min_precision: float = 0.9, sample: float = 0.05, rounds: int = 2, workers: int = 1, seed: int = 0):
    """Steps 1-6 over the papers. Returns (the PaperLoops, gate_open, the gate's reason)."""
    out = Path(out)
    loops = _run(items, finder, reader, extractor, verifier, out, rounds, workers)
    records = [r for lp in loops for r in lp.records]
    open_, why = gate_status(calibration, min_precision, extractor.rt.model_for("extract"), verifier.rechecker.model)
    if open_:
        staged = out / "staged"
        staged.mkdir(parents=True, exist_ok=True)
        for lp in loops:
            for f in lp.accepted_files:
                shutil.copy2(f, staged / Path(f).name)
    _write_csv(out / "provenance.csv", PROVENANCE_FIELDS, records)
    _write_csv(out / "held.csv", PROVENANCE_FIELDS, [r for r in records if r["final"] == "held"])
    _write_csv(out / "human_read.csv", ["paper", "device", "sheet", "row", "figure", "page", "what_to_read", "model_estimate", "value", "unit"],
               [_human_row(r) for r in records if r["final"] == "human_read"])
    _write_csv(out / "audit_sample.csv", [*PROVENANCE_FIELDS, "human_ok"], [{**r, "human_ok": ""} for r in audit_sample(records, sample, seed=seed)])
    _write_csv(out / "loop_summary.csv", ["paper", "si", "rounds", "rows", "accepted", "held", "human_read", "errors", "cost_usd", "seconds"],
               [{"paper": lp.key, "si": lp.si_status, "rounds": lp.rounds, "rows": len(lp.records), "accepted": lp.accepted,
                 "held": lp.held, "human_read": lp.human, "errors": len(lp.errors), "cost_usd": round(lp.cost, 4),
                 "seconds": lp.seconds} for lp in loops])
    _write_report(out, loops, open_, why, extractor.rt.model_for("extract"), verifier.rechecker.model)
    return loops, open_, why


def _human_row(r: dict) -> dict:
    """One line of the person's reading list: the row's identity, the figure, what the graph shows and the model's estimate;
    ``value`` is the person's own reading of the plot, to fill in."""
    cells = json.loads(r["values"])
    numbers = {c for cols in FIGURE_NUMBER_COLUMNS.values() for c in cols}
    estimate = {k: v for k, v in cells.items() if k in numbers}
    ident = {k: v for k, v in cells.items() if k not in numbers and k != "Unit"}
    return {"paper": r["paper"], "device": r["device"], "sheet": r["sheet"], "row": json.dumps(ident, ensure_ascii=False),
            "figure": r["evidence"], "page": r["page"], "what_to_read": r["why"],
            "model_estimate": json.dumps(estimate, ensure_ascii=False) if estimate else "", "value": "", "unit": cells.get("Unit", "")}


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _write_report(out: Path, loops: list[PaperLoop], gate_open: bool, why: str, extract_model: str, verifier_model: str) -> None:
    rows = sum(len(lp.records) for lp in loops)
    acc = sum(lp.accepted for lp in loops)
    lines = ["# psc-agent loop", "",
             f"- extract: `{extract_model}`, second reader: `{verifier_model}`",
             f"- papers: {len(loops)}, rows extracted: {rows}, **accepted {acc}** ({pct(acc / rows) if rows else '–'}), held {sum(lp.held for lp in loops)}, "
             f"**plot estimates for a person to re-read {sum(lp.human for lp in loops)}**",
             f"- gate: **{'OPEN' if gate_open else 'CLOSED'}**: {why}",
             f"- {'accepted rows are in `staged/`' if gate_open else 'nothing was staged: the accepted drafts are in papers/<key>/accepted/ for when the gate opens'}",
             "- `provenance.csv`: every row with both readings; `held.csv`: rows to settle; `human_read.csv`: numbers the model read off a plot, "
             "with the figure, what the graph shows and the estimate, for a person to read again; `audit_sample.csv`: a sample of accepted rows for an occasional look", "",
             "| paper | SI | rows | accepted | held | plot (person) | errors | cost |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {lp.key[:40]} | {lp.si_status} | {len(lp.records)} | {lp.accepted} | {lp.held} | {lp.human} | {len(lp.errors)} | ${lp.cost:.3f} |" for lp in loops]
    errors = [f"- {lp.key[:40]}: {e}" for lp in loops for e in lp.errors]
    if errors:
        lines += ["", "## Errors", *errors[:40]]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
