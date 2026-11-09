"""Supporting information (SI): where it lives on disk, and an agent that finds it on the web.

On disk the SI of a paper is ``<pdf folder>/SI/<doi key>*.pdf`` (the doi key is the DOI reduced to lowercase letters
and digits, e.g. ``101039d3lf00258f_SI.pdf``). Put files there by hand, or let ``SiFinder`` download them. Pairing
picks them up, and every workflow then reads the SI after the main text, as extra pages of the same paper.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from ..core.agent import run_tools
from ..core.browser import Browser, browser_tools
from ..core.openrouter import OpenRouterError
from ..core.runtime import Runtime, Usage, parse_json
from ..core.web import Web, web_tools
from .pairing import SI_DIR, doi_key, local_si  # noqa: F401  (re-exported)
from .paper import Paper

log = logging.getLogger(__name__)


# Supplementary-material pages that publishers serve at a fixed address (a browser opens them; scripts get 403).
_SUPPL_PAGE = {"10.1021": "https://pubs.acs.org/doi/suppl/{doi}", "10.1002": "https://onlinelibrary.wiley.com/doi/suppl/{doi}"}


def supplement_links(doi: str | None) -> list[str]:
    """Where a person can fetch the SI: the publisher's supplementary page when its address is known, and the DOI link
    (the article page, whose 'Supporting Information' section holds the files)."""
    if not doi:
        return []
    pattern = _SUPPL_PAGE.get(doi.split("/")[0])
    return ([pattern.format(doi=doi)] if pattern else []) + [f"https://doi.org/{doi}"]


def si_hint(paper: Paper) -> tuple[str, str]:
    """(link to open, file name to save it as) for a paper whose SI is not loaded; ("", "") when it is loaded or the
    paper has no DOI. The name is what pairing looks for in ``<pdf folder>/SI/``."""
    if paper.si_pages or not paper.doi:
        return "", ""
    return supplement_links(paper.doi)[0], f"{doi_key(paper.doi)}_SI.pdf"


class _Answer(BaseModel):
    status: str = "not_found"
    notes: str = ""
    links: list[str] = []  # addresses of the SI files or their page, also of files that could not be downloaded


@dataclass
class SiResult:
    key: str
    status: str  # found | links (urls-only: addresses the agent saw) | blocked | not_found | error | manual (no search) | printed | pattern | have | not_referenced (see si_stage)
    files: list[Path] = field(default_factory=list)
    notes: str = ""
    doi: str | None = None
    links: list[str] = field(default_factory=list)  # where to fetch the SI by hand
    usage: Usage = field(default_factory=Usage)


class _Reading(BaseModel):
    refers_to_si: bool
    evidence: str = ""  # the sentence of the paper that says so
    address: str = ""  # an address for the SI printed in the paper
    holds: str = ""  # what the paper says the SI contains


_READING_SCHEMA = {"name": "si_reading", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["refers_to_si", "evidence", "address", "holds"],
    "properties": {"refers_to_si": {"type": "boolean"}, "evidence": {"type": "string"}, "address": {"type": "string"},
                   "holds": {"type": "string"}}}}


@dataclass
class SiReading:
    refers: bool  # does the paper refer to supporting information published apart from it?
    evidence: str = ""
    address: str = ""  # printed in the paper, whitespace from the PDF's line breaks removed
    holds: str = ""
    notes: str = ""  # set when the model failed and the text rule decided
    usage: Usage = field(default_factory=Usage)


class SiReader:
    """The SI extractor: a model (role ``si_reader``, no tools) reads the paper's own text and answers whether the paper
    refers to supporting information, quoting the sentence, with the SI address when the paper prints one."""

    def __init__(self, rt: Runtime):
        self.rt = rt

    def read(self, paper: Paper) -> SiReading:
        main = len(paper.pages) if paper.n_main is None else paper.n_main  # an appended SI is not the paper's reference to it
        text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(paper.pages[:main], 1))
        usage = Usage()
        try:
            ans = self.rt.ask_json("si_reader", text, _Reading, schema=_READING_SCHEMA, max_tokens=800, usage=usage)
        except (OpenRouterError, ValueError) as e:  # a model hiccup must not lose the paper: the text rule decides
            return SiReading(mentions_si(paper), notes=f"model failed, the text rule decided: {type(e).__name__}: {e}", usage=usage)
        address = re.sub(r"\s+", "", ans.address).rstrip(".,;)")
        if address and address not in re.sub(r"\s+", "", text):  # an address the paper does not print is dropped
            address = ""
        return SiReading(ans.refers_to_si, ans.evidence.strip(), address, ans.holds.strip(), usage=usage)


class SiFinder:
    """An agent with web tools (role ``si_finder``) that searches for a paper's SI and saves the PDF."""

    def __init__(self, rt: Runtime, *, web: Web | None = None, browser: Browser | None = None, max_steps: int = 12,
                 download: bool = True):
        """``browser``: the person's signed-in Chrome (core.browser). With it the agent can open the publishers' pages.
        ``download=False``: urls only. The agent gets no download tool and answers with the addresses of the SI (the file,
        or the page that lists it) for a person to open; only addresses it actually saw in a result are kept."""
        self.rt, self.web, self.browser, self.max_steps, self.download = rt, web or Web(), browser, max_steps, download

    def find(self, paper: Paper, dest_dir, key: str | None = None) -> SiResult:
        doi = paper.doi
        key = key or doi_key(doi or paper.name)
        res = SiResult(key, "error", doi=doi, links=supplement_links(doi))
        head = re.sub(r"\s+", " ", " ".join(paper.pages[:1]))[:900]
        goal = ("Find its supporting information and download it." if self.download else
                "Find the address of its supporting information and answer with it in `links`: the direct address of the file "
                "when a page shows it, else the page that lists the supplementary files. Do not download anything.")
        task = f"Paper DOI: {doi or 'unknown'}\nStart of its first page: {head}\n\n{goal}"
        saved: list[Path] = []
        tools = web_tools(self.web, Path(dest_dir), key, saved)
        if self.browser:
            tools += browser_tools(self.browser, Path(dest_dir), key, saved)
        if not self.download:
            tools = [t for t in tools if t.name not in ("download_pdf", "browser_download")]
        messages = [{"role": "system", "content": self.rt.system("si_finder")}, {"role": "user", "content": task}]
        try:
            loop = run_tools(self.rt, "si_finder", messages, tools, max_steps=self.max_steps, usage=res.usage)
            ans = _Answer.model_validate(parse_json(loop.final))
            seen = " ".join(c.result for c in loop.calls)  # only addresses that a page or search result actually showed
            seen_links = [u.rstrip(".,;)") for u in ans.links if u.rstrip(".,;)") in seen]
            res.links = list(dict.fromkeys(seen_links + res.links))
        except (OpenRouterError, ValueError) as e:
            res.notes = f"{type(e).__name__}: {e}"
            res.files = saved
            res.status = "found" if saved else "error"
            return res
        res.files = saved
        if saved:
            res.status = "found"
        elif not self.download and seen_links:
            res.status = "links"
        else:
            res.status = "blocked" if ans.status == "blocked" else "not_found"
        res.notes = ans.notes
        log.info("%s: SI %s (%d file(s)) %s", key, res.status, len(saved), ans.notes[:120])
        return res


# ---------------------------------------------------------------- the cheapest steps first

_SI_PHRASE = re.compile(r"supporting information|supplementa(?:ry|l) (?:information|materials?|data|files?|figures?|tables?)|"
                        r"electronic supplementary", re.I)
_ESI = re.compile(r"\bESI\b")
# "Figure S3", "Figures S1 and S2", "Table S1, S3": the S-numbers of a figure / table reference (a list continues after , & and to)
_SI_REF = re.compile(r"\b(figures?|figs?\.?|tables?)\s*S(\d{1,2})((?:\s*(?:[,&–-]|and|to)\s*S?\d{1,2}\b)*)", re.I)


def mentions_si(paper: Paper) -> bool:
    """Does the paper itself refer to supporting information? Read from its own text, no model: the words "Supporting
    Information" / "supplementary material" / "ESI", or at least two different "Figure S3" / "Table S1" references. A paper
    that never does has no SI worth searching for. Only the main pages count (not an SI already appended)."""
    main = len(paper.pages) if paper.n_main is None else paper.n_main
    text = " ".join(paper.pages[:main])
    if _SI_PHRASE.search(text) or _ESI.search(text):
        return True
    refs = set()
    for m in _SI_REF.finditer(text):
        refs |= {(m.group(1)[0].lower(), n) for n in [m.group(2), *re.findall(r"\d+", m.group(3))]}
    return len(refs) >= 2


def intake_key(paper: Paper, pdf) -> str:
    """The name an SI file is saved under for a paper that may have no workbooks: its DOI as lowercase letters and digits,
    else the start of the PDF's file name."""
    return doi_key(paper.doi) if paper.doi else doi_key(Path(pdf).stem)[:40]


def has_pattern(doi: str | None) -> bool:
    """Is the supplementary page of this DOI's publisher at an address that follows from the DOI alone?"""
    return bool(doi) and doi.split("/")[0] in _SUPPL_PAGE


def si_stage(paper: Paper, key: str, si_dir, have: list[Path], finder: SiFinder | None, reader: SiReader | None = None) -> SiResult:
    """What to do about one paper's SI, cheapest answer first:

    have            SI files are already in the SI/ folder: nothing to do
    not_referenced  the paper never mentions an SI: skip. Decided by ``reader`` (a model reading the paper) when given,
                    else by the text rule ``mentions_si`` (no model)
    printed         the paper prints an address for its SI (only ``reader`` can pull it out): that link is enough
    pattern         the publisher's supplementary page follows from the DOI (ACS, Wiley): that link is enough
    <finder result> any other publisher: the SI agent searches (status found / links / blocked / not_found / error)
    manual          no agent given: the DOI link, for a person to open
    """
    doi = paper.doi
    if have:
        return SiResult(key, "have", files=list(have), doi=doi)
    reading = reader.read(paper) if reader else None
    if not (reading.refers if reading else mentions_si(paper)):
        said = f' (the paper says: "{reading.evidence}")' if reading and reading.evidence else ""
        return SiResult(key, "not_referenced", doi=doi, notes="the paper never refers to supporting information" + said,
                        usage=reading.usage if reading else Usage())
    if reading and reading.address:
        return SiResult(key, "printed", doi=doi, links=list(dict.fromkeys([reading.address, *supplement_links(doi)])),
                        notes=reading.holds or "the paper prints the address", usage=reading.usage)
    if has_pattern(doi):
        res = SiResult(key, "pattern", doi=doi, links=supplement_links(doi), notes="publisher page from the DOI pattern")
    elif finder is None:
        res = SiResult(key, "manual", doi=doi, links=supplement_links(doi))
    else:
        res = finder.find(paper, si_dir, key=key)
    if reading:
        res.usage.merge(reading.usage)
        res.notes = "; ".join(n for n in (res.notes, reading.notes) if n)
    return res


NO_DOWNLOAD_NEEDED = ("found", "have", "not_referenced")


def write_si_reports(rows: list[tuple[str, str, SiResult]], out, si_dir) -> list[tuple[str, str, SiResult]]:
    """``si_report.csv`` (one line per paper) and ``si_links.md`` (what to download by hand, with the file name to save it
    as) under ``out``. ``rows`` = (key, pdf name, result). Returns the rows that still need a download by hand."""
    out, si_dir = Path(out), Path(si_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "si_report.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["paper", "pdf", "doi", "status", "saved_files", "links", "save_as", "notes", "cost_usd"])
        for key, pdf, r in rows:
            w.writerow([key, pdf, r.doi or "", r.status, "; ".join(f.name for f in r.files), "\n".join(r.links),
                        f"{key}_SI.pdf", r.notes, round(r.usage.cost_usd, 4)])
    by_hand = [row for row in rows if row[2].status not in NO_DOWNLOAD_NEEDED]
    lines = [f"# Supporting information to download by hand ({len(by_hand)} of {len(rows)} papers)", "",
             f"Save each PDF in `{si_dir}` under the name shown. Then `recheck`, `extract` and `evaluate` read it.", ""]
    for key, pdf, r in by_hand:
        stem = Path(pdf).stem
        lines += [f"## {stem}", f"- save as: `{key}_SI.pdf`" + (f" (several files: `{key}_SI2.pdf` ...)" if r.status not in ("manual", "pattern", "printed") else ""),
                  *[f"- {u}" for u in r.links], *([f"- note: {r.notes}"] if r.notes else []), ""]
    (out / "si_links.md").write_text("\n".join(lines), encoding="utf-8")
    return by_hand
