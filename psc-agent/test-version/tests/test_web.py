"""Web service: pages, API validation, jobs end to end with a fake model per role, files, skills, safety."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from _fake import FakeRouter, last_user_text
from _fixtures import LAYER, write_pdf, write_workbook
from test_tools import write_data
from test_workflows import PAGE1, PAGE2, analyst, decision, extractor, mapper, planner

from psc_agent import HookBlock, OpenRouterClient, Runtime
from psc_agent.web import WebConfig, create_app
from psc_agent.web.jobs import CancelHook, JobManager

SECRET = "sk-or-v1-secret-key-that-must-not-leak-000"


def accept_all(body):
    items = [json.loads(l) for l in last_user_text(body).splitlines()[1:]]
    return {"verdicts": [{"id": i["id"], "choice": "accept", "p": 0.95, "reason": "ok"} for i in items]}


def recheck(body):
    import re

    ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
    return {"devices": [{"device_id": "D1", "paper_device": "control"}],
            "rows": [{"row_id": i, "found": True, "values": [{"column": "Thickness (nm)", "value": "25"}], "page": 1,
                      "source": "text", "evidence": None, "note": None, "key_issue": None} for i in ids]}


RESPONDERS = {"planner": planner, "analyst": analyst, "decision": decision, "reporter": lambda b: "# Key insights\n- x",
              "mapper": mapper, "extract": extractor, "recheck": recheck}


def make_project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "Raw Data").mkdir()
    (root / "original paper").mkdir()
    (root / "processed" / "prepared_v2").mkdir(parents=True)
    write_data(root / "processed" / "prepared_v2")
    write_workbook(root / "Raw Data" / "10.1016_j.test.2020.123_Sample1.xlsx", {"Layer": LAYER})
    write_pdf(root / "original paper" / "paper one.pdf", "https://doi.org/10.1016/j.test.2020.123")
    write_pdf(root / "original paper" / "new paper.pdf", PAGE1, page_texts=[PAGE2])
    return root


def make_app(root: Path, responders=None, **kw):
    fake = FakeRouter(responders or RESPONDERS)

    def factory(models, hooks, cfg):
        client = OpenRouterClient("sk-test-0000000000000000", transport=httpx.MockTransport(fake), max_retries=0)
        return Runtime(models, client=client, skills_dirs=[cfg.skills_dir], hooks=hooks)

    cfg = WebConfig(root=root, require_key=False, runtime_factory=factory, max_workers=1, **kw)
    app = create_app(cfg)
    app.testing = True
    return app, fake, cfg


def wait(client, job_id: str, timeout: float = 60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


class Web(unittest.TestCase):
    def setUp(self):
        self.root = make_project()
        self.app, self.fake, self.cfg = make_app(self.root)
        self.c = self.app.test_client()

    def test_pages_and_status_never_leak_the_key(self):
        page = self.c.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"PSC Agent", page.data)
        self.assertIn(b"/static/app.css", page.data)
        for asset in ("/static/app.css", "/static/app.js"):
            with self.c.get(asset) as res:
                self.assertEqual(res.status_code, 200, asset)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": SECRET}):
            res = self.c.get("/api/status")
            self.assertTrue(res.get_json()["key_set"])
            self.assertNotIn(SECRET.encode(), res.data)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            self.assertFalse(self.c.get("/api/status").get_json()["key_set"])

    def test_pairs_and_pdfs(self):
        pairs = self.c.get("/api/pairs").get_json()
        self.assertEqual([p["key"] for p in pairs["pairs"]], ["101016jtest2020123"])
        self.assertEqual(pairs["unmatched_pdfs"], ["new paper.pdf"])
        pdfs = {p["name"]: p for p in self.c.get("/api/pdfs").get_json()["pdfs"]}
        self.assertTrue(pdfs["paper one.pdf"]["has_workbooks"])
        self.assertEqual(pdfs["new paper.pdf"]["id"], "pdf:new paper.pdf")

    def test_analyze_job_end_to_end(self):
        res = self.c.post("/api/jobs", json={"kind": "analyze", "params": {"goal": "what drives PCE?", "budget": 1}})
        self.assertEqual(res.status_code, 202)
        job = wait(self.c, res.get_json()["id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["summary"]["counts"], {"accept": 1, "verify_more": 1})
        self.assertGreater(job["cost_usd"], 0)
        self.assertTrue(any(line.split(" ", 1)[1].startswith("analyst") for line in job["log"]))  # ProgressHook
        analysis = self.c.get(f"/api/jobs/{job['id']}/analysis").get_json()
        self.assertEqual([a["id"] for a in analysis["result"]["acs"]], ["AC-1", "AC-2"])
        self.assertTrue(analysis["insights"].startswith("# Key insights"))
        files = {f["path"] for f in self.c.get(f"/api/jobs/{job['id']}/files").get_json()["files"]}
        self.assertTrue({"PLAN.md", "INSIGHTS.md", "trace.jsonl", "job.json", "evidence/AC-1.json"} <= files)
        with self.c.get(f"/api/jobs/{job['id']}/files/PLAN.md") as res:
            self.assertEqual(res.status_code, 200)
        self.assertEqual(self.c.get(f"/api/jobs/{job['id']}/files/..%2F..%2FRaw%20Data").status_code, 404)
        self.assertEqual(self.c.get(f"/api/jobs/{job['id']}/files/../job.json").status_code, 404)
        trace = self.c.get(f"/api/jobs/{job['id']}/trace").get_json()
        self.assertIn("Tool calls that worked", trace["markdown"])
        self.assertTrue((self.cfg.skills_dir / "data-map" / "SKILL.md").is_file())  # DataMapHook ran

        learn = self.c.post("/api/jobs", json={"kind": "learn", "params": {"jobs": [job["id"]]}})
        self.assertEqual(wait(self.c, learn.get_json()["id"])["status"], "done")
        self.assertIn("Verified tool calls", self.c.get("/api/skills/learned-rules").get_json()["text"])

    def test_recheck_job_findings(self):
        res = self.c.post("/api/jobs", json={"kind": "recheck", "params": {"dois": ["101016jtest2020123"], "no_judge": True}})
        job = wait(self.c, res.get_json()["id"])
        self.assertEqual((job["status"], job["summary"]["papers"]), ("done", 1))
        rows = self.c.get(f"/api/jobs/{job['id']}/findings").get_json()["rows"]
        self.assertEqual({r["status"] for r in rows}, {"mismatch", "missing_in_record"})  # NiOx 20 vs 25; ITO empty vs 25

    def test_recheck_review_opens_the_paper_at_each_finding(self):
        res = self.c.post("/api/jobs", json={"kind": "recheck", "params": {"dois": ["101016jtest2020123"], "no_judge": True}})
        job = wait(self.c, res.get_json()["id"])
        jid, key = job["id"], "101016jtest2020123"
        src = self.c.get(f"/api/jobs/{jid}/sources?paper={key}").get_json()
        self.assertEqual((src["pdf"], src["si"]), ("paper one.pdf", []))
        self.assertNotIn("pdf_path", src)  # server paths stay on the server
        self.assertEqual(self.c.get(f"/api/jobs/{jid}/sources").status_code, 400)  # a recheck job has one paper per key
        r = self.c.get(f"/api/jobs/{jid}/source/0?paper={key}")
        self.assertEqual((r.status_code, r.mimetype), (200, "application/pdf"))
        self.assertEqual(self.c.get(f"/api/jobs/{jid}/source/1?paper={key}").status_code, 404)
        self.assertEqual(self.c.get(f"/api/jobs/{jid}/source/0?paper=../../etc").status_code, 400)
        hit = self.c.get(f"/api/jobs/{jid}/locate?row=f0").get_json()
        self.assertEqual(hit["paper"], key)
        self.assertEqual(self.c.get(f"/api/jobs/{jid}/locate?row=f999").status_code, 404)
        self.assertIn(b"review.js", self.c.get(f"/review/{jid}").data)


    def test_extract_job_from_an_upload(self):
        pdf = (self.root / "original paper" / "new paper.pdf").read_bytes()
        up = self.c.post("/api/uploads", data={"files": [(__import__("io").BytesIO(pdf), "My Paper (v2).pdf")]},
                         content_type="multipart/form-data")
        self.assertEqual(up.status_code, 201)
        file_id = up.get_json()["files"][0]["id"]
        self.assertEqual(file_id, "upload:My_Paper_v2.pdf")
        self.fake.responders["decision"] = accept_all
        res = self.c.post("/api/jobs", json={"kind": "extract", "params": {"pdf": file_id}})
        job = wait(self.c, res.get_json()["id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["summary"]["files"], ["10.1234_test.5678_Sample1.xlsx", "10.1234_test.5678_Sample2.xlsx"])
        rows = self.c.get(f"/api/jobs/{job['id']}/review").get_json()["rows"]
        self.assertEqual(len(rows), 10)
        with self.c.get(f"/api/jobs/{job['id']}/files/10.1234_test.5678_Sample1.xlsx?download=1") as dl:
            self.assertIn("attachment", dl.headers["Content-Disposition"])

    def test_request_validation(self):
        post = lambda body: self.c.post("/api/jobs", json=body)  # noqa: E731
        self.assertEqual(post({"kind": "rm -rf"}).status_code, 400)
        self.assertEqual(post({"kind": "analyze", "params": {"goal": "x", "model": "a b; c"}}).status_code, 400)
        self.assertEqual(post({"kind": "analyze", "params": {"goal": "x", "budget": 5000}}).status_code, 400)
        self.assertEqual(post({"kind": "analyze", "params": {"goal": "  "}}).status_code, 400)
        self.assertEqual(post({"kind": "extract", "params": {"pdf": "pdf:../Raw Data/x.pdf"}}).status_code, 404)
        self.assertEqual(post({"kind": "extract", "params": {"pdf": "C:/Windows/x.pdf"}}).status_code, 400)
        self.assertEqual(post({"kind": "recheck", "params": {"dois": "all"}}).status_code, 400)
        self.assertEqual(post({"kind": "learn", "params": {"jobs": ["nope"]}}).status_code, 400)
        bad = self.c.post("/api/uploads", data={"files": [(__import__("io").BytesIO(b"MZ\x90"), "evil.pdf")]},
                          content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.c.post("/api/uploads", data={"files": [(__import__("io").BytesIO(b"x"), "a.exe")]},
                                     content_type="multipart/form-data").status_code, 400)
        app, _, _ = make_app(self.root)
        app.config["PSC"].require_key = True
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            res = app.test_client().post("/api/jobs", json={"kind": "analyze", "params": {"goal": "x"}})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.c.get("/api/jobs/nope").status_code, 404)

    def test_skills_api(self):
        names = {s["name"]: s for s in self.c.get("/api/skills").get_json()["skills"]}
        self.assertEqual(names["analyst-tools"]["source"], "default")
        self.assertTrue(names["analyst-tools"]["read_only"])
        good = "---\nname: lab-rules\nroles: [analyst]\norder: 70\n---\nAlways report n papers."
        put = lambda name, text: self.c.put(f"/api/skills/{name}", json={"text": text})  # noqa: E731
        self.assertEqual(put("Bad Name", good).status_code, 400)
        self.assertEqual(put("lab-rules", "no front matter").status_code, 400)
        self.assertEqual(put("other-name", good).status_code, 400)
        self.assertEqual(put("data-map", good.replace("lab-rules", "data-map")).status_code, 400)
        self.assertEqual(put("lab-rules", good).status_code, 200)
        prompt = self.c.get("/api/prompt?role=analyst").get_json()
        self.assertIn("lab-rules", prompt["skills"])
        self.assertIn("Always report n papers.", prompt["prompt"])
        self.assertEqual(self.c.get("/api/prompt?role=hacker").status_code, 400)
        self.assertEqual(self.c.delete("/api/skills/lab-rules").status_code, 200)
        self.assertNotIn("lab-rules", self.c.get("/api/prompt?role=analyst").get_json()["skills"])
        self.assertEqual(self.c.delete("/api/skills/analyst-tools").status_code, 400)  # defaults cannot be deleted

    def test_cancel_and_restart(self):
        manager = JobManager(Path(tempfile.mkdtemp()), max_workers=1)

        def blocked(job):
            job.cancel.set()
            CancelHook(job).on_before_model()

        job = manager.submit("analyze", {}, blocked)
        for _ in range(200):
            if job.status not in ("queued", "running"):
                break
            time.sleep(0.01)
        self.assertEqual(job.status, "cancelled")
        with self.assertRaises(HookBlock):
            CancelHook(job).on_before_model()
        stuck = json.loads((Path(job.out_dir) / "job.json").read_text(encoding="utf-8"))
        stuck["status"] = "running"
        (Path(job.out_dir) / "job.json").write_text(json.dumps(stuck), encoding="utf-8")
        again = JobManager(manager.runs_dir)
        self.assertEqual(again.get(job.id).status, "interrupted")


if __name__ == "__main__":
    unittest.main()
