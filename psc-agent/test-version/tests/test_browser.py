"""The Chrome window for agents: against a local server with the real Chrome (skipped when it is not there), and the
tools in the SI finder with a stand-in browser."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from _fake import last_user_text, runtime, tool_call
from _fixtures import write_pdf

from psc_agent import load_paper
from psc_agent.core.browser import Browser, BrowserError, _blocked, browser_tools, find_chrome
from psc_agent.sources.si import SiFinder

try:
    import playwright.sync_api  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


class Pages(BaseHTTPRequestHandler):
    pdf = b""

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        if self.path == "/article":
            self.send(200, '<html><title>The article</title><body><p>Main text of the article.</p>'
                           '<a href="/about">About us</a><a href="/files/si.pdf">Supporting Information</a></body></html>')
        elif self.path == "/files/si.pdf":
            self.send(200, self.pdf, "application/pdf")
        elif self.path == "/challenge":
            self.send(200, "<html><title>Just a moment...</title><body>Verify you are human to continue.</body></html>")
        elif self.path == "/fake.pdf":
            self.send(200, "<html>please log in</html>")
        else:
            self.send(404, "no")


class Wall(unittest.TestCase):
    def test_a_bot_check_or_sign_in_page_is_recognised(self):
        self.assertEqual(_blocked("Just a moment...", "Verify you are human"), "challenge")
        self.assertEqual(_blocked("Sign in", "Use your institution"), "login")
        self.assertIsNone(_blocked("Title", "A long article. " * 400))  # an article that mentions a captcha is not a wall
        self.assertIsNone(_blocked("Probing the impact of ionic liquid additives", "Sign in to comment"))


@unittest.skipUnless(HAVE_PLAYWRIGHT and find_chrome(), "needs Playwright and Chrome")
class RealChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        Pages.pdf = write_pdf(cls.tmp / "served.pdf", "Supporting Information. Table S1.", pages=2).read_bytes()
        cls.server = HTTPServer(("127.0.0.1", 0), Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.browser = Browser(cls.tmp / "profile", headless=True, timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.server.shutdown()

    def test_read_download_and_walls(self):
        page = self.browser.read(self.base + "/article")
        self.assertEqual((page["title"], "Main text" in page["text"]), ("The article", True))
        self.assertEqual(page["links"][0]["text"], "Supporting Information")
        info = self.browser.download_pdf(self.base + "/files/si.pdf", self.tmp / "out" / "x_SI.pdf")
        self.assertEqual((info["pages"], info["saved"]), (2, "x_SI.pdf"))
        self.assertIn("Supporting Information", info["first_page_text"])
        self.assertEqual(self.browser.read(self.base + "/challenge")["blocked"], "challenge")  # reported, not solved
        with self.assertRaisesRegex(BrowserError, "not a PDF"):
            self.browser.download_pdf(self.base + "/fake.pdf", self.tmp / "out" / "no.pdf")
        self.assertFalse((self.tmp / "out" / "no.pdf").exists())


class StandIn:
    """A browser that serves canned answers: what the SI finder sees through browser_read / browser_download."""

    def __init__(self, tmp):
        self.tmp = tmp

    def read(self, url):
        return {"url": url, "blocked": "challenge", "note": "a challenge page"} if "blocked" in url else {"url": url, "title": "ok"}

    def download_pdf(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(dest, "Supporting Information. Table S1.")
        return {"saved": dest.name, "pages": 2, "bytes": 1, "first_page_text": "Supporting Information"}


class FinderWithBrowser(unittest.TestCase):
    def test_the_agent_gets_the_browser_tools_and_a_download_counts(self):
        tmp = Path(tempfile.mkdtemp())
        paper = load_paper(write_pdf(tmp / "m.pdf", "doi: 10.1002/admi.202500231 main text"))
        steps = iter([{"tool_calls": [tool_call("browser_read", {"url": "https://x.example/blocked"})]},
                      {"tool_calls": [tool_call("browser_download", {"url": "https://x.example/si.pdf"})]},
                      json.dumps({"status": "found", "notes": "saved through the browser", "links": []})])
        seen = {}

        def responder(body):
            seen.setdefault("tools", body.get("tools"))
            return next(steps)

        rt, fake = runtime({"si_finder": responder})
        res = SiFinder(rt, browser=StandIn(tmp)).find(paper, tmp / "SI", key="101002admi202500231")
        names = {t["function"]["name"] for t in seen["tools"]}
        self.assertTrue({"web_search", "read_page", "download_pdf", "browser_read", "browser_download"} <= names)
        self.assertEqual((res.status, [f.name for f in res.files]), ("found", ["101002admi202500231_SI.pdf"]))
        self.assertIn("blocked", json.dumps(fake.requests[1]))  # the wall came back to the model as a result

    def test_without_a_browser_there_are_no_browser_tools(self):
        tmp = Path(tempfile.mkdtemp())
        paper = load_paper(write_pdf(tmp / "m.pdf", "doi: 10.1002/admi.202500231"))
        seen = {}

        def responder(body):
            seen["tools"] = body.get("tools")
            return json.dumps({"status": "not_found", "notes": "", "links": []})

        rt, _ = runtime({"si_finder": responder})
        SiFinder(rt).find(paper, tmp / "SI", key="k")
        self.assertFalse({t["function"]["name"] for t in seen["tools"]} & {"browser_read", "browser_download"})


if __name__ == "__main__":
    unittest.main()
