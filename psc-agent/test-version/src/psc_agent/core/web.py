"""Web access for agents: search, read a page, download a PDF. Standard library only, no API key.

``Web`` does the requests; ``web_tools`` wraps them as ``Tool`` objects for the agent loop (core.agent), so any
model client that can run tools (OpenRouter, or the emulation in core.subscription) can browse.

Search goes to DuckDuckGo's HTML page. Publishers often refuse scripts (HTTP 403 behind a bot check): that comes
back to the model as an error it can report, never as a crash. Requests to private or local addresses are
refused, also after a redirect, so a page cannot send the agent to the user's own network.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import fitz  # pymupdf

from .agent import Tool, ToolError

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
SEARCH_URL = "https://html.duckduckgo.com/html/"
_RESULT = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]*class="result__a"|\Z)', re.S)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")
_SUPP = re.compile(r"suppl|supporting|\bsi\b|esi|mmc\d|media-?\d|\.pdf|download|attachment", re.I)


class WebError(ToolError):
    """A request that failed or was refused; the message goes back to the model."""


class _Guard(urllib.request.HTTPRedirectHandler):
    def __init__(self, web: Web):
        self.web = web

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.web.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    """Visible text, title and links of an HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.chunks, self.links = "", [], []
        self._skip, self._in_title, self._href, self._anchor = 0, False, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href, self._anchor = dict(attrs).get("href"), []
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href:
            self.links.append((" ".join("".join(self._anchor).split()), self._href))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.chunks.append(data)
            if self._href is not None:
                self._anchor.append(data)


class Web:
    def __init__(self, *, search_url: str = SEARCH_URL, timeout: float = 25.0, max_bytes: int = 60_000_000,
                 allow_private: bool = False):
        self.search_url, self.timeout, self.max_bytes, self.allow_private = search_url, timeout, max_bytes, allow_private
        self._opener = urllib.request.build_opener(_Guard(self))

    def check(self, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise WebError(f"only http(s) URLs are allowed: {url[:100]}")
        if self.allow_private:
            return
        try:
            infos = socket.getaddrinfo(parts.hostname, None)
        except OSError as e:
            raise WebError(f"cannot resolve {parts.hostname}: {e}") from e
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise WebError(f"refused: {parts.hostname} is a private address")

    def _get(self, url: str, data: bytes | None = None) -> tuple[bytes, str, str]:
        """-> (body, content type, final URL)."""
        self.check(url)
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                                                              "Accept-Language": "en"})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                body = r.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise WebError(f"larger than {self.max_bytes // 1_000_000} MB")
                return body, r.headers.get("content-type", ""), r.geturl()
        except urllib.error.HTTPError as e:
            hint = " (the site blocks scripts)" if e.code in (401, 403, 429) else ""
            raise WebError(f"HTTP {e.code}{hint}: {url[:150]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WebError(f"request failed: {str(e)[:150]}") from e

    def search(self, query: str, n: int = 8) -> list[dict]:
        body, _, _ = self._get(self.search_url, urllib.parse.urlencode({"q": query}).encode())
        page = body.decode("utf-8", "replace")
        out = []
        for href, title, rest in _RESULT.findall(page):
            href = html.unescape(href)
            if "uddg=" in href:  # DuckDuckGo wraps the target
                href = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg", [href])[0]
            snippet = _SNIPPET.search(rest)
            out.append({"title": html.unescape(_TAG.sub("", title)).strip(), "url": urllib.parse.urljoin(self.search_url, href),
                        "snippet": html.unescape(_TAG.sub("", snippet.group(1))).strip() if snippet else ""})
            if len(out) >= n:
                break
        return out

    def page(self, url: str, max_chars: int = 6000) -> dict:
        body, ctype, final = self._get(url)
        if body[:5] == b"%PDF-" or "pdf" in ctype.lower():
            return {"url": final, "type": "pdf", "note": "this is a PDF: use download_pdf to save it"}
        parser = _Text()
        parser.feed(body.decode("utf-8", "replace"))
        text = re.sub(r"\n\s*\n+", "\n", "".join(parser.chunks)).strip()
        links, seen = [], set()
        for label, href in parser.links:
            full = urllib.parse.urljoin(final, html.unescape(href))
            if full in seen or not full.startswith("http"):
                continue
            seen.add(full)
            links.append((bool(_SUPP.search(label + " " + full)), label[:80], full))
        links.sort(key=lambda t: not t[0])  # links that look like supporting files first
        return {"url": final, "title": " ".join(parser.title.split())[:200], "text": text[:max_chars],
                "links": [{"text": t, "url": u} for _, t, u in links[:40]]}

    def download_pdf(self, url: str, dest: Path) -> dict:
        body, _, final = self._get(url)
        if body[:5] != b"%PDF-":
            raise WebError("not a PDF (a login or bot-check page?): " + body[:120].decode("utf-8", "replace").strip()[:100])
        try:
            with fitz.open(stream=body, filetype="pdf") as doc:
                pages, first = len(doc), doc[0].get_text()[:400] if len(doc) else ""
        except Exception as e:  # a truncated or damaged file
            raise WebError(f"the PDF cannot be read: {e}") from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return {"saved": dest.name, "url": final, "pages": pages, "bytes": len(body), "first_page_text": " ".join(first.split())}


def web_tools(web: Web, download_dir: Path, prefix: str, saved: list[Path]) -> list[Tool]:
    """Search, read and download tools. Downloads go to ``download_dir/<prefix>_SI.pdf`` (then ``_SI2`` ...) and
    are appended to ``saved``."""

    def search(query: str):
        return web.search(query)

    def read_page(url: str):
        return web.page(url)

    def download_pdf(url: str):
        n = len(saved) + 1
        dest = download_dir / f"{prefix}_SI{'' if n == 1 else n}.pdf"
        info = web.download_pdf(url, dest)
        saved.append(dest)
        return info

    obj = lambda **props: {"type": "object", "properties": props, "required": list(props)}  # noqa: E731
    return [
        Tool("web_search", "Search the web. Returns up to 8 results: title, url, snippet.",
             obj(query={"type": "string"}), search),
        Tool("read_page", "Fetch a web page: title, text (first 6000 characters) and its links, supporting-file links first.",
             obj(url={"type": "string"}), read_page),
        Tool("download_pdf", "Download a PDF and save it as this paper's supporting information. Returns the page count and "
                             "the first page's text so you can check it is the right file.",
             obj(url={"type": "string"}), download_pdf),
    ]
