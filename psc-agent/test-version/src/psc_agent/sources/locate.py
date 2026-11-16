"""Find an evidence quote on a PDF page, so the review page can point at it (no searching by hand).

Matching runs on letters and digits only (lowercase, NFKC), over the page's words joined together: a quote whose spaces,
line breaks, hyphenation or superscripts differ from the PDF's text layer is still found. When the whole quote is not on
the page, runs of five of its words are looked up (a model often trims or joins a sentence), and last the numbers of the
extracted values. Each hit is returned as rectangles in PDF points, one per line of text.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import fitz

from .paper import _FITZ_LOCK

WINDOW = 5  # words per run when the whole quote is not on the page
MIN_CHARS = 4  # shorter needles match by chance


def squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKC", str(text)).lower())


def value_terms(values) -> list[str]:
    """The numbers of a row's cells (the JSON of review.csv's ``values``), longest first: the last thing to look for."""
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            values = {"v": values}
    nums = {n for v in (values or {}).values() for n in re.findall(r"\d+(?:\.\d+)?", str(v))}
    return sorted(nums, key=len, reverse=True)


def _find(words: list, quote: str, terms) -> tuple[set[int], str]:
    cat, owner = [], []
    for i, w in enumerate(words):
        s = squash(w[4])
        cat.append(s)
        owner.extend([i] * len(s))
    cat = "".join(cat)

    def span(needle: str, shortest: int = MIN_CHARS) -> set[int]:
        if len(needle) < shortest:
            return set()
        j = cat.find(needle)
        return set(owner[j: j + len(needle)]) if j >= 0 else set()

    hit = span(squash(quote))
    if hit:
        return hit, "quote"
    toks = [t for t in (squash(x) for x in str(quote).split()) if t]
    part: set[int] = set()
    for k in range(max(1, len(toks) - WINDOW + 1)):
        part |= span("".join(toks[k: k + WINDOW]))
    if part:
        return part, "part of the quote"
    for t in terms:
        hit = span(squash(t), shortest=2)
        if hit:
            return hit, "value"
    return set(), ""


def _rects(words: list, idx: set[int]) -> list[list[float]]:
    lines: dict[tuple, list[float]] = {}
    for i in sorted(idx):
        x0, y0, x1, y1, _, block, line = words[i][:7]
        r = lines.setdefault((block, line), [x0, y0, x1, y1])
        r[:] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
    return [[round(v, 1) for v in r] for r in lines.values()]


def find_quote(pdf: Path, page: int, quote: str, terms=()) -> dict:
    """``page`` starts at 1. -> {"page", "width", "height", "rects", "how"}; ``how`` is "" and ``rects`` empty when
    nothing was found."""
    with _FITZ_LOCK, fitz.open(pdf) as doc:
        p = doc[page - 1]
        words = p.get_text("words", flags=fitz.TEXT_DEHYPHENATE)
        idx, how = _find(words, quote or "", terms)
        return {"page": page, "width": round(p.rect.width, 1), "height": round(p.rect.height, 1),
                "rects": _rects(words, idx), "how": how}


def page_png(pdf: Path, page: int, dpi: int = 110) -> bytes:
    with _FITZ_LOCK, fitz.open(pdf) as doc:
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")


def page_count(pdf: Path) -> int:
    with _FITZ_LOCK, fitz.open(pdf) as doc:
        return len(doc)
