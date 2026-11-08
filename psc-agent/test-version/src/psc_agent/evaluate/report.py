"""Turn per-paper evaluations into the numbers and the report: how well does the extracted data match the record?"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .compare import SHEETS, Cell
from .run import CELL_FIELDS, PaperEval
from .text import property_key

RECORDED = ("match", "review", "mismatch", "unit_mismatch", "not_found")  # verdicts on hand-entered cells


def stats(cells: list[Cell]) -> dict:
    """Counts and rates over the hand-entered cells of ``cells`` (``extra`` cells are counted, not rated)."""
    c = {s: 0 for s in (*RECORDED, "extra")}
    likely = likely_match = 0
    for x in cells:
        c[x.status] += 1
        if x.status in RECORDED and x.likely:
            likely += 1
            likely_match += x.status == "match"
    n = sum(c[s] for s in RECORDED)
    found = n - c["not_found"]
    rate = lambda a, b: a / b if b else None  # noqa: E731
    return {**c, "n": n, "found": found, "match_rate": rate(c["match"], n), "lenient_rate": rate(c["match"] + c["review"], n),
            "found_rate": rate(found, n), "accuracy_when_found": rate(c["match"], found), "likely": likely,
            "likely_share": rate(likely, n), "match_rate_likely": rate(likely_match, likely)}


def pct(v, digits: int = 0) -> str:
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def _frac(s: dict, key: str = "match", total: str = "n") -> str:
    return "–" if not s[total] else f"{pct(s[key] / s[total])} ({s[key]}/{s[total]})"


def _core_group(x: Cell) -> str:
    return property_key(x.row)[0].upper() if x.sheet == "Properties" else x.row


def _table(headers: list[str], rows: list[list]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                     + ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows])


def summarize(evals: list[PaperEval]) -> dict:
    cells = [c for ev in evals for c in ev.cells]
    value = [c for c in cells if c.kind == "value"]
    rows = [c for c in cells if c.kind == "row"]
    core = [c for c in cells if c.core]
    groups: dict[str, list[Cell]] = defaultdict(list)
    for c in core:
        groups[_core_group(c)].append(c)
    return {"papers": len(evals), "values": stats(value), "rows": stats(rows), "core": stats(core),
            "by_sheet": {s: {"rows": stats([c for c in rows if c.sheet == s]), "values": stats([c for c in value if c.sheet == s])}
                         for s in SHEETS},
            "core_groups": {k: stats(v) for k, v in sorted(groups.items())},
            "by_paper": {ev.key: {"rows": stats([c for c in ev.cells if c.kind == "row"]),
                                  "values": stats([c for c in ev.cells if c.kind == "value"])} for ev in evals}}


def write_report(evals: list[PaperEval], out: str | Path, meta: dict | None = None) -> Path:
    """Write ``cells_all.csv`` and ``report.md`` under ``out``; returns the report path."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    s = summarize(evals)
    with open(out / "cells_all.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, ["paper", *CELL_FIELDS])
        w.writeheader()
        for ev in evals:
            w.writerows({"paper": ev.key, **asdict(c)} for c in ev.cells)

    v, r = s["values"], s["rows"]
    tokens_in = sum(ev.usage.get("prompt_tokens", 0) for ev in evals)
    tokens_out = sum(ev.usage.get("completion_tokens", 0) for ev in evals)
    cost = sum(ev.usage.get("cost_usd", 0.0) for ev in evals)
    calls = sum(ev.usage.get("calls", 0) for ev in evals)
    failed = [ev for ev in evals if ev.errors]
    lines = [f"# Extraction vs hand-entered workbooks ({date.today().isoformat()})", ""]
    lines.append(
        f"Provider **{meta.get('provider', '?')}** · model `{meta.get('model', '?')}` · {s['papers']} papers · "
        f"{calls} model calls · {tokens_in:,} tokens in, {tokens_out:,} out · ${cost:.2f} "
        f"({'API-equivalent price, charged to the plan' if meta.get('provider') == 'claude-code' else 'billed'}) · "
        f"numeric tolerance {meta.get('tol', 0.01):.0%} plus rounding to either side's precision")
    lines += ["", "## Headline", "",
              f"- **{pct(v['match_rate'])}** of the {v['n']} hand-entered values match what the model extracted from the PDF alone "
              f"({pct(v['lenient_rate'])} if text that may mean the same is counted).",
              f"- The model gave a value for {pct(v['found_rate'])} of them, and **{pct(v['accuracy_when_found'])}** of those are correct.",
              f"- {pct(r['match_rate'])} of the {r['n']} hand-entered rows (a layer, material, process step, property or test) were found.",
              f"- Only {pct(v['likely_share'])} of the hand-entered values appear in the PDF text at all (the rest comes from the "
              f"supporting information or the curator's own notes), so the share of *findable* values that match is "
              f"**{pct(v['match_rate_likely'])}**. That, not the first number, measures the extractor.", ""]

    lines += ["## By sheet", "", _table(
        ["sheet", "rows found", "values in record", "values match", "model gave a value", "correct when given",
         "in the PDF text", "match, of those in the PDF text", "extra values"],
        [[sh, _frac(d["rows"]), d["values"]["n"], _frac(d["values"]), pct(d["values"]["found_rate"]),
          pct(d["values"]["accuracy_when_found"]), pct(d["values"]["likely_share"]), pct(d["values"]["match_rate_likely"]),
          d["values"]["extra"]] for sh, d in s["by_sheet"].items() if d["rows"]["n"] or d["values"]["n"]]), ""]

    lines += ["## Core fields (champion PCE, Voc, Jsc, FF and the layer materials)", "", _table(
        ["field", "in record", "match", "review", "mismatch", "unit mismatch", "not found"],
        [[k, d["n"], _frac(d), d["review"], d["mismatch"], d["unit_mismatch"], d["not_found"]]
         for k, d in s["core_groups"].items()]), ""]

    lines += ["## By paper", "", _table(
        ["paper", "devices (record / paired)", "rows found", "values match", "values match, of those in the PDF text", "calls", "seconds", "errors"],
        [[ev.key, f"{sum(1 for d in ev.devices if d['record'])} / {sum(1 for d in ev.devices if d['record'] and d['extracted'])}",
          _frac(s["by_paper"][ev.key]["rows"]), _frac(s["by_paper"][ev.key]["values"]),
          pct(s["by_paper"][ev.key]["values"]["match_rate_likely"]), ev.usage.get("calls", ""), ev.seconds,
          "; ".join(ev.errors)[:80]] for ev in evals]), ""]

    audited = [c for ev in evals for c in ev.cells if c.audit_verdict]
    if audited:
        v_counts: dict[str, int] = defaultdict(int)
        for c in audited:
            v_counts[c.audit_verdict] += 1
        n_aud = len(audited)
        adj_matches = v["match"] + v_counts["extractor_correct"] + v_counts["equivalent"]
        lines += ["", "## Auditor Discrepancy Resolution", "",
                  f"The auditor agent inspected **{n_aud}** non-matching values against the paper text and figures:", "",
                  _table(["auditor verdict", "count", "share of audited", "meaning"], [
                      ["extractor_correct", v_counts["extractor_correct"], pct(v_counts["extractor_correct"] / n_aud),
                       "Extractor matches paper; human record omitted/misrecorded"],
                      ["human_correct", v_counts["human_correct"], pct(v_counts["human_correct"] / n_aud),
                       "Human record is right; extractor made a mistake"],
                      ["equivalent", v_counts["equivalent"], pct(v_counts["equivalent"] / n_aud),
                       "Both mean the same (synonym, format, or unit)"],
                      ["si_only", v_counts["si_only"], pct(v_counts["si_only"] / n_aud),
                       "Data lives in Supporting Information, not main PDF"],
                      ["ambiguous", v_counts["ambiguous"], pct(v_counts["ambiguous"] / n_aud),
                       "Paper text is ambiguous or conflicting"],
                  ]), "",
                  f"- **Adjusted Match Rate**: **{pct(adj_matches / v['n'])}** "
                  f"({adj_matches}/{v['n']} values match or verified correct by the auditor, up from {pct(v['match_rate'])}).", ""]
        sample_aud = [c for c in audited if c.audit_verdict != "si_only"][:30]
        if sample_aud:
            lines += ["### Auditor Resolutions (sample)", "", _table(
                ["sheet", "row", "column", "recorded", "extracted", "verdict", "resolved", "explanation"],
                [[c.sheet, c.row[:24], c.column, f"{c.gt} {c.gt_unit or ''}".strip(),
                  f"{c.ex} {c.ex_unit or ''}".strip(), c.audit_verdict, c.audit_resolved or "-",
                  (c.audit_explanation or "")[:60]] for c in sample_aud]), ""]

    miss = [(ev.key, c) for ev in evals for c in ev.cells
            if c.kind == "value" and c.status in ("mismatch", "unit_mismatch", "review")]
    miss.sort(key=lambda kc: (kc[1].status != "mismatch", kc[1].sheet))
    lines += [f"## Values that differ ({len(miss)}; first 40)", "", _table(
        ["paper", "sheet", "row", "column", "record", "extracted", "verdict", "detail"],
        [[key[:28], c.sheet, c.row[:36], c.column, f"{c.gt} {c.gt_unit or ''}".strip(), f"{c.ex} {c.ex_unit or ''}".strip(),
          c.status, (c.detail or "")[:70]] for key, c in miss[:40]]), ""]

    lines += ["## How to read this", "",
              "- *Record* = the hand-entered workbooks in `Raw Data/`. The model never saw them. Devices are paired by layer stack, "
              "rows by layer slot, process type, property name, material or test condition.",
              "- *match* = same value after unit conversion and rounding; *review* = text that may mean the same (not counted as a match); "
              "*not found* = the model gave nothing for that cell or row; *extra* = the model gave a value the curator did not record "
              "(not an error).",
              "- *In the PDF text* is optimistic: a number counts if it appears anywhere in the paper, a name if most of its words do. "
              "Suppliers, spin recipes and purities mostly live in the supporting information, which is not in the PDFs.",
              "- Subscription runs report the API-equivalent price of the tokens used; no money is charged."]
    if failed:
        lines += ["", "## Errors", ""] + [f"- {ev.key}: {'; '.join(ev.errors)}" for ev in failed]
    path = out / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
