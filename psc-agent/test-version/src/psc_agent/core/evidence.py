"""Evidence rows: the shape every extraction answer shares (cells + page + quote), and the quote check.

Recheck rows (RowAnswer) and extract rows (XRow) both extend EvidenceRow, and QuoteCheckHook sets
``quote_ok`` on any of them by looking the quote up on the page it cites.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from pydantic import BaseModel, field_validator


def first_int(v):
    """A page number from whatever the model sent ("3", "p. 3", "3-4" -> 3)."""
    if v is None or isinstance(v, int):
        return v
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


class CellAnswer(BaseModel):
    column: str
    value: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _as_text(cls, v):
        return None if v is None else str(v)


class EvidenceRow(BaseModel):
    values: list[CellAnswer] = []
    page: int | None = None
    source: str | None = None  # text | table | figure | none
    evidence: str | None = None  # a verbatim quote or a figure/table label
    note: str | None = None
    quote_ok: bool | None = None  # set by QuoteCheckHook, never by the model

    @field_validator("page", mode="before")
    @classmethod
    def _page_int(cls, v):
        return first_int(v)

    def cells(self) -> dict[str, str]:
        """Filled cells only, as {column: value}."""
        return {c.column: c.value for c in self.values if c.value not in (None, "")}


_LABEL = re.compile(r"^\s*(?:fig(?:ure)?s?\.?|table|scheme|eq(?:uation)?\.?|supplementary|SI)\b", re.I)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "−": "-", " ": " "})


def normalize_text(text: str) -> str:
    """Lowercase, NFKC, straight quotes/dashes, joined line-break hyphenation, single spaces."""
    t = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    t = re.sub(r"-\s*\n\s*", "", t)  # words hyphenated across lines
    return re.sub(r"\s+", " ", t).strip()


def squash(text: str) -> str:
    """Only the letters and digits, lowercase: a quote and its page then agree although a model spaced a superscript
    differently ("2.70 x 10 18 cm 3" vs "2.7010 18cm3") or the PDF dropped the multiplication sign."""
    return re.sub(r"[\W_]+", "", normalize_text(text))


def quote_on_page(pages: list[str], page: int | None, quote: str | None, source: str | None = None,
                  min_ratio: float = 0.8) -> bool | None:
    """True if ``quote`` is on the cited page (or the page next to it), False if not, None when the
    evidence is not a checkable quote (no quote, a figure/table label, or a value read off a figure)."""
    if not quote or source == "figure" or _LABEL.match(quote) or len(normalize_text(quote)) < 12:
        return None
    if not page or not 1 <= page <= len(pages):
        return False
    q = normalize_text(quote).strip("\"' .")
    squashed = squash(quote)
    for i in (page, page - 1, page + 1):
        if 1 <= i <= len(pages):
            text = normalize_text(pages[i - 1])
            if q in text or (len(squashed) >= 10 and squashed in squash(text)):
                return True
            m = SequenceMatcher(None, text, q, autojunk=False).find_longest_match(0, len(text), 0, len(q))
            if m.size >= min_ratio * len(q):
                return True
    return False
