"""A real Chrome window for agents: read a page, download a PDF, with the person's own sign-in.

Why a browser: publishers (ACS, Wiley, RSC, Elsevier) refuse plain HTTP clients with a bot check, but serve the same
files to a browser that is signed in through the reader's institution. ``Browser`` drives the installed Chrome through
Playwright, with a profile folder of its own (``~/.psc-agent/chrome``) that keeps the sign-in between runs.

The person stays in charge:
- ``psc-agent browser login`` opens the window so you sign in once, by hand.
- A login page or a bot check is never solved by the program. The tool reports it, and with ``wait_human`` the window
  stays open (and the terminal says so) until the person has solved it and the page has changed, or the time is up.
- No stealth tricks: it is an ordinary Chrome with a visible window.

Playwright's sync API belongs to the thread that started it, so one ``Browser`` serves one thread (run papers one after
another). Playwright is optional: ``pip install playwright`` (it drives the installed Chrome, no browser download).
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from pathlib import Path

from .agent import Tool, ToolError

DEFAULT_PROFILE = Path.home() / ".psc-agent" / "chrome"
# words of the pages that stop a script: a bot check or a sign-in wall
_CHALLENGE = re.compile(r"just a moment|verify you are (?:a )?human|checking your browser|are you a robot|attention required|"
                        r"access denied|captcha|performing security verification|enable javascript and cookies", re.I)
_LOGIN = re.compile(r"\bsign in\b|\blog in\b|institutional login|shibboleth|openathens|access through your (?:institution|organi[sz]ation)", re.I)
_SUPP = re.compile(r"suppl|supporting|\bsi\b|esi|mmc\d|media-?\d|\.pdf|download|attachment", re.I)


class BrowserError(ToolError):
    """The browser could not do it; the message goes back to the model."""


def find_chrome() -> str | None:
    for p in (os.environ.get("CHROME_BIN"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    return None


def _blocked(title: str, text: str) -> str | None:
    """'challenge' or 'login' when the page looks like a wall rather than an article."""
    head = f"{title}\n{text[:1500]}"
    if _CHALLENGE.search(head) and len(text) < 4000:
        return "challenge"
    if _LOGIN.search(title) and len(text) < 2500:
        return "login"
    return None


class Browser:
    """``cdp_url``: attach to a Chrome you started yourself with --remote-debugging-port instead of launching one.
    ``wait_human``: seconds to leave a bot check or sign-in page open for the person (0: report it at once).
    ``say``: how the program talks to the person while it waits (default: print)."""

    def __init__(self, profile_dir=DEFAULT_PROFILE, *, headless: bool = False, cdp_url: str | None = None,
                 wait_human: float = 0.0, timeout: float = 45.0, say=print):
        self.profile_dir, self.headless, self.cdp_url = Path(profile_dir), headless, cdp_url
        self.wait_human, self.timeout, self.say = wait_human, timeout, say
        self._pw = self._ctx = self._page = None

    # ------------------------------------------------------------ lifecycle
    def _start(self):
        if self._ctx is not None:
            return self._ctx
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserError("Playwright is not installed: pip install playwright") from None
        self._pw = sync_playwright().start()
        try:
            if self.cdp_url:
                browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
                self._ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            else:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                kwargs = dict(headless=self.headless, accept_downloads=True, viewport=None, args=["--start-maximized"])
                exe = find_chrome()
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), **kwargs, **({"executable_path": exe} if exe else {"channel": "chrome"}))
        except Exception as e:
            self.close()
            raise BrowserError(f"cannot start Chrome: {str(e).splitlines()[0][:200]}") from e
        self._ctx.set_default_timeout(self.timeout * 1000)
        return self._ctx

    def close(self) -> None:
        for obj in (self._ctx, self._pw):
            try:
                obj and (obj.close() if obj is self._ctx else obj.stop())
            except Exception:
                pass
        self._pw = self._ctx = self._page = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _tab(self):
        ctx = self._start()
        if self._page is None or self._page.is_closed():
            self._page = ctx.new_page()
        return self._page

    # ------------------------------------------------------------ actions
    def _wall(self, page) -> str | None:
        return _blocked(page.title(), page.inner_text("body")[:6000] if page.query_selector("body") else "")

    def read(self, url: str, max_chars: int = 6000) -> dict:
        """Open ``url`` in the window; -> title, text, links (supporting-file links first). A page that is a bot check
        or a sign-in wall is waited on for the person (``wait_human``), else reported as ``blocked``."""
        page = self._tab()
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            if "Download is starting" in str(e):
                return {"url": url, "type": "download", "note": "this address starts a file download: use browser_download"}
            raise BrowserError(f"cannot open {url[:120]}: {str(e).splitlines()[0][:150]}") from e
        if resp is not None and "pdf" in (resp.headers.get("content-type", "")).lower():
            return {"url": page.url, "type": "pdf", "note": "this is a PDF: use browser_download to save it"}
        page.wait_for_timeout(1500)
        wall = self._wall(page)
        if wall and self.wait_human:
            self.say(f"\n[browser] {wall} page at {page.url[:100]}: solve it in the Chrome window "
                     f"(waiting up to {self.wait_human:.0f} s) ...")
            end = time.time() + self.wait_human
            while wall and time.time() < end:
                page.wait_for_timeout(2000)
                wall = self._wall(page)
        if wall:
            return {"url": page.url, "blocked": wall,
                    "note": f"a {wall} page, not the article. The program does not solve it: tell the person."}
        text = re.sub(r"\n\s*\n+", "\n", page.inner_text("body")).strip()
        links, seen = [], set()
        for a in page.query_selector_all("a[href]")[:600]:
            href = urllib.parse.urljoin(page.url, a.get_attribute("href") or "")
            label = " ".join((a.inner_text() or "").split())[:80]
            if href.startswith("http") and href not in seen:
                seen.add(href)
                links.append((bool(_SUPP.search(label + " " + href)), label, href))
        links.sort(key=lambda t: not t[0])
        return {"url": page.url, "title": page.title()[:200], "text": text[:max_chars],
                "links": [{"text": t, "url": u} for _, t, u in links[:40]]}

    def download_pdf(self, url: str, dest: Path) -> dict:
        """Save the PDF at ``url`` (a link or a page that serves one) to ``dest``, using the window's sign-in."""
        ctx = self._start()
        body = None
        try:  # the window's cookies, no page: works when the file is served directly
            r = ctx.request.get(url, timeout=self.timeout * 1000)
            if r.ok and r.body()[:5] == b"%PDF-":
                body = r.body()
        except Exception:
            body = None
        if body is None:  # a link that needs the page to run first: let the browser follow it and catch the download
            page = ctx.new_page()
            try:
                with page.expect_download(timeout=self.timeout * 1000) as dl:
                    try:
                        page.goto(url)
                    except Exception:
                        pass  # navigation to a download is reported as an error; the download event is what counts
                path = Path(dl.value.path())
                body = path.read_bytes()
            except Exception:
                body = None
            finally:
                page.close()
        if body is None or body[:5] != b"%PDF-":
            raise BrowserError("not a PDF (a login or bot-check page, or no access): open it with browser_read to see")
        try:
            import fitz

            with fitz.open(stream=body, filetype="pdf") as doc:
                pages, first = len(doc), (doc[0].get_text()[:400] if len(doc) else "")
        except Exception as e:
            raise BrowserError(f"the PDF cannot be read: {e}") from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return {"saved": dest.name, "pages": pages, "bytes": len(body), "first_page_text": " ".join(first.split())}

    def login(self, url: str = "about:blank") -> None:
        """Open the window for the person to sign in by hand, and wait for Enter in the terminal."""
        page = self._tab()
        if url != "about:blank":
            page.goto(url)
        self.say("Chrome is open. Sign in to the publishers (through your institution) in that window, then press Enter here.")
        input()
        self.close()


def browser_tools(browser: Browser, download_dir: Path, prefix: str, saved: list[Path]) -> list[Tool]:
    """Tools for the agent loop that use the person's Chrome. Downloads go to ``download_dir/<prefix>_SI.pdf`` (then
    ``_SI2`` ...) and are appended to ``saved``."""

    def browser_read(url: str):
        return browser.read(url)

    def browser_download(url: str):
        n = len(saved) + 1
        dest = download_dir / f"{prefix}_SI{'' if n == 1 else n}.pdf"
        info = browser.download_pdf(url, dest)
        saved.append(dest)
        return info

    obj = lambda **props: {"type": "object", "properties": props, "required": list(props)}  # noqa: E731
    return [
        Tool("browser_read", "Open a page in the person's signed-in Chrome: title, text (first 6000 characters) and its links, "
                             "supporting-file links first. Use it for publisher pages that refuse web_search / read_page. "
                             "A bot-check or sign-in page comes back as blocked: tell the person, do not try to get past it.",
             obj(url={"type": "string"}), browser_read),
        Tool("browser_download", "Download a PDF with the person's signed-in Chrome and save it as this paper's supporting "
                                "information. Returns the page count and the first page's text so you can check it is the right file.",
             obj(url={"type": "string"}), browser_download),
    ]
