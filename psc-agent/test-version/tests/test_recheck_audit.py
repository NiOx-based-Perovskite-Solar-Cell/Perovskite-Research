"""The auditor: a second, open reading of the cells where the record and the agent disagree."""
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf, write_workbook

from psc_agent import Rechecker

TEXT = ("doi 10.1234/test.5678 C60 (20 nm), BCP (7 nm), and Cu (100 nm) in sequential order were thermally evaporated "
        "in a vacuum chamber (< 3x10-4 Pa).")
QUOTE = "thermally evaporated in a vacuum chamber (< 3x10-4 Pa)"


def row(rid, **values):
    return {"row_id": rid, "found": True, "values": [{"column": c, "value": v} for c, v in values.items()], "page": 1,
            "source": "text", "evidence": "C60 (20 nm)", "note": None, "key_issue": None}


class Audit(unittest.TestCase):
    def run_recheck(self, verdict, recorded="< 3E-4", unit="Pa", agent="20 nm", **kw):
        tmp = Path(tempfile.mkdtemp())
        sheet = {"Process": [("LayerID", "StepOrder", "ProcessType", "Parameter", "Unit"), (6, 1, "ThermalEvaporation", recorded, unit)]}
        wb = write_workbook(tmp / "10.1234_test.5678_Sample1.xlsx", sheet)
        pdf = write_pdf(tmp / "p.pdf", TEXT)
        self.items = None

        def recheck(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            return {"devices": [{"device_id": "D1", "paper_device": "c"}], "rows": [row(i, Parameter=agent) for i in ids]}

        def auditor(body):
            text = last_user_text(body)
            self.items = text
            ids = re.findall(r'"id":\s*"(\d+)"', text)
            return {"audits": [{"id": i, **verdict} for i in ids]}

        rt, _ = runtime({"recheck": recheck, "recheck_auditor": auditor, "judge": lambda b: {"verdicts": []}})
        (f,) = Rechecker(runtime=rt, **kw).recheck(pdf, [wb]).findings
        return f

    def test_the_person_recorded_another_parameter_of_the_step_and_is_right(self):
        f = self.run_recheck({"verdict": "both_correct", "p": 0.95, "evidence": QUOTE, "page": 1,
                              "explanation": "the pressure and the thickness are both given for the evaporation"})
        self.assertEqual((f.status, f.audit, f.audit_p, f.audit_quote_ok), ("match", "both_correct", 0.95, True))
        self.assertEqual((f.recorded, f.paper_value), ("< 3E-4", "20 nm"))  # neither value is changed
        self.assertEqual(f.audit_evidence, QUOTE)
        self.assertIn("auditor (both_correct, p=0.95)", f.detail)
        self.assertIn('"recorded": "< 3E-4 Pa"', self.items)  # the auditor sees both values
        self.assertIn('"agent": "20 nm', self.items)

    def test_a_slip_of_the_person_becomes_a_mismatch(self):
        f = self.run_recheck({"verdict": "agent_correct", "p": 0.9, "evidence": "C60 (20 nm), BCP (7 nm)", "page": 1,
                              "explanation": "the paper gives 20 nm; 25 nm is nowhere"}, recorded="25", unit="nm")
        self.assertEqual((f.status, f.audit), ("mismatch", "agent_correct"))

    def test_an_unsure_auditor_changes_nothing(self):
        f = self.run_recheck({"verdict": "human_correct", "p": 0.5, "evidence": QUOTE, "page": 1, "explanation": "perhaps"})
        self.assertEqual((f.status, f.audit, f.audit_p), ("review", "human_correct", 0.5))
        self.assertIn("escalate: auditor said human_correct (p=0.50)", f.detail)

    def test_a_quote_that_is_not_in_the_paper_changes_nothing(self):
        f = self.run_recheck({"verdict": "human_correct", "p": 0.99, "evidence": "the chamber was held at a pressure of 3E-4 Pa throughout", "page": 1,
                              "explanation": "made up"})
        self.assertEqual((f.status, f.audit_quote_ok), ("review", False))
        self.assertIn("its quote is not on the page", f.detail)

    def test_no_audit(self):
        f = self.run_recheck({"verdict": "both_correct", "p": 0.95, "evidence": QUOTE, "page": 1, "explanation": "x"}, audit=False)
        self.assertEqual((f.status, f.audit, self.items), ("review", None, None))


if __name__ == "__main__":
    unittest.main()
