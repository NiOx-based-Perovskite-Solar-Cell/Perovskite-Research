"""The agent server: the SI agent with the person's own Chrome, running outside Docker.

Docker has no Chrome and no sign-in, and publishers answer plain scripts with 403 while serving the person's signed-in
browser. So the agent that needs the browser runs where the browser is. The web app (in Docker or not) sends it a
paper and gets back the SI it downloaded:

    python -m psc_agent agent-server --pdf "original paper"        # http://127.0.0.1:5100
    web app environment:  AGENT_SERVER_URL=http://host.docker.internal:5100   AGENT_SERVER_TOKEN=<the same token>

    GET  /health        -> {"ok": true, "chrome": bool}
    POST /si/download   multipart: pdf (the paper), key (the SI file-name stem), model (optional)
                        -> {"key", "status", "files": [names], "links", "notes", "cost_usd"}

The files are saved in ``<--pdf folder>/SI/<key>_SI.pdf``: the folder Docker mounts, so the web app finds them by key.
One paper at a time (there is one Chrome profile; a second request gets 409). Every request needs the shared token:
the server drives a signed-in browser, so a web page open in your own browser must not be able to start it.
A login page or a bot check is left to the person (see core.browser): the Chrome window stays open for them.
"""
from __future__ import annotations

import hmac
import re
import tempfile
import threading
from pathlib import Path

import httpx
from flask import Flask, jsonify, request

from ..core.browser import DEFAULT_PROFILE, Browser, find_chrome
from ..core.hooks import BudgetHook
from ..core.runtime import Runtime
from ..sources.pairing import SI_DIR
from ..sources.paper import load_paper
from ..sources.si import SiFinder

DEFAULT_PORT = 5100
_KEY = re.compile(r"^[a-z0-9]{1,60}$")  # becomes the start of a file name: letters and digits only
_MODEL = re.compile(r"^[\w.\-/:]{1,200}$")


class AgentServerError(Exception):
    """The agent server could not be used; the message says what to do."""


def create_agent_app(pdf_dir, token: str, *, model: str = "anthropic/claude-sonnet-5.5", budget: float = 1.0, profile=None,
                     cdp_url: str | None = None, wait_human: float = 300.0, browser_factory=None, runtime_factory=None) -> Flask:
    """``browser_factory() -> Browser`` and ``runtime_factory(model) -> Runtime`` are for tests; they default to the
    person's Chrome (``profile`` or ``cdp_url``) and the OpenRouter runtime."""
    if not token:
        raise ValueError("the agent server needs a token (AGENT_SERVER_TOKEN)")
    si_dir = Path(pdf_dir) / SI_DIR
    busy = threading.Lock()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
    make_browser = browser_factory or (lambda: Browser(profile or DEFAULT_PROFILE, cdp_url=cdp_url, wait_human=wait_human))
    make_runtime = runtime_factory or (lambda m: Runtime({"default": m}, hooks=[BudgetHook(budget)]))

    @app.before_request
    def authorize():
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
            return jsonify(error="the token is missing or wrong"), 401

    @app.get("/health")
    def health():
        return jsonify(ok=True, chrome=bool(find_chrome()))

    @app.post("/si/download")
    def download():
        key, upload, model_id = request.form.get("key", ""), request.files.get("pdf"), request.form.get("model") or model
        if not _KEY.match(key) or upload is None or not _MODEL.match(model_id):
            return jsonify(error="send a pdf, a key of letters and digits (at most 60), and optionally a model id"), 400
        if not busy.acquire(blocking=False):
            return jsonify(error="the agent is busy with another paper: try again when its Chrome window has finished"), 409
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "paper.pdf"
                upload.save(path)
                try:
                    paper = load_paper(path)
                except Exception:  # not a readable PDF
                    return jsonify(error="the file is not a readable PDF"), 400
                browser = make_browser()
                try:
                    res = SiFinder(make_runtime(model_id), browser=browser, download=True).find(paper, si_dir, key=key)
                finally:
                    browser.close()
            return jsonify(key=res.key, status=res.status, files=[f.name for f in res.files], links=res.links, notes=res.notes,
                           cost_usd=round(res.usage.cost_usd, 4))
        except Exception as e:  # e.g. no OpenRouter key, Chrome cannot start
            return jsonify(error=f"{type(e).__name__}: {e}"), 500
        finally:
            busy.release()

    return app


class AgentServer:
    """The web app's side: where the agent server is and how to ask it."""

    def __init__(self, url: str, token: str, *, transport=None):
        self.url, self.token, self._transport = url.rstrip("/"), token, transport

    def _request(self, method: str, path: str, *, timeout: float, **kwargs) -> dict:
        try:
            with httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {self.token}"}, timeout=timeout,
                              transport=self._transport) as client:
                r = client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise AgentServerError(f"cannot reach the agent server at {self.url} ({type(e).__name__}): start it with "
                                   "`python -m psc_agent agent-server --pdf <folder>`") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200:
            raise AgentServerError(data.get("error") or f"the agent server answered HTTP {r.status_code}")
        return data

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=3.0)

    def download_si(self, pdf: Path, key: str, model: str | None = None, *, timeout: float = 1800.0) -> dict:
        """Ask the agent to find and download the SI of this paper with the person's Chrome. Waits for it (a bot check
        can keep the window open for minutes)."""
        with open(pdf, "rb") as fh:
            return self._request("POST", "/si/download", timeout=timeout, data={"key": key, **({"model": model} if model else {})},
                                 files={"pdf": ("paper.pdf", fh, "application/pdf")})
