"""Fewer false alarms in recheck: extra values, unit slips, "98+", free text left blank, and values only the SI holds."""
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf, write_workbook

from psc_agent import Rechecker, load_paper
from psc_agent.recheck.compare import compare_values

TOL = 0.001


def cmp(*a):
    v = compare_values(*a, tol=TOL)
    return v.status, v.detail


class Compare(unittest.TestCase):
    def test_extra_values_the_paper_gives_are_not_an_error(self):
        status, detail = cmp("8e-06", "torr", "100, 8e-6", "nm, torr")
        self.assertEqual(status, "match")
        self.assertIn("paper also gives 100 nm (not recorded)", detail)
        self.assertEqual(cmp("< 8E-4 Pa", None, "100, <8\u00d710-4", "nm, Pa")[0], "match")

    def test_a_real_difference_is_still_a_mismatch(self):
        self.assertEqual(cmp("3000", "RPM", "4000", "RPM")[0], "mismatch")
        self.assertEqual(cmp("8e-06", "torr", "100, 5e-6", "nm, torr")[0], "mismatch")  # the matching unit disagrees
        self.assertEqual(cmp("4000, 30", "RPM, s", "4000", "RPM")[0], "mismatch")  # the paper lacks one value
        self.assertEqual(cmp("1000, 10", None, "1000, 10, 5", None)[0], "mismatch")  # no units: cannot pair

    def test_a_plus_sign_is_a_qualifier_not_a_unit(self):
        self.assertEqual(cmp("98", "%", "98+", "%")[0], "match")

    def test_a_wrong_recorded_unit_is_named(self):
        status, detail = cmp("70, 1440", "C, sec", "70, 24", "C, h")
        self.assertEqual(status, "unit_mismatch")
        self.assertIn("if the recorded unit is min, not sec", detail)

    def test_two_kinds_of_quantity_are_a_review_not_a_mismatch(self):
        status, detail = cmp("25", "mg/ml", "1", "%")
        self.assertEqual(status, "review")
        self.assertIn("different kinds of quantity", detail)

    def test_the_amount_in_a_unit_note_counts(self):
        status, detail = cmp("0.1", "M (0.5 mmol in 5 mL)", "0.124, 0.5", "g, mmol")
        self.assertEqual(status, "match")
        self.assertIn("0.5 mmol", detail)
        self.assertEqual(cmp("0.1", "M (0.5 mmol in 5 mL)", "0.124, 0.9", "g, mmol")[0], "mismatch")


PROCESS = [("LayerID", "StepOrder", "ProcessType", "Descriptions", "Parameter", "Unit"),
           (4, 1, "SpinCoating", None, "4000, 30", "RPM, s"),
           (4, 2, "Annealing", None, "100, 10", "C, min")]


def row(row_id, found, **values):
    return {"row_id": row_id, "found": found, "values": [{"column": k, "value": v} for k, v in values.items()],
            "page": 1, "source": "text", "evidence": None, "note": None, "key_issue": None}


def recheck(body):
    ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
    return {"devices": [{"device_id": "D1", "paper_device": "control"}],
            "rows": [row(ids[0], False), row(ids[1], True, Descriptions="Hot plate", Parameter="100, 10", Unit="C, min")]}


class Rechecked(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wb = write_workbook(self.tmp / "10.1234_test.5678_Sample1.xlsx", {"Process": PROCESS})
        self.main = write_pdf(self.tmp / "main.pdf", "doi 10.1234/test.5678 The film was annealed at 100 C for 10 min.")

    def run_recheck(self, paper):
        rt, _ = runtime({"recheck": recheck})
        return Rechecker(runtime=rt, judge=False).recheck(paper, [self.wb])

    def by(self, report):
        return {(f.excel_row, f.column): f for f in report.findings}

    def test_a_value_only_the_si_can_hold_is_flagged_needs_si(self):
        f = self.by(self.run_recheck(load_paper(self.main)))
        self.assertEqual(f[(2, "Parameter")].status, "needs_si")
        self.assertIn("101234test5678_SI.pdf", f[(2, "Parameter")].detail)
        self.assertEqual(f[(3, "Parameter")].status, "match")

    def test_rows_the_si_could_settle_carry_a_link_to_it(self):
        f = self.by(self.run_recheck(load_paper(self.main)))
        self.assertEqual((f[(2, "Parameter")].si_link, f[(2, "Parameter")].si_save_as),
                         ("https://doi.org/10.1234/test.5678", "101234test5678_SI.pdf"))
        self.assertEqual(f[(3, "Parameter")].si_link, "")  # a matched row needs no SI
        si = write_pdf(self.tmp / "si2.pdf", "Supporting information. Table S1.")
        loaded = self.by(self.run_recheck(load_paper(self.main, si=[si])))
        self.assertEqual(loaded[(2, "Parameter")].si_link, "")  # the SI is loaded: nothing to fetch

    def test_free_text_left_blank_is_not_a_finding(self):
        f = self.by(self.run_recheck(load_paper(self.main)))
        self.assertNotIn((3, "Descriptions"), f)  # the paper says "Hot plate", the record is empty on purpose

    def test_the_models_own_not_reported_is_enough(self):
        def recheck_note(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            r1 = row(ids[0], False)
            r1["note"] = "Not reported in the available pages."
            return {"devices": [{"device_id": "D1", "paper_device": "c"}], "rows": [r1, row(ids[1], False)]}

        main = write_pdf(self.tmp / "m3.pdf", "doi 10.1234/test.5678 Films were spin coated at 4000 rpm for 30 s.")
        rt, _ = runtime({"recheck": recheck_note})
        f = self.by(Rechecker(runtime=rt, judge=False).recheck(load_paper(main), [self.wb]))
        self.assertEqual(f[(2, "Parameter")].status, "needs_si")  # the numbers are in the text, but the model says it is not reported
        self.assertEqual(f[(3, "Parameter")].status, "needs_si")  # 100 and 10 are not in this text at all

    def test_with_the_si_loaded_an_unfound_value_is_just_not_found(self):
        si = write_pdf(self.tmp / "si.pdf", "Supporting information. Table S1.")
        f = self.by(self.run_recheck(load_paper(self.main, si=[si])))
        self.assertEqual(f[(2, "Parameter")].status, "not_found")

    def test_a_value_that_is_in_the_text_but_missed_stays_not_found(self):
        main = write_pdf(self.tmp / "m2.pdf", "doi 10.1234/test.5678 Films were spin coated at 4000 rpm for 30 s.")
        f = self.by(self.run_recheck(load_paper(main)))
        self.assertEqual(f[(2, "Parameter")].status, "not_found")  # the model missed it; the SI would not help


class JudgeOnce(unittest.TestCase):
    def test_the_same_row_of_two_devices_is_judged_once(self):
        tmp = Path(tempfile.mkdtemp())
        sheet = {"Process": [("LayerID", "StepOrder", "ProcessType", "Descriptions"), (1, 1, "Plasma Treat", "O2")]}
        wbs = [write_workbook(tmp / f"10.1234_test.5678_Sample{n}.xlsx", sheet) for n in (1, 2)]
        pdf = write_pdf(tmp / "p.pdf", "doi 10.1234/test.5678 Oxygen plasma treatment was applied.")

        def recheck(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            return {"devices": [{"device_id": "D1", "paper_device": "c"}, {"device_id": "D2", "paper_device": "t"}],
                    "rows": [row(i, True, Descriptions="Oxygen plasma treatment") for i in ids]}

        def judge(body):
            ids = re.findall(r'"id":\s*"(\d+)"', last_user_text(body))
            self.asked = ids
            return {"verdicts": [{"id": i, "same": True, "reason": "O2 plasma is oxygen plasma"} for i in ids]}

        rt, _ = runtime({"recheck": recheck, "judge": judge})
        report = Rechecker(runtime=rt, judge=True).recheck(pdf, wbs)
        self.assertEqual(len(self.asked), 1)  # one question for both devices
        self.assertEqual([f.status for f in report.findings], ["match", "match"])


if __name__ == "__main__":
    unittest.main()
