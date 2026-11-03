"""Source papers: text layer per page plus JPEG renders of pages that carry figures or tables."""
from __future__ import annotations

import base64
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf

from .workbook import Source

# PyMuPDF is not thread-safe, even across documents; every call into it goes through this lock.
_FITZ_LOCK = threading.Lock()
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_MIN_DRAWINGS = 30  # vector plots and ruled tables; below this a page is plain text


@dataclass
class Paper:
    name: str
    data: bytes
    pages: list[str]  # text layer, one entry per page
    metadata: dict[str, str]

    def head_text(self, n_pages: int = 2) -> str:
        return " ".join([*self.metadata.values(), *self.pages[:n_pages]])

    def dois(self) -> list[str]:
        """DOIs in metadata and the first pages, in order (best effort; line-wrapped DOIs are missed)."""
        return list(dict.fromkeys(m.rstrip(".,;)]") for m in _DOI.findall(self.head_text())))

    @property
    def doi(self) -> str | None:
        return next(iter(self.dois()), None)

    def page_images(self, *, dpi: int = 150, quality: int = 75, all_pages: bool = False) -> list[tuple[int, bytes]]:
        """[(0-based page index, JPEG bytes)] for pages with raster images or vector graphics."""
        out = []
        with _FITZ_LOCK, fitz.open(stream=self.data, filetype="pdf") as doc:
            for page in doc:
                if all_pages or page.get_images() or len(page.get_drawings()) >= _MIN_DRAWINGS:
                    out.append((page.number, page.get_pixmap(dpi=dpi).tobytes("jpg", jpg_quality=quality)))
        return out


def load_paper(src: Source, name: str | None = None) -> Paper:
    """Read a PDF from a path or raw bytes."""
    if isinstance(src, bytes):
        data, name = src, name or "paper.pdf"
    else:
        data, name = Path(src).read_bytes(), name or os.path.basename(src)
    with _FITZ_LOCK, fitz.open(stream=data, filetype="pdf") as doc:
        pages = [p.get_text() for p in doc]
        meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
    return Paper(name, data, pages, meta)


def paper_parts(paper: Paper, *, dpi: int, all_pages: bool) -> list[dict]:
    """The paper as model message parts: page images, then the text layer marked as a cache breakpoint
    (it is resent with every call about the same paper)."""
    parts: list[dict] = []
    for i, jpg in paper.page_images(dpi=dpi, all_pages=all_pages):
        parts.append({"type": "text", "text": f"[Image of page {i + 1}]"})
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    text = "\n\n".join(f"=== Page {i} ===\n{t.strip()}" for i, t in enumerate(paper.pages, 1))
    parts.append({"type": "text", "text": f"TEXT LAYER OF THE PAPER ({paper.name})\n\n{text}",
                  "cache_control": {"type": "ephemeral"}})
    return parts
