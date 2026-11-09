"""Dashboard API: how well did an extraction match the hand-entered workbooks, for one run or two side by side.

A run is the output of ``evaluate``: a folder with one sub-folder per paper (``eval.json`` + ``cells.csv``). Runs come from
the web's own evaluate jobs and from the folders listed in ``WebConfig.eval_dirs`` (by default ``out/`` and
``evaluate_out/`` under the project root and the working folder), so runs made on the command line show up too.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..evaluate.report import RECORDED, stats, summarize
from ..evaluate.run import PaperEval, load_eval
from ..evaluate.versus import compare_runs, totals
from .api import ApiError, _cfg, _jobs

dash = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")
_DIR_ID = re.compile(r"^dir:(\d+):([\w. \-]{1,100})$")
_META = re.compile(r"Provider \*\*(.+?)\*\* · model `(.+?)`")
PROBLEMS = ("mismatch", "unit_mismatch", "review", "not_found")


@dash.errorhandler(ApiError)
def _error(e: ApiError):
    return jsonify(error=str(e)), e.status


def _finished(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / "eval.json").is_file()) if folder.is_dir() else []


def _modified(folders: list[Path]) -> str:
    return datetime.fromtimestamp(max((p / "eval.json").stat().st_mtime for p in folders)).isoformat(timespec="seconds") if folders else ""


def _eval_dirs() -> list[Path]:
    return [Path(d) for d in _cfg().eval_dirs]


def _runs() -> list[dict]:
    runs = []
    for job in _jobs().list():
        if job.kind == "evaluate":
            done = _finished(Path(job.out_dir))
            if done:
                runs.append({"id": f"job:{job.id}", "label": f"web job {job.id}", "source": "web job", "status": job.status,
                             "papers": len(done), "modified": _modified(done), **_meta(Path(job.out_dir))})
    for i, d in enumerate(_eval_dirs()):
        if d.is_dir():
            for sub in sorted(p for p in d.iterdir() if p.is_dir()):
                done = _finished(sub)
                if done:
                    runs.append({"id": f"dir:{i}:{sub.name}", "label": f"{d.name}/{sub.name}", "source": "folder", "status": "done",
                                 "papers": len(done), "modified": _modified(done), **_meta(sub)})
    return sorted(runs, key=lambda r: r["modified"], reverse=True)


def _meta(folder: Path) -> dict:
    """Provider and model, read from the run's report.md when it has one."""
    try:
        m = _META.search((folder / "report.md").read_text(encoding="utf-8")[:1500])
    except OSError:
        m = None
    return {"provider": m.group(1) if m else "", "model": m.group(2) if m else ""}


def _resolve(run_id: str) -> tuple[Path, str]:
    if run_id.startswith("job:"):
        job = _jobs().get(run_id[4:])
        if job is None or job.kind != "evaluate":
            raise ApiError(f"no evaluate job {run_id[4:]}", 404)
        return Path(job.out_dir), f"web job {job.id}"
    m = _DIR_ID.match(run_id or "")
    dirs = _eval_dirs()
    if not m or int(m.group(1)) >= len(dirs):
        raise ApiError(f"no such run: {run_id}", 404)
    base = dirs[int(m.group(1))].resolve()
    path = (base / m.group(2)).resolve()
    if path.parent != base or not path.is_dir():
        raise ApiError(f"no such run: {run_id}", 404)
    return path, f"{base.name}/{m.group(2)}"


def _load(path: Path) -> list[PaperEval]:
    """The finished papers of a run; parsed again only when a file of the run changed."""
    folders = _finished(path)
    stamp = tuple((p.name, p.stat().st_mtime_ns, (p / "eval.json").stat().st_mtime_ns) for p in folders)
    cache = current_app.config.setdefault("DASH_CACHE", {})
    hit = cache.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    evals = [ev for ev in (load_eval(p) for p in folders) if ev]
    if len(cache) >= 8:
        cache.pop(next(iter(cache)))
    cache[str(path)] = (stamp, evals)
    return evals


def _per_paper(evals: list[PaperEval]) -> list[dict]:
    out = []
    for ev in evals:
        out.append({"key": ev.key, "pdf": ev.pdf, "seconds": ev.seconds, "errors": ev.errors,
                    "record_devices": len(ev.devices), "paired_devices": sum(d.get("extracted") is not None for d in ev.devices),
                    "values": stats([c for c in ev.cells if c.kind == "value"]), "rows": stats([c for c in ev.cells if c.kind == "row"])})
    return out


def _cell(c, paper: str = "") -> dict:
    return {"paper": paper, "device": c.device, "sheet": c.sheet, "row": c.row, "column": c.column, "record": c.gt, "record_unit": c.gt_unit,
            "extracted": c.ex, "extracted_unit": c.ex_unit, "status": c.status, "detail": c.detail, "likely": c.likely, "core": c.core}


@dash.get("/runs")
def runs():
    return jsonify(runs=_runs())


@dash.get("/run")
def run():
    path, label = _resolve(request.args.get("id", ""))
    evals = _load(path)
    if not evals:
        raise ApiError("this run has no finished paper yet", 404)
    return jsonify(id=request.args["id"], label=label, meta=_meta(path), summary=summarize(evals), totals=totals(evals),
                   papers=_per_paper(evals))


@dash.get("/compare")
def compare():
    (pa, la), (pb, lb) = _resolve(request.args.get("a", "")), _resolve(request.args.get("b", ""))
    r = compare_runs(_load(pa), _load(pb))
    if not r["papers"]:
        raise ApiError("no paper is finished in both runs", 404)
    differing = [{**_cell(ca, k[0]), "other_extracted": cb.ex, "other_status": cb.status} for k, ca, cb in r["differing"][:300]]
    return jsonify(labels=[la, lb], meta=[_meta(pa), _meta(pb)], papers=r["papers"], a=r["a"], b=r["b"], totals=[r["ta"], r["tb"]],
                   agree=r["agree"], cells_compared=r["cells_compared"], differing=differing, differing_total=len(r["differing"]),
                   by_paper={k: {"a": sa, "b": sb} for k, (sa, sb) in r["by_paper"].items()})


@dash.get("/cells")
def cells():
    """The hand-entered values of a run, filtered: which ones did not match, and what did the model give instead."""
    path, _ = _resolve(request.args.get("id", ""))
    statuses = set((request.args.get("status") or ",".join(PROBLEMS)).split(",")) & set(RECORDED)
    sheet, paper, core = request.args.get("sheet", ""), request.args.get("paper", ""), request.args.get("core") == "1"
    found = []
    for ev in _load(path):
        if paper and ev.key != paper:
            continue
        found += [_cell(c, ev.key) for c in ev.cells if c.kind == "value" and c.status in statuses and (not sheet or c.sheet == sheet)
                  and (not core or c.core)]
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except ValueError:
        limit = 200
    return jsonify(total=len(found), cells=found[:limit])
