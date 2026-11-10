"""``psc-agent loop``: extract, fill what failed, a second reader, final status, gate, staging, calibration."""
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf

from psc_agent import Extractor, Rechecker, write_workbook
from psc_agent.autoloop import audit_sample, calibrate, gate_status, run_loop
from psc_agent.core.evidence import quote_on_page
from psc_agent.extract.verify import Verifier
from psc_agent.runner import Item, collect
from psc_agent.sources.workbook import load_workbook

PAGE = ("Methods. The NiOx film is 20 nm thick. The device reached a PCE of 20.1 % with a trap density of "
        "2.70 x 10 18 cm 3 in the control. ITO was the anode.")

# what the extractor says: (sheet, cells, source, evidence)
ROWS = [
    ("Layer", {"LayerID": "1", "LayerType": "Anode", "Material": "ITO", "Thickness (nm)": "150"}, "text", "ITO was the anode."),
    ("Layer", {"LayerID": "2", "LayerType": "HTL", "Material": "NiOx", "Thickness (nm)": "20"}, "text", "The NiOx film is 20 nm thick."),
    ("Properties", {"Properties": "PCE", "Values": "20.1", "Unit": "%"}, "text", "The device reached a PCE of 20.1 % with a trap"),
    ("Properties", {"Properties": "VOC", "Values": "1.10", "Unit": "V"}, "figure", "Figure 3"),
]


def xrow(sheet, cells, source, evidence, page=1):
    return {"device": "S1", "sheet": sheet, "values": [{"column": k, "value": v} for k, v in cells.items()], "page": page,
            "source": source, "evidence": evidence}


def mapper(body):
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "d"}],
            "rows": [xrow(*ROWS[0]), xrow(*ROWS[1])]}


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdfs, self.raw, self.out = self.tmp / "pdf", self.tmp / "raw", self.tmp / "out"
        self.pdfs.mkdir(), self.raw.mkdir()
        write_pdf(self.pdfs / "new paper.pdf", "doi: 10.1016/j.test.2024.1 " + PAGE)
        self.rows_call = 0
        self.second = {}  # (sheet, key) -> the values the second reader reads (default: the extractor's)
        self.fail_rows_once = False
        self.rows_failures = 0

    def extract_rows(self, body):
        self.rows_call += 1
        if self.fail_rows_once and "Properties" in last_user_text(body) and self.rows_failures == 0:
            self.rows_failures += 1
            raise ValueError("HTTP 503")
        return {"rows": [xrow(*r) for r in ROWS[2:] if r[0] in last_user_text(body)]}

    def second_reader(self, body):
        """The recheck answer: reads each listed row back from SECOND (the extractor's value unless told otherwise)."""
        text = last_user_text(body)
        rows = []
        for rid, sheet, key in re.findall(r"^(D\d+:(\w+):\d+) \| (.*)$", text, re.M):
            ident = key.split(" | ")[0].split("=")[1] if "=" in key else key
            got = self.second.get((sheet, ident))
            if got is None:  # unless told otherwise the second reader agrees with the paper text
                src = {"1": ROWS[0][1], "2": ROWS[1][1], "PCE": ROWS[2][1], "VOC": ROWS[3][1]}[ident]
                got = {k: v for k, v in src.items() if k not in ("LayerID", "LayerType", "Material", "Properties")}
            if got == "missing":
                rows.append({"row_id": rid, "found": False, "values": [], "page": 1, "source": "text", "evidence": None,
                             "note": None, "key_issue": None})
                continue
            rows.append({"row_id": rid, "found": True, "values": [{"column": c, "value": v} for c, v in got.items()],
                         "page": 1, "source": "text", "evidence": None, "note": None, "key_issue": None})
        return {"devices": [{"device_id": "D1", "paper_device": "control"}], "rows": rows}

    def pipeline(self, extract_extra=None, verify_extra=None):
        ext_rt, self.ext_fake = runtime({"mapper": mapper, "extract": self.extract_rows,
                                         "decision": lambda b: {"decisions": []}, **(extract_extra or {})},
                                        models={"default": "m/extract", "decision": "m/small"})
        ver_rt, self.ver_fake = runtime({"recheck": self.second_reader, "judge": lambda b: {"verdicts": []}, **(verify_extra or {})},
                                        models={"default": "m/verifier"})
        self.extractor = Extractor(ext_rt, decide_rows=False)
        self.verifier = Verifier(Rechecker(runtime=ver_rt, judge=False))
        self.kw = dict(finder=None, reader=None, extractor=self.extractor, verifier=self.verifier, out=self.out)

    def rows(self, name="provenance.csv"):
        with open(self.out / name, encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    def item(self):
        return collect(self.raw, self.pdfs, mode="extract")


class QuoteCheck(unittest.TestCase):
    def test_superscripts_and_spacing_do_not_fail_a_quote_that_is_on_the_page(self):
        pages = ["a trap density of 2.70  1018 cm3 in the control film, which is lower"]
        self.assertTrue(quote_on_page(pages, 1, "trap density of 2.70   10 18 cm 3 in the control film"))
        self.assertFalse(quote_on_page(pages, 1, "trap density of 9.99 x 10 20 cm 3 in the control film"))  # another number


class Loop(Setup):
    def test_rows_both_readers_agree_on_are_accepted_and_staged_only_through_an_open_gate(self):
        self.pipeline()
        cal = self.tmp / "calibration.json"
        loops, open_, why = run_loop(self.item(), calibration=cal, **self.kw)
        (lp,) = loops
        self.assertEqual((len(lp.records), lp.accepted, lp.held), (4, 4, 0))
        self.assertFalse(open_)  # no calibration: nothing is staged
        self.assertIn("no calibration", why)
        self.assertFalse((self.out / "staged").exists())
        self.assertTrue(list((self.out / "papers").glob("*/accepted/*.xlsx")))  # the accepted drafts wait for the gate
        prov = {r["row_id"]: r for r in self.rows()}
        self.assertEqual({r["extractor_model"] for r in prov.values()}, {"m/extract"})
        self.assertEqual({r["verifier_model"] for r in prov.values()}, {"m/verifier"})
        self.assertEqual({r["confidence"] for r in prov.values() if r["source"] == "figure"}, {"figure"})  # agreed, still an estimate
        self.assertIn("CLOSED", (self.out / "REPORT.md").read_text(encoding="utf-8"))

    def test_a_disputed_or_unfound_row_is_held_with_the_reason_and_left_out_of_the_accepted_workbook(self):
        self.second = {("Properties", "PCE"): {"Values": "19.5", "Unit": "%"}, ("Layer", "1"): "missing"}
        self.pipeline()
        (lp,), *_ = run_loop(self.item(), calibration=self.tmp / "none.json", **self.kw)
        held = {r["row_id"]: r for r in self.rows("held.csv")}
        self.assertEqual(len(held), 2)
        why = " | ".join(r["why"] for r in held.values())
        self.assertIn("disputed", why)
        self.assertIn("19.5", why)  # what the second reader read
        self.assertIn("unverified", why)
        accepted = [load_workbook(f) for f in lp.accepted_files]
        sheets = {s: rows for d in accepted for s, rows in d.sheets.items()}
        self.assertEqual([r.cells.get("Material") for r in sheets["Layer"]], ["NiOx"])  # ITO (unverified) is not in it
        self.assertNotIn("PCE", [r.cells.get("Properties") for r in sheets["Properties"]])

    def test_a_failed_sheet_group_is_asked_again(self):
        self.fail_rows_once = True
        self.pipeline()
        loops, *_ = run_loop(self.item(), calibration=self.tmp / "none.json", rounds=2, **self.kw)
        (lp,) = loops
        self.assertEqual(lp.rounds, 1)
        self.assertEqual(lp.errors, [])
        self.assertEqual(len(lp.records), 4)  # the Properties rows arrived on the second ask
        self.assertEqual(lp.accepted, 4)

    def test_without_retries_the_failure_stays_and_is_reported(self):
        self.fail_rows_once = True
        self.pipeline()
        (lp,), *_ = run_loop(self.item(), calibration=self.tmp / "none.json", rounds=0, **self.kw)
        self.assertTrue(any("Properties" in e for e in lp.errors))

    def test_a_paper_that_fails_does_not_stop_the_others(self):
        write_pdf(self.pdfs / "second.pdf", "doi: 10.1016/j.test.2024.2 " + PAGE)

        def boom(body):
            raise RuntimeError("model fell over")

        self.pipeline(extract_extra={"mapper": lambda b: boom(b) if "test.2024.2" in last_user_text(b) else mapper(b)})
        loops, *_ = run_loop(self.item(), calibration=self.tmp / "none.json", **self.kw)
        self.assertEqual(len(loops), 2)
        bad, good = sorted(loops, key=lambda lp: not lp.errors)
        self.assertTrue(bad.errors and not bad.records)
        self.assertEqual((good.errors, good.accepted), ([], 4))


class Gate(Setup):
    def cal(self, precision, found=150, extract="m/extract", verifier="m/verifier"):
        path = self.tmp / "calibration.json"
        path.write_text(json.dumps({"date": "2026-11-10", "extract_model": extract, "verifier_model": verifier,
                                    "accepted": {"accuracy_when_found": precision, "found": found}}), encoding="utf-8")
        return path

    def test_the_gate_opens_only_for_enough_precision_from_the_same_models(self):
        self.assertTrue(gate_status(self.cal(0.95), 0.9, "m/extract", "m/verifier")[0])
        self.assertIn("below", gate_status(self.cal(0.8), 0.9, "m/extract", "m/verifier")[1])
        self.assertIn("at least 100", gate_status(self.cal(0.99, found=30), 0.9, "m/extract", "m/verifier")[1])
        self.assertIn("calibrate again", gate_status(self.cal(0.99), 0.9, "other/model", "m/verifier")[1])
        self.assertIn("no calibration", gate_status(self.tmp / "missing.json", 0.9, "m/extract", "m/verifier")[1])

    def test_an_open_gate_stages_the_accepted_workbooks(self):
        self.pipeline()
        loops, open_, _ = run_loop(self.item(), calibration=self.cal(0.97), **self.kw)
        self.assertTrue(open_)
        staged = sorted((self.out / "staged").glob("*.xlsx"))
        self.assertEqual([p.name for p in staged], ["10.1016_j.test.2024.1_Sample1.xlsx"])
        self.assertEqual(sum(len(r) for r in load_workbook(staged[0]).sheets.values()), 4)

    def test_the_audit_sample_is_a_reproducible_share_of_the_accepted_rows(self):
        recs = [{"final": "accepted", "n": i} for i in range(100)] + [{"final": "held", "n": 0}] * 10
        a, b = audit_sample(recs, 0.05, seed=3), audit_sample(recs, 0.05, seed=3)
        self.assertEqual((len(a), a), (5, b))
        self.assertTrue(all(r["final"] == "accepted" for r in a))
        self.assertEqual(len(audit_sample(recs[:3], 0.05)), 3)  # never more than there are


class Calibrate(Setup):
    def test_calibration_measures_the_extractor_alone_and_what_both_readers_accepted_against_the_record(self):
        # the hand-entered workbook says PCE 18.0 (the extractor said 20.1) and the second reader disagrees with the extractor
        write_workbook({"Layer": [{"LayerID": 1, "LayerType": "Anode", "Material": "ITO", "Thickness (nm)": "150"},
                                  {"LayerID": 2, "LayerType": "HTL", "Material": "NiOx", "Thickness (nm)": "20"}],
                        "Properties": [{"Properties": "PCE", "Values": "18.0", "Unit": "%"}]}, self.raw / "10.1016_j.test.2024.1_Sample1.xlsx")
        self.second = {("Properties", "PCE"): {"Values": "18.0", "Unit": "%"}}  # the second reader read the paper differently
        self.pipeline()
        items = collect(self.raw, self.pdfs, mode="recheck")
        self.assertEqual(len(items), 1)
        cal = calibrate(items, calibration=self.tmp / "cal.json", **self.kw)
        self.assertEqual((cal["extract_model"], cal["verifier_model"]), ("m/extract", "m/verifier"))
        self.assertGreaterEqual(cal["all_rows"]["match"], 2)  # the layers
        self.assertGreaterEqual(cal["all_rows"]["mismatch"], 1)  # the extractor's PCE is wrong
        self.assertEqual(cal["accepted"]["mismatch"], 0)  # the disputed PCE row was held, not staged
        self.assertEqual(json.loads((self.tmp / "cal.json").read_text(encoding="utf-8"))["papers"], 1)


if __name__ == "__main__":
    unittest.main()
