"""The agent server (SI agent + the person's Chrome, outside Docker) and the web app's side of it."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _fake import runtime, tool_call
from _fixtures import write_pdf
from werkzeug.serving import make_server

from psc_agent.web import WebConfig, create_app
from psc_agent.web.agent_server import AgentServer, AgentServerError, create_agent_app

TOKEN = "s3cret-token"
PAPER = "doi: 10.1016/j.test.2024.1 Methods. The details are in the Supporting Information."
SI_URL = "https://publisher.example/si.pdf"


class FakeChrome:
    """Stands in for core.browser.Browser: 'downloads' a small PDF where it is told to."""

    def __init__(self, gate: threading.Event | None = None):
        self.gate, self.closed, self.downloads = gate, False, []

    def read(self, url):
        return {"url": url, "title": "article", "text": "Supporting Information", "links": []}

    def download_pdf(self, url, dest):
        if self.gate:
            self.gate.wait(10)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(dest, "Supporting Information. Table S1: PEAI at 4000 rpm.")
        self.downloads.append((url, dest.name))
        return {"saved": dest.name, "pages": 2, "bytes": dest.stat().st_size, "first_page_text": "Supporting Information"}

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdfs = self.tmp / "original paper"
        self.pdfs.mkdir()
        self.paper = write_pdf(self.tmp / "paper.pdf", PAPER)
        self.browsers: list[FakeChrome] = []
        self.models: list[str] = []
        self.gate = None
        self.fail_runtime = None
        self.app = create_agent_app(self.pdfs, TOKEN, browser_factory=self.make_browser, runtime_factory=self.make_runtime)
        self.c = self.app.test_client()

    def make_browser(self):
        b = FakeChrome(self.gate)
        self.browsers.append(b)
        return b

    def make_runtime(self, model):
        if self.fail_runtime:
            raise self.fail_runtime
        self.models.append(model)
        steps = iter([{"tool_calls": [tool_call("browser_download", {"url": SI_URL})]},
                      json.dumps({"status": "found", "notes": "saved the SI", "links": []})])
        return runtime({"si_finder": lambda body: next(steps)})[0]

    def post(self, key="101016jtest20241", pdf=True, token=TOKEN, **form):
        data = {"key": key, **form}
        if pdf:
            data["pdf"] = (io.BytesIO(self.paper.read_bytes()), "paper.pdf")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.c.post("/si/download", data=data, headers=headers, content_type="multipart/form-data")


class Server(Base):
    # ---- who may ask
    def test_every_request_needs_the_token(self):
        for token in (None, "wrong"):
            self.assertEqual(self.post(token=token).status_code, 401)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self.assertEqual(self.c.get("/health", headers=headers).status_code, 401)
        self.assertEqual(self.browsers, [])  # nothing started
        self.assertEqual(self.c.get("/health", headers={"Authorization": f"Bearer {TOKEN}"}).get_json()["ok"], True)

    def test_a_server_without_a_token_does_not_start(self):
        with self.assertRaises(ValueError):
            create_agent_app(self.pdfs, "")

    # ---- the download
    def test_the_agent_downloads_the_si_into_the_pdf_folders_si_dir(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual((body["status"], body["files"], body["key"]), ("found", ["101016jtest20241_SI.pdf"], "101016jtest20241"))
        self.assertTrue((self.pdfs / "SI" / "101016jtest20241_SI.pdf").is_file())
        self.assertEqual(self.browsers[0].downloads, [(SI_URL, "101016jtest20241_SI.pdf")])
        self.assertTrue(self.browsers[0].closed)  # the window is released after every paper
        self.assertEqual(self.models, ["anthropic/claude-sonnet-5.5"])

    def test_the_model_can_be_chosen_per_request(self):
        self.assertEqual(self.post(model="big/sonnet").status_code, 200)
        self.assertEqual(self.models, ["big/sonnet"])
        self.assertEqual(self.post(model="bad model!").status_code, 400)

    def test_bad_requests_write_nothing(self):
        for key in ("", "../x", "A1", "a" * 61, "a b"):
            self.assertEqual(self.post(key=key).status_code, 400, key)
        self.assertEqual(self.post(pdf=False).status_code, 400)
        r = self.c.post("/si/download", data={"key": "k1", "pdf": (io.BytesIO(b"not a pdf"), "x.pdf")},
                        headers={"Authorization": f"Bearer {TOKEN}"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertFalse((self.pdfs / "SI").exists())
        self.assertEqual(self.browsers, [])

    def test_a_failure_is_reported_and_the_server_stays_usable(self):
        self.fail_runtime = ValueError("OpenRouter API key missing")
        r = self.post()
        self.assertEqual(r.status_code, 500)
        self.assertIn("OpenRouter API key missing", r.get_json()["error"])
        self.assertTrue(self.browsers[0].closed)
        self.fail_runtime = None
        self.assertEqual(self.post().status_code, 200)

    def test_one_paper_at_a_time(self):
        self.gate = threading.Event()
        first = {}
        t = threading.Thread(target=lambda: first.setdefault("r", self.post()))
        t.start()
        for _ in range(100):  # wait until the first request holds the browser
            if self.browsers:
                break
            time.sleep(0.05)
        busy = self.post()
        self.assertEqual(busy.status_code, 409)
        self.gate.set()
        t.join(10)
        self.assertEqual(first["r"].status_code, 200)
        self.assertEqual(self.post().status_code, 200)  # free again


class Live(Base):
    """The same server over real HTTP, asked through AgentServer, and the web app's pages and job that use it."""

    def setUp(self):
        super().setUp()
        self.httpd = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.addCleanup(self.httpd.shutdown)

    def web(self, agent, **cfg):
        root = self.tmp  # the web app reads the same folder the server writes
        (root / "Raw Data").mkdir(exist_ok=True)
        app = create_app(WebConfig(root=root, require_key=False, max_workers=1, agent_server=agent, **cfg))
        app.testing = True
        return app, app.test_client()

    def wait(self, app, job_id):
        for _ in range(150):
            job = app.config["JOBS"].get(job_id)
            if job.status in ("done", "failed"):
                return job
            time.sleep(0.1)
        return job

    # ---- the client
    def test_client_health_and_download(self):
        srv = AgentServer(self.url, TOKEN)
        self.assertTrue(srv.health()["ok"])
        res = srv.download_si(self.paper, "101016jtest20241")
        self.assertEqual((res["status"], res["files"]), ("found", ["101016jtest20241_SI.pdf"]))

    def test_client_errors_say_what_to_do(self):
        with self.assertRaises(AgentServerError) as e:
            AgentServer(self.url, "wrong").health()
        self.assertIn("token", str(e.exception))
        with self.assertRaises(AgentServerError) as e:
            AgentServer("http://127.0.0.1:1", TOKEN).health()
        self.assertIn("agent-server", str(e.exception))
        self.gate = threading.Event()
        t = threading.Thread(target=lambda: AgentServer(self.url, TOKEN).download_si(self.paper, "k1"))
        t.start()
        for _ in range(100):
            if self.browsers:
                break
            time.sleep(0.05)
        with self.assertRaises(AgentServerError) as e:
            AgentServer(self.url, TOKEN).download_si(self.paper, "k2")
        self.assertIn("busy", str(e.exception))
        self.gate.set()
        t.join(10)

    # ---- the web app
    def test_status_endpoint(self):
        _, c = self.web(None)
        self.assertEqual(c.get("/api/agent-server").get_json(), {"configured": False})
        _, c = self.web(AgentServer(self.url, TOKEN))
        status = c.get("/api/agent-server").get_json()
        self.assertEqual((status["configured"], status["reachable"], status["url"]), (True, True, self.url))
        self.assertNotIn(TOKEN, json.dumps(status))
        _, c = self.web(AgentServer("http://127.0.0.1:1", TOKEN))
        down = c.get("/api/agent-server").get_json()
        self.assertEqual((down["configured"], down["reachable"]), (True, False))
        self.assertIn("agent-server", down["error"])
        self.assertNotIn(TOKEN, json.dumps(down))

    def test_the_job_downloads_through_the_server_and_the_plan_then_says_have(self):
        write_pdf(self.pdfs / "other.pdf", PAPER)
        app, c = self.web(AgentServer(self.url, TOKEN))
        self.assertEqual(c.get("/api/si/plan?pdf=pdf:other.pdf").get_json()["status"], "agent_needed")
        r = c.post("/api/jobs", json={"kind": "si_download", "params": {"pdf": "pdf:other.pdf", "si_finder_model": "big/sonnet"}})
        self.assertEqual(r.status_code, 202)
        job = self.wait(app, r.get_json()["id"])
        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual((job.summary["status"], job.summary["files"]), ("found", ["101016jtest20241_SI.pdf"]))
        self.assertEqual(self.models, ["big/sonnet"])
        self.assertTrue(any("agent server" in line for line in job.log))
        plan = c.get("/api/si/plan?pdf=pdf:other.pdf").get_json()
        self.assertEqual((plan["status"], [f["name"] for f in plan["files"]]), ("have", ["101016jtest20241_SI.pdf"]))

    def test_the_job_needs_no_model_key_in_the_web_app_and_a_configured_server(self):
        write_pdf(self.pdfs / "other.pdf", PAPER)
        app = create_app(WebConfig(root=self.tmp, require_key=True, max_workers=1, agent_server=AgentServer(self.url, TOKEN)))
        c = app.test_client()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            self.assertEqual(c.post("/api/jobs", json={"kind": "si", "params": {"pdf": "pdf:other.pdf"}}).status_code, 409)  # a model job
            ok = c.post("/api/jobs", json={"kind": "si_download", "params": {"pdf": "pdf:other.pdf"}})
        self.assertEqual(ok.status_code, 202)
        self.wait(app, ok.get_json()["id"])
        _, plain = self.web(None)
        refused = plain.post("/api/jobs", json={"kind": "si_download", "params": {"pdf": "pdf:other.pdf"}})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("agent server", refused.get_json()["error"])

    def test_a_server_that_is_down_fails_the_job_with_a_message(self):
        write_pdf(self.pdfs / "other.pdf", PAPER)
        app, c = self.web(AgentServer("http://127.0.0.1:1", TOKEN))
        job = self.wait(app, c.post("/api/jobs", json={"kind": "si_download", "params": {"pdf": "pdf:other.pdf"}}).get_json()["id"])
        self.assertEqual(job.status, "failed")
        self.assertIn("agent-server", job.error)


if __name__ == "__main__":
    unittest.main()
