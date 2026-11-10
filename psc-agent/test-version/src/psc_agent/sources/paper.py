"""Source papers: text layer per page plus JPEG renders of pages that carry figures or tables."""
from __future__ import annotations

import base64
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf

from .workbook import Source

# PyMuPDF is not thread-safe, even across documents; every call into it goes through this lock.
_FITZ_LOCK = threading.Lock()
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_MIN_DRAWINGS = 30  # vector plots and ruled tables; below this a page is plain text


MAX_SI_PAGES = 60  # an SI can run to hundreds of pages; past this the prompt grows faster than the yield


@dataclass
class Paper:
    name: str
    data: bytes  # the PDF; the supporting information, when given, is appended after the main pages
    pages: list[str]  # text layer, one entry per page (main pages, then SI pages)
    metadata: dict[str, str]
    n_main: int | None = None  # pages before the SI; None = no SI

    @property
    def si_pages(self) -> int:
        return 0 if self.n_main is None else len(self.pages) - self.n_main

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
                if self.n_main is not None and page.number >= self.n_main:
                    break  # SI pages go in as text only: their plots would multiply the prompt
                if all_pages or page.get_images() or len(page.get_drawings()) >= _MIN_DRAWINGS:
                    out.append((page.number, page.get_pixmap(dpi=dpi).tobytes("jpg", jpg_quality=quality)))
        return out


def load_paper(src: Source, name: str | None = None, si: Sequence[Source] = ()) -> Paper:
    """Read a PDF from a path or raw bytes. ``si``: supporting-information PDFs, appended after the main pages
    (at most ``MAX_SI_PAGES`` of them), so page numbers, evidence quotes and the text layer all cover both."""
    if isinstance(src, bytes):
        data, name = src, name or "paper.pdf"
    else:
        data, name = Path(src).read_bytes(), name or os.path.basename(src)
    with _FITZ_LOCK, fitz.open(stream=data, filetype="pdf") as doc:
        meta = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
        n_main = len(doc)
        if si:
            for extra in si:
                if len(doc) - n_main >= MAX_SI_PAGES:
                    break
                with fitz.open(stream=extra if isinstance(extra, bytes) else Path(extra).read_bytes(), filetype="pdf") as sd:
                    sd.select(list(range(min(len(sd), MAX_SI_PAGES - (len(doc) - n_main)))))
                    doc.insert_pdf(sd)
            if len(doc) > n_main:
                data = doc.tobytes()
            else:
                n_main = None
        else:
            n_main = None
        pages = [p.get_text() for p in doc]
    return Paper(name, data, pages, meta, n_main)


def paper_parts(paper: Paper, *, dpi: int, all_pages: bool, images: bool = True, pdf: bool = False) -> list[dict]:
    """The paper as model message parts: page images (unless ``images`` is off), then the text layer marked as a
    cache breakpoint (it is resent with every call about the same paper).

    ``pdf``: send the whole PDF as one file part instead of page images, for models that read PDFs natively (Gemini:
    no page cutting, the model sees every page, the supporting information appended). The text layer still follows,
    because its ``=== Page N ===`` labels are what evidence quotes are checked against."""
    parts: list[dict] = []
    if pdf:
        name = paper.name if paper.name.lower().endswith(".pdf") else paper.name + ".pdf"
        parts.append({"type": "file", "file": {"filename": name,
                                               "file_data": "data:application/pdf;base64," + base64.b64encode(paper.data).decode()}})
        images = False
    for i, jpg in paper.page_images(dpi=dpi, all_pages=all_pages) if images else []:
        parts.append({"type": "text", "text": f"[Image of page {i + 1}]"})
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    main = len(paper.pages) if paper.n_main is None else paper.n_main
    text = "\n\n".join(f"=== Page {i}{' (supporting information)' if i > main else ''} ===\n{t.strip()}"
                      for i, t in enumerate(paper.pages, 1))
    parts.append({"type": "text", "text": f"TEXT LAYER OF THE PAPER ({paper.name})\n\n{text}",
                  "cache_control": {"type": "ephemeral"}})
    return parts
