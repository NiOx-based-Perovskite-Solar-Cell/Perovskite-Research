"""Dashboard API: runs from folders and from web jobs, one run, two runs side by side, the values that did not match."""
import tempfile
import time
import unittest
from pathlib import Path

from _fake import runtime
from _fixtures import write_pdf
from test_evaluate import PAGE1, PAGE2, fake_mapper, record_s1, record_s2
from test_providers import bad_extractor, good_extractor

from psc_agent import Extractor, write_workbook
from psc_agent.evaluate import evaluate_pairs, write_report
from psc_agent.sources.pairing import PaperPair
from psc_agent.web import WebConfig, create_app

KEY = "101234test5678"


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        raw, pdfs = self.root / "Raw Data", self.root / "original paper"
        raw.mkdir(), pdfs.mkdir()
        pdf = write_pdf(pdfs / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        record = []
        for n, sheets in enumerate((record_s1(), record_s2()), 1):
            sheets["Stability"] = []
            sheets["Process"] = [r for r in sheets["Process"] if r["LayerID"] == 2]
            record.append(write_workbook(sheets, raw / f"10.1234_test.5678_Sample{n}.xlsx"))
        self.pair = PaperPair(KEY, pdf, record)
        self.runs = self.root / "evaluate_out"
        for name, extractor, model in (("bad", bad_extractor, "small/model"), ("good", good_extractor, "big/model")):
            rt, _ = runtime({"mapper": fake_mapper, "extract": extractor})
            evals = evaluate_pairs(Extractor(rt, decide_rows=False), [self.pair], self.runs / name)
            write_report(evals, self.runs / name, {"provider": "fake", "model": model})
        (self.runs / "empty").mkdir()  # a folder with no finished paper is not a run
        self.app = create_app(WebConfig(root=self.root, require_key=False, eval_dirs=[self.runs],
                                        runtime_factory=self.factory, max_workers=1))
        self.app.testing = True
        self.c = self.app.test_client()

    @staticmethod
    def factory(models, hooks, cfg):
        return runtime({"mapper": fake_mapper, "extract": good_extractor}, models=models, hooks=hooks, skills_dirs=[cfg.skills_dir])[0]

    def test_runs_come_from_folders_with_finished_papers(self):
        runs = self.c.get("/api/dashboard/runs").get_json()["runs"]
        self.assertEqual(sorted(r["id"] for r in runs), ["dir:0:bad", "dir:0:good"])
        good = next(r for r in runs if r["id"].endswith("good"))
        self.assertEqual((good["papers"], good["provider"], good["model"], good["label"]), (1, "fake", "big/model", "evaluate_out/good"))

    def test_one_run(self):
        r = self.c.get("/api/dashboard/run?id=dir:0:bad").get_json()
        v = r["summary"]["values"]
        self.assertEqual(r["meta"], {"provider": "fake", "model": "small/model"})
        self.assertGreater(v["n"], 0)
        self.assertLess(v["match_rate"], 1)  # the wrong PCE is in there
        self.assertEqual(r["summary"]["by_sheet"]["Properties"]["values"]["mismatch"], 1)
        (paper,) = r["papers"]
        self.assertEqual((paper["key"], paper["record_devices"], paper["paired_devices"]), (KEY, 2, 2))
        self.assertIn("calls", r["totals"])

    def test_two_runs(self):
        r = self.c.get("/api/dashboard/compare?a=dir:0:bad&b=dir:0:good").get_json()
        self.assertEqual((r["labels"], r["papers"]), (["evaluate_out/bad", "evaluate_out/good"], [KEY]))
        self.assertEqual((r["agree"]["only_b"], r["agree"]["only_a"]), (1, 0))  # the PCE that B got right and A got wrong
        (d,) = r["differing"]
        self.assertEqual((d["column"], d["status"], d["other_status"], d["extracted"], d["other_extracted"]), ("Values", "mismatch", "match", "23.9", "22.5"))
        self.assertGreater(r["b"]["values"]["match_rate"], r["a"]["values"]["match_rate"])
        self.assertEqual(set(r["by_paper"][KEY]), {"a", "b"})

    def test_the_values_that_did_not_match(self):
        get = lambda q: self.c.get(f"/api/dashboard/cells?id=dir:0:bad&{q}").get_json()  # noqa: E731
        problems = get("")
        self.assertTrue(any(c["column"] == "Values" and c["status"] == "mismatch" and c["extracted"] == "23.9" for c in problems["cells"]))
        self.assertTrue(all(c["status"] != "match" for c in problems["cells"]))
        self.assertTrue(all(c["status"] == "match" for c in get("status=match")["cells"]))
        self.assertTrue(all(c["sheet"] == "Properties" for c in get("sheet=Properties")["cells"]))
        self.assertTrue(all(c["core"] for c in get("core=1")["cells"]))
        self.assertEqual(get("paper=nothing")["total"], 0)
        self.assertLessEqual(len(get("limit=1")["cells"]), 1)

    def test_bad_requests(self):
        for url in ("/api/dashboard/run?id=dir:0:nope", "/api/dashboard/run?id=dir:0:../x", "/api/dashboard/run?id=dir:9:bad",
                    "/api/dashboard/run?id=job:nope", "/api/dashboard/run?id=", "/api/dashboard/cells?id=whatever"):
            with self.subTest(url):
                self.assertEqual(self.c.get(url).status_code, 404)
        self.assertEqual(self.c.get("/api/dashboard/compare?a=dir:0:bad&b=dir:0:empty").status_code, 404)

    def test_an_evaluate_job_from_the_web_becomes_a_run(self):
        r = self.c.post("/api/jobs", json={"kind": "evaluate", "params": {"dois": [KEY], "workers": 1}})
        self.assertEqual(r.status_code, 202)
        job_id = r.get_json()["id"]
        jobs = self.app.config["JOBS"]
        for _ in range(100):
            if jobs.get(job_id).status in ("done", "failed"):
                break
            time.sleep(0.1)
        job = jobs.get(job_id)
        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual(job.summary["papers"], 1)
        self.assertEqual(job.to_dict()["progress"]["fraction"], 1.0)
        runs = self.c.get("/api/dashboard/runs").get_json()["runs"]
        mine = next(x for x in runs if x["id"] == f"job:{job_id}")
        self.assertEqual((mine["source"], mine["papers"]), ("web job", 1))
        body = self.c.get(f"/api/dashboard/run?id=job:{job_id}").get_json()
        self.assertEqual(body["summary"]["values"]["mismatch"], 0)  # the good extractor

    def test_evaluate_job_parameters_are_checked(self):
        bad = lambda params: self.c.post("/api/jobs", json={"kind": "evaluate", "params": params}).status_code  # noqa: E731
        self.assertEqual(bad({"dois": "not a list"}), 400)
        self.assertEqual(bad({"dois": [KEY], "budget": 5000}), 400)
        self.assertEqual(bad({"dois": [KEY], "model": "bad model!"}), 400)


if __name__ == "__main__":
    unittest.main()
