"""Match PDFs to device workbooks by DOI.

PDF file names are arbitrary, so the DOI is searched in the PDF's metadata and first pages.
Both sides are reduced to a key of lowercase letters and digits, which also survives DOIs
that the PDF text layer wraps across lines (``10.1\\n016/j.jpowsour...``).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .paper import load_paper
from .workbook import parse_device_name


def doi_key(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


SI_DIR = "SI"


def local_si(pdf_dir, key: str) -> list[Path]:
    """The supporting-information PDFs of the paper with this doi key, found in ``<pdf_dir>/SI/<key>*.pdf``."""
    folder = Path(pdf_dir) / SI_DIR
    if not key or not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.pdf") if doi_key(p.stem).startswith(key))


@dataclass
class PaperPair:
    key: str
    pdf: Path
    workbooks: list[Path]
    duplicate_pdfs: list[Path] = field(default_factory=list)  # same DOI, not used
    pdf_doi: str | None = None  # set when paired despite a one-character typo in the workbook names
    si: list[Path] = field(default_factory=list)  # supporting-information PDFs found in <pdf folder>/SI/


@dataclass
class Pairing:
    pairs: list[PaperPair]
    unmatched_pdfs: list[Path]
    workbooks_without_pdf: dict[str, list[Path]]


def match_key(text: str, keys) -> str | None:
    """The workbook key that appears earliest in ``text`` (longest wins on ties)."""
    t = doi_key(text)
    hits = [(t.find(k), -len(k), k) for k in keys if k and k in t]
    return min(hits)[2] if hits else None


def pair_folders(raw_dir, pdf_dir) -> Pairing:
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(Path(raw_dir).glob("*.xlsx")):
        if not p.name.startswith("~$"):
            groups[doi_key(parse_device_name(p.name)[1])].append(p)

    by_key: dict[str, list[Path]] = defaultdict(list)
    unmatched = []
    for pdf in sorted(Path(pdf_dir).glob("*.pdf")):
        paper = load_paper(pdf)
        k = match_key(paper.head_text(), groups)
        (by_key[k].append(pdf) if k else unmatched.append((pdf, paper.dois())))

    # Second pass: workbook names with a one-character DOI typo ("10.1016.jpowsour" for
    # 10.1016/j.jpowsour). Only PDFs and workbooks left without a partner are considered.
    typo: dict[str, str] = {}
    still = []
    for pdf, dois in unmatched:
        free = [k for k in groups if k not in by_key]
        hit = next(((k, d) for d in dois for k in free if _one_edit(doi_key(d), k)), None)
        if hit:
            by_key[hit[0]].append(pdf)
            typo[hit[0]] = hit[1]
        else:
            still.append(pdf)

    pairs = []
    for k, pdfs in sorted(by_key.items()):
        pdfs = sorted(pdfs, key=lambda p: -p.stat().st_size)  # keep the largest copy
        pairs.append(PaperPair(k, pdfs[0], groups[k], pdfs[1:], typo.get(k), local_si(pdf_dir, k)))
    return Pairing(pairs, still, {k: v for k, v in groups.items() if k not in by_key})


def _one_edit(a: str, b: str) -> bool:
    """True if a and b differ by exactly one inserted, deleted or substituted character."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return a[i + 1:] == b[i + 1:] or a[i + 1:] == b[i:] or a[i:] == b[i + 1:]
