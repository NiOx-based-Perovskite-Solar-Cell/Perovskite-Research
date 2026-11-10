"""Put two evaluation runs side by side (two models, or one model with and without the SI) on the papers both have.

A run is an ``evaluate`` output folder (one sub-folder per paper with ``eval.json`` and ``cells.csv``). Only papers present
in both runs are compared, so a run that stopped early or skipped a paper does not look worse than it is. The cells are
the hand-entered values (the record), the same in both runs, so a cell can be followed from one run to the other.
"""
from __future__ import annotations

from pathlib import Path

from .compare import SHEETS, Cell
from .report import _table, pct, stats, summarize
from .run import PaperEval, load_eval


def load_run(folder: str | Path) -> list[PaperEval]:
    """Every finished paper of an ``evaluate`` output folder."""
    out = []
    for sub in sorted(Path(folder).iterdir()):
        if sub.is_dir():
            ev = load_eval(sub)
            if ev:
                out.append(ev)
    return out


def totals(evals: list[PaperEval]) -> dict:
    u = lambda k: sum(ev.usage.get(k, 0) or 0 for ev in evals)  # noqa: E731
    return {"calls": u("calls"), "tokens_in": u("prompt_tokens"), "tokens_out": u("completion_tokens"), "cost": u("cost_usd"),
            "seconds": sum(ev.seconds for ev in evals), "errors": sum(len(ev.errors) for ev in evals)}


def _cells(evals: list[PaperEval]) -> dict[tuple, Cell]:
    return {(ev.key, c.device, c.sheet, c.row, c.column): c for ev in evals for c in ev.cells if c.kind == "value" and c.status != "extra"}


def compare_runs(a: list[PaperEval], b: list[PaperEval]) -> dict:
    keys = sorted({ev.key for ev in a} & {ev.key for ev in b})
    a, b = [ev for ev in a if ev.key in keys], [ev for ev in b if ev.key in keys]
    ca, cb = _cells(a), _cells(b)
    shared = ca.keys() & cb.keys()
    agree = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
    differing = []
    for k in sorted(shared):
        ma, mb = ca[k].status == "match", cb[k].status == "match"
        agree["both" if ma and mb else "only_a" if ma else "only_b" if mb else "neither"] += 1
        if ma != mb:
            differing.append((k, ca[k], cb[k]))
    return {"papers": keys, "a": summarize(a), "b": summarize(b), "ta": totals(a), "tb": totals(b), "agree": agree,
            "cells_compared": len(shared), "differing": differing,
            "by_paper": {k: (stats([c for ev in a if ev.key == k for c in ev.cells if c.kind == "value"]),
                             stats([c for ev in b if ev.key == k for c in ev.cells if c.kind == "value"])) for k in keys}}


def _delta(x, y) -> str:
    return "–" if x is None or y is None else f"{100 * (y - x):+.0f} pts"


def _short(v, n: int = 40) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def write_comparison(a: list[PaperEval], b: list[PaperEval], labels: tuple[str, str], out: str | Path, examples: int = 25) -> Path:
    r = compare_runs(a, b)
    la, lb = labels
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    va, vb = r["a"]["values"], r["b"]["values"]
    ta, tb = r["ta"], r["tb"]
    lines = [f"# {la} vs {lb}", "",
             f"{len(r['papers'])} papers present in both runs, {r['cells_compared']} hand-entered values compared. "
             f"A = **{la}**, B = **{lb}**. Differences are B minus A.", "", "## Headline", ""]
    metric = lambda name, key: [name, pct(va[key]), pct(vb[key]), _delta(va[key], vb[key])]  # noqa: E731
    lines.append(_table(["metric", la, lb, "B − A"], [
        metric("values that match", "match_rate"),
        metric("values the model gave", "found_rate"),
        metric("correct when given", "accuracy_when_found"),
        metric("match, of values that are in the PDF text", "match_rate_likely"),
        ["rows found (a layer, material, step, property)", pct(r["a"]["rows"]["match_rate"]), pct(r["b"]["rows"]["match_rate"]),
         _delta(r["a"]["rows"]["match_rate"], r["b"]["rows"]["match_rate"])],
        ["core fields match (champion PCE/Voc/Jsc/FF, layer materials)", pct(r["a"]["core"]["match_rate"]), pct(r["b"]["core"]["match_rate"]),
         _delta(r["a"]["core"]["match_rate"], r["b"]["core"]["match_rate"])],
        ["model calls", ta["calls"], tb["calls"], f"{tb['calls'] - ta['calls']:+d}"],
        ["tokens in / out", f"{ta['tokens_in']:,} / {ta['tokens_out']:,}", f"{tb['tokens_in']:,} / {tb['tokens_out']:,}", ""],
        ["cost (USD as reported; 0 = not priced)", f"{ta['cost']:.2f}", f"{tb['cost']:.2f}", f"{tb['cost'] - ta['cost']:+.2f}"],
        ["time (s)", f"{ta['seconds']:.0f}", f"{tb['seconds']:.0f}", f"{tb['seconds'] - ta['seconds']:+.0f}"],
        ["papers with an error", sum(bool(ev.errors) for ev in a), sum(bool(ev.errors) for ev in b), ""]]))
    lines.append("")

    g = r["agree"]
    lines += ["## Cell by cell", "", _table(["", "values"], [["both match", g["both"]], [f"only {la} matches", g["only_a"]],
                                                              [f"only {lb} matches", g["only_b"]], ["neither matches", g["neither"]]]), ""]

    lines += ["## By sheet (values that match)", "", _table(
        ["sheet", "values in record", la, lb, "B − A"],
        [[s, r["a"]["by_sheet"][s]["values"]["n"], pct(r["a"]["by_sheet"][s]["values"]["match_rate"]),
          pct(r["b"]["by_sheet"][s]["values"]["match_rate"]),
          _delta(r["a"]["by_sheet"][s]["values"]["match_rate"], r["b"]["by_sheet"][s]["values"]["match_rate"])]
         for s in SHEETS if r["a"]["by_sheet"][s]["values"]["n"]]), ""]

    groups = sorted(set(r["a"]["core_groups"]) | set(r["b"]["core_groups"]))
    lines += ["## Core fields (match)", "", _table(
        ["field", "in record", la, lb],
        [[k, (r["a"]["core_groups"].get(k) or r["b"]["core_groups"][k])["n"], pct((r["a"]["core_groups"].get(k) or {}).get("match_rate")),
          pct((r["b"]["core_groups"].get(k) or {}).get("match_rate"))] for k in groups]), ""]

    lines += ["## By paper (values that match)", "", _table(
        ["paper", "values", la, lb, "B − A"],
        [[k, sa["n"], pct(sa["match_rate"]), pct(sb["match_rate"]), _delta(sa["match_rate"], sb["match_rate"])]
         for k, (sa, sb) in r["by_paper"].items()]), ""]

    lines += [f"## Where they disagree (first {examples} of {len(r['differing'])})", "",
              _table(["paper", "sheet", "row", "column", "record", f"{la}", f"{lb}"],
                     [[k[0][:24], k[2], _short(k[3], 30), k[4], _short(ca_.gt, 24),
                       f"{_short(ca_.ex, 24)} ({ca_.status})", f"{_short(cb_.ex, 24)} ({cb_.status})"]
                      for k, ca_, cb_ in r["differing"][:examples]]), ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
