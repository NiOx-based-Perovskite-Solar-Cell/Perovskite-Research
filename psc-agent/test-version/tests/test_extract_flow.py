"""The web extraction in three steps: choose a paper, settle its SI (check, find, upload), then extract with the SI."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf

from psc_agent.web import WebConfig, create_app

OTHER = "doi: 10.1016/j.test.2024.1 Methods. The details are in the Supporting Information."  # a publisher with no known page
ACS = "doi: 10.1021/acsami.5c13836 Figures S1 and S2 show more."
PLAIN = "doi: 10.1016/j.test.2024.2 A complete paper."
SI_TEXT = "Supporting Information. Table S1: PEAI was spin coated at 4000 rpm for 30 s."


def mapper(body):
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "d"}], "rows": []}


class Flow(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Raw Data").mkdir()
        self.pdfs = self.root / "original paper"
        self.pdfs.mkdir()
        write_pdf(self.pdfs / "other.pdf", OTHER)
        write_pdf(self.pdfs / "acs.pdf", ACS)
        write_pdf(self.pdfs / "plain.pdf", PLAIN)
        self.seen: list[dict] = []
        self.reading = {"refers_to_si": True, "evidence": "e", "address": "", "holds": ""}  # what the SI reader model answers
        self.app = create_app(WebConfig(root=self.root, require_key=False, runtime_factory=self.factory, max_workers=1, eval_dirs=[]))
        self.app.testing = True
        self.c = self.app.test_client()

    def factory(self, models, hooks, cfg):
        def si_finder(body):
            self.seen.append({"role": "si_finder", "model": models.get("si_finder"), "tools": [t["function"]["name"] for t in body.get("tools", [])]})
            return json.dumps({"status": "found", "notes": "the publisher page", "links": []})

        def si_reader(body):
            self.seen.append({"role": "si_reader", "model": models.get("si_reader"), "tools": [t["function"]["name"] for t in body.get("tools", [])]})
            return self.reading

        def spy(body):
            self.seen.append({"role": "mapper", "text": last_user_text(body)})
            return mapper(body)

        return runtime({"si_reader": si_reader, "si_finder": si_finder, "mapper": spy, "extract": lambda b: {"rows": []}}, models=models, hooks=hooks,
                       skills_dirs=[cfg.skills_dir])[0]

    def wait(self, job_id):
        jobs = self.app.config["JOBS"]
        for _ in range(150):
            if jobs.get(job_id).status in ("done", "failed"):
                break
            time.sleep(0.1)
        return jobs.get(job_id)

    def plan(self, name):
        return self.c.get(f"/api/si/plan?pdf=pdf:{name}").get_json()

    def upload_si(self, key, text=SI_TEXT):
        data = write_pdf(self.root / "si_src.pdf", text, pages=3).read_bytes()
        return self.c.post("/api/si", data={"key": key, "files": (io.BytesIO(data), "si.pdf")}, content_type="multipart/form-data")

    # ---- step 2: the plan, from the paper's own text
    def test_the_plan_for_each_kind_of_paper(self):
        plain = self.plan("plain.pdf")
        self.assertEqual((plain["status"], plain["links"], plain["key"]), ("not_referenced", [], "101016jtest20242"))
        acs = self.plan("acs.pdf")
        self.assertEqual((acs["status"], acs["links"][0]), ("pattern", "https://pubs.acs.org/doi/suppl/10.1021/acsami.5c13836"))
        other = self.plan("other.pdf")
        self.assertEqual((other["status"], other["save_as"]), ("agent_needed", "101016jtest20241_SI.pdf"))
        self.assertEqual(other["files"], [])

    def test_a_saved_si_turns_the_plan_into_have(self):
        self.assertEqual(self.upload_si("101016jtest20241").status_code, 201)
        plan = self.plan("other.pdf")
        self.assertEqual((plan["status"], [f["name"] for f in plan["files"]]), ("have", ["101016jtest20241_SI.pdf"]))
        self.assertEqual(self.c.delete("/api/si/101016jtest20241_SI.pdf").status_code, 200)
        self.assertEqual(self.plan("other.pdf")["status"], "agent_needed")

    def test_an_uploaded_pdf_gets_a_plan_too(self):
        data = write_pdf(self.root / "new.pdf", OTHER.replace("2024.1", "2024.7")).read_bytes()
        up = self.c.post("/api/uploads", data={"files": (io.BytesIO(data), "new paper.pdf")}, content_type="multipart/form-data").get_json()
        pid = up["files"][0]["id"]
        plan = self.c.get(f"/api/si/plan?pdf={pid}").get_json()
        self.assertEqual((plan["status"], plan["key"]), ("agent_needed", "101016jtest20247"))
        self.assertEqual(self.upload_si(plan["key"]).status_code, 201)
        self.assertEqual(self.c.get(f"/api/si/plan?pdf={pid}").get_json()["status"], "have")

    def test_bad_requests(self):
        self.assertEqual(self.c.get("/api/si/plan?pdf=pdf:nothing.pdf").status_code, 404)
        self.assertEqual(self.c.get("/api/si/plan?pdf=../x").status_code, 400)
        self.assertEqual(self.c.get("/api/si/plan").status_code, 400)
        self.assertEqual(self.c.post("/api/jobs", json={"kind": "si", "params": {"pdf": "pdf:nothing.pdf"}}).status_code, 404)

    # ---- the SI agent: addresses only
    def test_the_si_agent_job_answers_with_links_and_downloads_nothing(self):
        r = self.c.post("/api/jobs", json={"kind": "si", "params": {"pdf": "pdf:other.pdf", "si_finder_model": "big/sonnet", "budget": 1}})
        self.assertEqual(r.status_code, 202)
        job = self.wait(r.get_json()["id"])
        self.assertEqual((job.status, job.error), ("done", None))
        self.assertEqual(job.summary["key"], "101016jtest20241")
        self.assertIn("https://doi.org/10.1016/j.test.2024.1", job.summary["links"])  # the DOI link behind whatever the agent saw
        asked = next(s for s in self.seen if s["role"] == "si_finder")
        self.assertEqual(asked["model"], "big/sonnet")
        self.assertEqual(sorted(asked["tools"]), ["read_page", "web_search"])  # no download tool
        self.assertFalse((self.pdfs / "SI").exists())

    def si_job(self, name, **params):
        r = self.c.post("/api/jobs", json={"kind": "si", "params": {"pdf": f"pdf:{name}", **params}})
        self.assertEqual(r.status_code, 202)
        job = self.wait(r.get_json()["id"])
        self.assertEqual((job.status, job.error), ("done", None))
        return job

    def test_the_agent_is_not_called_when_the_answer_is_cheaper(self):
        self.si_job("acs.pdf")  # the reader says yes; the publisher page follows from the DOI
        self.reading = {**self.reading, "refers_to_si": False}
        self.si_job("plain.pdf")  # the reader says there is none
        self.assertEqual([s for s in self.seen if s["role"] == "si_finder"], [])

    # ---- the model as the SI extractor: it reads the paper's text
    def test_the_model_reads_the_paper_with_the_default_small_model_and_no_tools(self):
        self.si_job("acs.pdf")
        (asked,) = [s for s in self.seen if s["role"] == "si_reader"]
        self.assertEqual((asked["model"], asked["tools"]), ("anthropic/claude-haiku-5.5", []))
        self.si_job("acs.pdf", si_reader_model="big/reader")
        self.assertEqual([s["model"] for s in self.seen if s["role"] == "si_reader"][-1], "big/reader")

    def test_the_model_can_overrule_the_rule_in_both_directions(self):
        self.reading = {"refers_to_si": False, "evidence": "No additional information is available.", "address": "", "holds": ""}
        job = self.si_job("other.pdf")  # the rule sees the words and would send the agent searching
        self.assertEqual(job.summary["status"], "not_referenced")
        self.assertIn("No additional information is available.", job.summary["notes"])
        self.assertEqual([s for s in self.seen if s["role"] == "si_finder"], [])
        self.reading = {"refers_to_si": True, "evidence": "e", "address": "", "holds": ""}
        self.si_job("plain.pdf")  # the rule said none; the model said yes, so the web agent was sent
        self.assertEqual(len([s for s in self.seen if s["role"] == "si_finder"]), 1)

    def test_an_address_printed_in_the_paper_is_returned_without_the_web_agent(self):
        write_pdf(self.pdfs / "printed.pdf", "doi: 10.3390/nano10101961 Supplementary Materials: available online at http://www.mdpi.com/2079-4991/10/10/1961/s1")
        self.reading = {"refers_to_si": True, "evidence": "e", "address": "http://www.mdpi.com/2079-4991/10/10/1961/s1", "holds": "Figures S1-S6"}
        job = self.si_job("printed.pdf")
        self.assertEqual((job.summary["status"], job.summary["links"][0]), ("printed", "http://www.mdpi.com/2079-4991/10/10/1961/s1"))
        self.assertEqual([s for s in self.seen if s["role"] == "si_finder"], [])

    # ---- step 3: the extraction reads the paper and its SI once
    def extract(self, name):
        r = self.c.post("/api/jobs", json={"kind": "extract", "params": {"pdf": f"pdf:{name}", "no_decide": True, "budget": 1}})
        self.assertEqual(r.status_code, 202)
        job = self.wait(r.get_json()["id"])
        self.assertEqual((job.status, job.error), ("done", None))
        return job

    def test_extract_reads_the_saved_si_with_the_paper(self):
        self.upload_si("101016jtest20241")
        job = self.extract("other.pdf")
        self.assertEqual(job.summary["si_files"], 1)
        text = "\n".join(s["text"] for s in self.seen if s["role"] == "mapper")
        self.assertIn("4000 rpm", text)
        self.assertIn("(supporting information)", text)
        self.assertEqual(len([s for s in self.seen if s["role"] == "mapper"]), 1)  # the paper was read once
        self.assertTrue(any("1 file(s) read with the paper" in line for line in job.log))

    def test_extract_without_an_si_still_works_and_says_so(self):
        job = self.extract("other.pdf")
        self.assertEqual(job.summary["si_files"], 0)
        self.assertNotIn("(supporting information)", "\n".join(s["text"] for s in self.seen if s["role"] == "mapper"))
        self.assertTrue(any("none loaded" in line for line in job.log))
        review = self.c.get(f"/api/jobs/{job.id}/files").get_json()["files"]
        self.assertTrue(any(f["path"] == "review.csv" for f in review))


if __name__ == "__main__":
    unittest.main()
