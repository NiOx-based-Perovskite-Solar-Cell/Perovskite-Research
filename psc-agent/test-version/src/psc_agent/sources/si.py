"""Supporting information (SI): where it lives on disk, and an agent that finds it on the web.

On disk the SI of a paper is ``<pdf folder>/SI/<doi key>*.pdf`` (the doi key is the DOI reduced to lowercase letters
and digits, e.g. ``101039d3lf00258f_SI.pdf``). Put files there by hand, or let ``SiFinder`` download them. Pairing
picks them up, and every workflow then reads the SI after the main text, as extra pages of the same paper.
"""
from __future__ import annotations

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
    status: str  # found | blocked | not_found | error
    files: list[Path] = field(default_factory=list)
    notes: str = ""
    doi: str | None = None
    links: list[str] = field(default_factory=list)  # where to fetch the SI by hand
    usage: Usage = field(default_factory=Usage)


class SiFinder:
    """An agent with web tools (role ``si_finder``) that searches for a paper's SI and saves the PDF."""

    def __init__(self, rt: Runtime, *, web: Web | None = None, browser: Browser | None = None, max_steps: int = 12):
        """``browser``: the person's signed-in Chrome (core.browser). With it the agent can open the publishers' pages."""
        self.rt, self.web, self.browser, self.max_steps = rt, web or Web(), browser, max_steps

    def find(self, paper: Paper, dest_dir, key: str | None = None) -> SiResult:
        doi = paper.doi
        key = key or doi_key(doi or paper.name)
        res = SiResult(key, "error", doi=doi, links=supplement_links(doi))
        head = re.sub(r"\s+", " ", " ".join(paper.pages[:1]))[:900]
        task = (f"Paper DOI: {doi or 'unknown'}\nStart of its first page: {head}\n\n"
                "Find its supporting information and download it.")
        saved: list[Path] = []
        tools = web_tools(self.web, Path(dest_dir), key, saved)
        if self.browser:
            tools += browser_tools(self.browser, Path(dest_dir), key, saved)
        messages = [{"role": "system", "content": self.rt.system("si_finder")}, {"role": "user", "content": task}]
        try:
            loop = run_tools(self.rt, "si_finder", messages, tools, max_steps=self.max_steps, usage=res.usage)
            ans = _Answer.model_validate(parse_json(loop.final))
            seen = " ".join(c.result for c in loop.calls)  # only addresses that a page or search result actually showed
            res.links = list(dict.fromkeys([u for u in ans.links if u in seen] + res.links))
        except (OpenRouterError, ValueError) as e:
            res.notes = f"{type(e).__name__}: {e}"
            res.files = saved
            res.status = "found" if saved else "error"
            return res
        res.files = saved
        res.status = "found" if saved else ("blocked" if ans.status == "blocked" else "not_found")
        res.notes = ans.notes
        log.info("%s: SI %s (%d file(s)) %s", key, res.status, len(saved), ans.notes[:120])
        return res
