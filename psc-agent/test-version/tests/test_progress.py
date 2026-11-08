"""Progress and the time left: workflows report steps, a job turns them into a fraction and an estimate."""
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf, write_workbook

from psc_agent import Extractor, Rechecker
from psc_agent.web.jobs import Job, ProgressHook


class Recorder:
    def __init__(self):
        self.steps = []

    def on_progress(self, scope, done, total, **_):
        self.steps.append((done, total))


def job() -> Job:
    return Job("j", "extract", {}, tempfile.mkdtemp())


class JobMath(unittest.TestCase):
    def test_nothing_done_gives_no_estimate(self):
        j = job()
        j.status, j.started = "running", datetime.now().isoformat(timespec="seconds")
        self.assertEqual((j.progress()["fraction"], j.progress()["eta_seconds"]), (0.0, None))

    def test_estimate_scales_the_time_so_far(self):
        j = job()
        j.status, j.started = "running", (datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds")
        j.set_steps("paper", 1, 4)  # a quarter done in a minute: three more minutes
        p = j.progress()
        self.assertEqual(p["fraction"], 0.25)
        self.assertAlmostEqual(p["eta_seconds"], 180, delta=3)
        self.assertEqual(j.to_dict()["progress"]["fraction"], 0.25)

    def test_papers_not_started_yet_count_as_not_done(self):
        j = job()
        j.status, j.started = "running", (datetime.now() - timedelta(seconds=100)).isoformat(timespec="seconds")
        ProgressHook(j).on_batch(papers=4)
        ProgressHook(j).on_progress("a", 3, 3)  # one paper of four finished
        ProgressHook(j).on_progress("b", 1, 3)
        self.assertAlmostEqual(j.progress()["fraction"], (1 + 1 / 3) / 4, places=3)

    def test_a_finished_job_has_no_estimate(self):
        j = job()
        j.status = "done"
        self.assertEqual(j.progress(), {"fraction": 1.0, "eta_seconds": None, "elapsed_seconds": None})


class WorkflowsReportSteps(unittest.TestCase):
    def test_extract_ticks_once_per_stage(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "p.pdf", "doi 10.1234/test.5678", page_texts=["The champion PCE was 20.1%."])
        rec = Recorder()
        rt, _ = runtime({"mapper": lambda b: {"devices": [{"sample": "S1", "paper_name": "c", "description": "d"}], "rows": []},
                         "extract": lambda b: {"rows": []}}, hooks=[rec])
        Extractor(rt, decide_rows=False).extract(pdf, tmp / "out")
        self.assertEqual(rec.steps, [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)])  # map, three sheet groups, verdicts

    def test_recheck_ticks_per_sheet_group_and_the_judge(self):
        tmp = Path(tempfile.mkdtemp())
        wb = write_workbook(tmp / "10.1016_j.test.2020.123_Sample1.xlsx",
                            {"Layer": [("LayerID", "LayerType", "Material", "Thickness (nm)"), (1, "Anode", "ITO", 150)]})
        pdf = write_pdf(tmp / "p.pdf", "doi 10.1016/j.test.2020.123")

        def recheck(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            return {"devices": [{"device_id": "D1", "paper_device": "c"}],
                    "rows": [{"row_id": i, "found": False, "values": [], "page": 1, "source": "text", "evidence": None,
                              "note": None, "key_issue": None} for i in ids]}

        rec = Recorder()
        rt, _ = runtime({"recheck": recheck}, hooks=[rec])
        Rechecker(runtime=rt, judge=False).recheck(pdf, [wb])
        self.assertEqual(rec.steps[0], (0, 2))
        self.assertEqual(rec.steps[-1], (2, 2))
        self.assertEqual([s for s in rec.steps if s[1] != 2], [])


if __name__ == "__main__":
    unittest.main()
