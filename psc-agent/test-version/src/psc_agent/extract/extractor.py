"""Extract database rows from a paper that has no workbooks yet: paper map first, then rows per sheet group.

    mapper    (Big model)    the paper map: every device (control first) with its layer stack
    extract   (any model)    rows of the other sheets for all devices, each with page + quote
    hooks                    after_answer → QuoteCheckHook marks quotes that are not on the cited page
    decision  (small model)  accept / verify_more / revise per row against its evidence; rules first:
                             no quote / quote not on the page / value read off a figure → verify_more,
                             and table values reach the model only if every number is in the page text

Writes ``<out>/<doi>_Sample<N>.xlsx`` (Raw Data layout, drafts for review), review.csv (every row with
evidence and verdict) and extraction.json.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..core.decision import Verdict, decide, rule
from ..core.evidence import EvidenceRow, normalize_text
from ..core.hooks import HookBlock
from ..core.openrouter import OpenRouterError
from ..core.runtime import Runtime, Usage
from ..sources.paper import Paper, load_paper, paper_parts
from ..sources.si import si_hint
from ..sources.workbook import STANDARD_HEADERS, Source, write_workbook

log = logging.getLogger(__name__)
FAILURES = (OpenRouterError, ValueError, HookBlock)
GROUPS = (("Vender", "Materials"), ("Process",), ("Properties", "Stability"))


class XRow(EvidenceRow):
    device: str
    sheet: str


class XDevice(BaseModel):
    sample: str
    paper_name: str | None = None
    description: str | None = None


class MapAnswer(BaseModel):
    devices: list[XDevice]
    rows: list[XRow] = []


class RowsAnswer(BaseModel):
    rows: list[XRow] = []


class ExtractResult(BaseModel):
    paper: str
    doi: str | None = None
    devices: list[XDevice] = Field(default_factory=list)
    rows: list[XRow] = Field(default_factory=list)
    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    si_link: str = ""  # where to download the SI when none was loaded: recipes and suppliers are usually only there
    si_save_as: str = ""
    usage: Usage = Field(default_factory=Usage)
    errors: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.verdicts.values():
            out[v.choice] = out.get(v.choice, 0) + 1
        return out


def _schema(name: str, sheets: list[str], with_devices: bool) -> dict:
    columns = sorted({c for s in sheets for c in STANDARD_HEADERS[s]})
    row = {"type": "object", "additionalProperties": False,
           "required": ["device", "sheet", "values", "page", "source", "evidence", "note"],
           "properties": {
               "device": {"type": "string"}, "sheet": {"type": "string", "enum": sheets},
               "values": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["column", "value"],
                   "properties": {"column": {"type": "string", "enum": columns}, "value": {"type": ["string", "null"]}}}},
               "page": {"type": ["integer", "null"]},
               "source": {"type": "string", "enum": ["text", "table", "figure", "none"]},
               "evidence": {"type": ["string", "null"]}, "note": {"type": ["string", "null"]}}}
    props = {"rows": {"type": "array", "items": row}}
    if with_devices:
        props["devices"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["sample", "paper_name", "description"],
            "properties": {"sample": {"type": "string"}, "paper_name": {"type": ["string", "null"]},
                           "description": {"type": ["string", "null"]}}}}
    return {"name": name, "strict": True, "schema": {"type": "object", "additionalProperties": False,
                                                    "required": list(props), "properties": props}}


def _columns_text(sheets) -> str:
    return "\n".join(f'SHEET "{s}" columns: {" | ".join(STANDARD_HEADERS[s])}' for s in sheets)


def _values_on_page(row: XRow, pages: list[str]) -> tuple[bool, str]:
    """Every number of the row's cells is in the cited page's text. Labels (a property name, a unit, a remark)
    are not looked up, only the numbers. Returns (all found, snippets around them) for the decision model."""
    if not row.page or not 1 <= row.page <= len(pages):
        return False, ""
    text = normalize_text(pages[row.page - 1])
    snippets = []
    for value in row.cells().values():
        for needle in re.findall(r"\d+(?:\.\d+)?", str(value)):
            i = text.find(needle)
            if i < 0:
                return False, ""
            snippets.append(text[max(0, i - 80): i + 80])
    return bool(snippets), " … ".join(dict.fromkeys(snippets))[:1500]


class Extractor:
    """``decide_rows``: run the decision model on the rows (rules always run). ``images``: send page images too, not only the text."""

    def __init__(self, runtime: Runtime, *, dpi: int = 150, all_pages: bool = False, decide_rows: bool = True,
                 images: bool = True):
        self.rt, self.dpi, self.all_pages, self.decide_rows, self.images = runtime, dpi, all_pages, decide_rows, images

    def extract(self, paper: Paper | Source, out_dir) -> ExtractResult:
        paper = paper if isinstance(paper, Paper) else load_paper(paper)
        out = Path(out_dir)
        result = ExtractResult(paper=paper.name, doi=paper.doi)
        result.si_link, result.si_save_as = si_hint(paper)
        run = {"kind": "extract", "paper": paper.name}
        self.rt.hooks.emit("run_start", run=run, runtime=self.rt)
        parts = paper_parts(paper, dpi=self.dpi, all_pages=self.all_pages, images=self.images, pdf=self.rt.native_pdf)
        total = 2 + len(GROUPS)  # map, one call per sheet group, the verdicts
        tick = lambda done: self.rt.hooks.emit("progress", scope=paper.name, done=done, total=total)  # noqa: E731
        try:
            self._map(paper, parts, result)
            tick(1)
        except FAILURES as e:  # without the device map nothing else can be asked
            result.errors.append(f"map: {e}")
            self.rt.hooks.emit("run_end", run=run, summary={"errors": result.errors})
            return result
        for i, group in enumerate(GROUPS, 2):
            try:
                self._rows(paper, parts, group, result)
            except FAILURES as e:
                result.errors.append(f"{'/'.join(group)}: {e}")
                log.error("%s: %s", paper.name, e)
            tick(i)
        result.verdicts = self._decide(paper, result)
        tick(total)
        self._write(paper, out, result)
        self.rt.hooks.emit("run_end", run=run, summary={"devices": len(result.devices), "rows": len(result.rows),
                                                        "verdicts": result.counts(), "errors": len(result.errors)})
        return result

    def _map(self, paper: Paper, parts, result: ExtractResult) -> None:
        task = ("Build the paper map: every device the paper reports (S1, S2, ... control first) with how the paper "
                "names it and a one-line description of what differs, plus its Layer rows. Layer rows use the lab's fixed "
                "slots (workbook-schema): LayerID 1 Anode, 2 HTL, 3 HTL treatment, 4 PSK, 5 PSK treatment, 6 ETL, "
                "7 ETL treatment, 8 Cathode. Give a row for each slot the device uses, never renumber, and leave the glass "
                f"substrate out.\n{_columns_text(['Layer'])}\n"
                "Return JSON: devices [{sample, paper_name, description}] and rows (sheet = Layer).")
        ans = self.rt.ask_json("mapper", [*parts, {"type": "text", "text": task}], MapAnswer,
                               schema=_schema("paper_map", ["Layer"], True), usage=result.usage)
        if not ans.devices:
            raise ValueError("the paper map lists no devices")
        self.rt.hooks.emit("after_answer", kind="extract-map", paper=paper, rows=ans.rows)
        result.devices = ans.devices
        result.rows += [r for r in ans.rows if r.sheet == "Layer"]

    def _rows(self, paper: Paper, parts, group, result: ExtractResult) -> None:
        devices = "\n".join(f"{d.sample} = {d.paper_name or '?'}: {d.description or ''}" for d in result.devices)
        stacks = "\n".join(f"{d.sample}: " + " / ".join(f"slot {r.cells().get('LayerID', '?')} {r.cells().get('Material', '?')}"
                                                         for r in result.rows if r.device == d.sample and r.sheet == "Layer")
                           for d in result.devices)
        task = (f"DEVICES (the paper map):\n{devices}\n\nLAYER STACKS:\n{stacks}\n\n{_columns_text(group)}\n\n"
                f"LayerID is the slot number shown above.\nGive the rows of these sheets for every device: {', '.join(group)}. "
                "Return JSON: rows.")
        ans = self.rt.ask_json("extract", [*parts, {"type": "text", "text": task}], RowsAnswer,
                               schema=_schema("sheet_rows", list(group), False), usage=result.usage)
        known = {d.sample for d in result.devices}
        rows = [r for r in ans.rows if r.device in known and r.sheet in group]
        self.rt.hooks.emit("after_answer", kind="extract", paper=paper, rows=rows)
        result.rows += rows

    def _decide(self, paper: Paper, result: ExtractResult) -> dict[str, Verdict]:
        verdicts, items = {}, []
        for i, r in enumerate(result.rows):
            rid = f"{r.device}:{r.sheet}:{i}"
            claim = f"{r.device} {r.sheet}: " + "; ".join(f"{k}={v}" for k, v in r.cells().items())
            if r.quote_ok is False:
                verdicts[rid] = rule(rid, "verify_more", f"quote not found on page {r.page}")
            elif r.source == "figure":
                verdicts[rid] = rule(rid, "verify_more", f"read off a figure ({r.evidence or 'no label'}): check it")
            elif r.quote_ok is None:  # a table label or no quote: the values themselves must be on the page
                found, snippet = _values_on_page(r, paper.pages)
                if not found:
                    verdicts[rid] = rule(rid, "verify_more", f"values not found in the text of page {r.page}")
                else:
                    items.append({"id": rid, "claim": claim, "evidence": f"page {r.page} [{r.source}] {r.evidence or ''}"
                                                                          f"\npage text: {snippet}"})
            else:
                items.append({"id": rid, "claim": claim,
                              "evidence": f'page {r.page} [{r.source}] "{r.evidence}" (quote verified on the page)'})
        if items and self.decide_rows:
            try:
                verdicts.update(decide(self.rt, "extract", items, usage=result.usage))
            except FAILURES as e:
                result.errors.append(f"decision: {e}")
        for it in items:
            verdicts.setdefault(it["id"], rule(it["id"], "verify_more", "not judged"))
        return verdicts

    def _write(self, paper: Paper, out: Path, result: ExtractResult) -> None:
        stem = (paper.doi or Path(paper.name).stem[:40].strip()).replace("/", "_")  # a long title would pass Windows' path limit
        for n, d in enumerate(result.devices, 1):
            sheets: dict[str, list[dict]] = {}
            for r in result.rows:
                if r.device == d.sample:
                    sheets.setdefault(r.sheet, []).append(r.cells())
            for name, key in (("Layer", ("LayerID",)), ("Process", ("LayerID", "StepOrder"))):
                sheets.get(name, []).sort(key=lambda c: tuple(_as_float(c.get(k)) for k in key))
            result.files.append(str(write_workbook(sheets, out / f"{stem}_Sample{n}.xlsx")))
        with open(out / "review.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["row_id", "device", "sheet", "values", "page", "source", "evidence", "quote_ok", "decision",
                        "p", "reason", "note", "si_link", "si_save_as"])
            for i, r in enumerate(result.rows):
                rid = f"{r.device}:{r.sheet}:{i}"
                v = result.verdicts.get(rid)
                w.writerow([rid, r.device, r.sheet, json.dumps(r.cells(), ensure_ascii=False), r.page, r.source,
                            r.evidence, r.quote_ok, v.choice if v else "", v.p if v else "", v.reason if v else "",
                            r.note, result.si_link, result.si_save_as])
        (out / "extraction.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")


def _as_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("inf")
