"""Scoring an extraction against the hand-entered workbooks: normalisation, alignment, verdicts, report, end to end."""
import json
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf

from psc_agent import Extractor, load_workbook, write_workbook
from psc_agent.evaluate import Auditor, compare_devices, evaluate_pairs, load_eval, stats, summarize, write_report
from psc_agent.evaluate.text import (composition, formula_status, likely_in_paper, paper_text, process_key, property_key,
                                     solvent_status, text_status)
from psc_agent.sources.pairing import PaperPair


def device(tmp: Path, name: str, sheets: dict):
    return load_workbook(write_workbook(sheets, tmp / f"{name}.xlsx"))


def record_s2():
    return {
        "Layer": [{"LayerID": 1, "LayerType": "Anode", "Material": "ITO"}, {"LayerID": 2, "LayerType": "HTL", "Material": "NiOx", "Thickness (nm)": 20},
                  {"LayerID": 3, "LayerType": "HTL treatment", "Material": "PEAI"},
                  {"LayerID": 4, "LayerType": "PSK", "Material": "Cs0.05MA0.10FA0.85Pb(I0.97Br0.03)3"}],
        "Process": [{"LayerID": 2, "StepOrder": 1, "ProcessType": "Annealing", "Parameter": "300, 60", "Unit": "C, min"},
                    {"LayerID": 4, "StepOrder": 1, "ProcessType": "SpinCoating", "Parameter": "3000, 30", "Unit": "RPM, s"}],
        "Properties": [{"Properties": "Champion PCE", "Values": 22.5, "Unit": "%"},
                       {"Properties": "Avg PCE", "Values": 21.8, "Errors": 0.4, "Unit": "%"},
                       {"Properties": "Champion Jsc", "Values": 24.1, "Unit": "mA cm-2"}],
        "Stability": [{"Environment": "Ambient air storage", "Encapsulation": "N", "Time (hr)": 1000, "Retention (%)": 85}],
        "Materials": [{"LayerID": 3, "Material": "PEAI", "Type": "Solution", "Solvent": "IPA", "Amount": 1, "Unit": "mg/mL"}],
        "Vender": [{"Material": "PEAI", "Vender": "Greatcellsolar"}],
    }


def record_s1():
    s = record_s2()
    s["Layer"] = [r for r in s["Layer"] if r["LayerID"] != 3]
    s["Properties"] = [{"Properties": "Champion PCE", "Values": 20.1, "Unit": "%"}]
    s["Stability"] = []
    s["Materials"], s["Vender"] = [], []
    return s


class Normalise(unittest.TestCase):
    def test_property_names(self):
        self.assertEqual(property_key("Avg PCE"), property_key("Average  PCE"))
        self.assertEqual(property_key("Champion Voc (RS)"), ("voc", "champion", "rs"))
        self.assertEqual(property_key("Voc"), ("voc", "champion", ""))  # a bare metric is the champion value
        self.assertEqual(property_key("Roughness RMS")[0], property_key("AFM rms")[0])
        self.assertEqual(process_key("Spin coating"), process_key("SpinCoating"))
        self.assertEqual(process_key("Plasma Treatment"), process_key("Plasma Treat"))

    def test_formulas_compare_by_composition(self):
        a, b = "Cs0.05MA0.10FA0.85Pb(I0.97Br0.03)3", "Cs0.05FA0.85MA0.1PbI2.91Br0.09"
        self.assertEqual(formula_status(a, b), "match")  # another order and style, same composition
        self.assertEqual(formula_status(a, "Cs0.05FA0.85MA0.1PbI3"), "mismatch")  # no bromide
        self.assertIsNone(formula_status("NiOx", "NiOx"))
        a_site, x_site = composition("Cs0.05(FA0.83MA0.12)0.95Pb(I0.83Br0.17)3")
        self.assertAlmostEqual(x_site["Br"], 0.17, places=2)
        self.assertAlmostEqual(a_site["Cs"] / a_site["FA"], 0.05 / (0.83 * 0.95), places=2)

    def test_text_and_solvents(self):
        self.assertEqual(text_status("Me-4PACz SAM", "Me-4PACz"), "match")
        self.assertEqual(text_status("Sigma-Aldrich", "Sigma Aldrich"), "match")
        self.assertEqual(text_status("NiOx", "TiO2"), "mismatch")
        self.assertEqual(solvent_status("DMF:DMSO (4:1 v/v)", "DMF/DMSO 4:1"), "match")
        self.assertEqual(solvent_status("DMF:DMSO (4:1 v/v)", "DMF:DMSO (3:1)"), "mismatch")
        self.assertEqual(solvent_status("DMF:DMSO", "DMF"), "review")

    def test_is_the_value_in_the_paper(self):
        text = paper_text(["The champion PCE was 22.5% with Voc of 1.12 V.", "Films were annealed at 3,000 rpm."])
        self.assertTrue(likely_in_paper("22.5", text))
        self.assertTrue(likely_in_paper("3000", text))  # thousands separator in the PDF
        self.assertFalse(likely_in_paper("22.51", text))
        self.assertFalse(likely_in_paper("Colorado Concept Coatings", text))


class Compare(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.gt = [device(self.tmp, "gt_Sample1", record_s1()), device(self.tmp, "gt_Sample2", record_s2())]

    def by(self, cells, sheet, row, column, device="gt_Sample2"):
        return next(c for c in cells if (c.device, c.sheet, c.row, c.column) == (device, sheet, row, column))

    def test_identical_data_matches_everywhere_even_when_the_device_order_differs(self):
        cells, pairing = compare_devices(self.gt, list(reversed(self.gt)))
        self.assertEqual({c.status for c in cells}, {"match"})
        self.assertEqual([(p["record"], p["extracted"]) for p in pairing], [("gt_Sample1", "gt_Sample1"), ("gt_Sample2", "gt_Sample2")])

    def test_each_kind_of_difference_gets_its_own_verdict(self):
        s = record_s2()
        s["Layer"][3]["Material"] = "Cs0.05FA0.85MA0.1PbI2.91Br0.09"  # same perovskite, written differently
        s["Layer"][2]["Material"] = "PEAI SAM"  # extra word
        s["Properties"][0]["Values"] = 22.9  # wrong number
        s["Properties"][1] = {"Properties": "Average PCE", "Values": 21.8, "Errors": 0.4, "Unit": "%"}  # same property, other name
        s["Properties"][2] = {"Properties": "Champion Jsc", "Values": 0.0241, "Unit": "A cm-2"}  # same number, other unit
        s["Process"].pop()  # the spin step is not extracted
        s["Stability"] = []  # no stability given
        s["Properties"].append({"Properties": "Trap Density", "Values": 1e16, "Unit": "cm-3"})  # not in the record
        ex = [self.gt[0], device(self.tmp, "ex_S2", s)]
        cells, _ = compare_devices(self.gt, ex)
        status = lambda sheet, row, col: self.by(cells, sheet, row, col).status  # noqa: E731
        self.assertEqual(status("Layer", "slot 4 PSK", "Material"), "match")
        self.assertEqual(status("Layer", "slot 3 HTL treatment", "Material"), "match")
        self.assertEqual(status("Properties", "Champion PCE", "Values"), "mismatch")
        self.assertEqual(status("Properties", "Avg PCE", "Values"), "match")
        self.assertEqual(status("Properties", "Avg PCE", "Errors"), "match")
        self.assertEqual(status("Properties", "Champion Jsc", "Values"), "match")
        self.assertEqual(status("Process", "L4 SpinCoating", "ProcessType"), "not_found")
        self.assertEqual(status("Process", "L4 SpinCoating", "Parameter"), "not_found")
        self.assertEqual(status("Stability", "Ambient air storage", "Retention (%)"), "not_found")
        self.assertEqual(status("Process", "L2 Annealing", "Parameter"), "match")
        self.assertEqual([c.row for c in cells if c.status == "extra" and c.kind == "row"], ["Trap Density"])
        self.assertTrue(self.by(cells, "Properties", "Champion PCE", "Values").core)
        self.assertFalse(self.by(cells, "Properties", "Avg PCE", "Values").core)

    def test_a_device_the_model_missed_is_all_not_found(self):
        cells, pairing = compare_devices(self.gt, [self.gt[0]])
        missed = [c for c in cells if c.device == "gt_Sample2"]
        self.assertTrue(missed and {c.status for c in missed} == {"not_found"})
        self.assertEqual(len([p for p in pairing if p["extracted"] is None]), 1)

    def test_a_stability_header_variant_still_lines_up(self):
        s = record_s2()
        s["Stability"] = [{"Environment": "ambient", "Encapsulation": "N", "Time (hr)": 1000, "Retention (%)": 85}]
        variant = load_workbook(write_workbook(s, self.tmp / "v.xlsx"))
        for r in variant.sheets["Stability"]:  # curators sometimes lower-cased and renamed the headers
            r.cells = {{"Time (hr)": "time (hr)", "Retention (%)": "Normalize PCE (%)"}.get(k, k): v for k, v in r.cells.items()}
        cells, _ = compare_devices([variant], [self.gt[1]])
        self.assertEqual(self.by(cells, "Stability", "ambient", "Retention (%)", device="v").status, "match")

    def test_stats(self):
        cells, _ = compare_devices(self.gt, [self.gt[0]])
        v = stats([c for c in cells if c.kind == "value"])
        self.assertEqual(v["n"], v["match"] + v["not_found"])
        self.assertAlmostEqual(v["match_rate"], v["match"] / v["n"])
        self.assertEqual(v["found"], v["match"])
        self.assertEqual(v["accuracy_when_found"], 1.0)


PAGE1 = ("doi: 10.1234/test.5678\nDevice fabrication. NiOx films were annealed at 300 C for 60 min in air. The control "
         "device used bare NiOx; the target device used a PEAI interlayer on NiOx.")
PAGE2 = "Results. Table 1 lists the champion PCE of 20.1% for the control and 22.5% for the target device."


def row(device, sheet, page, source, evidence, **cols):
    return {"device": device, "sheet": sheet, "page": page, "source": source, "evidence": evidence, "note": None,
            "values": [{"column": k, "value": v} for k, v in cols.items()]}


def fake_mapper(body):
    quote = "the target device used a PEAI interlayer on NiOx"
    layer = lambda dev, lid, typ, mat: row(dev, "Layer", 1, "text", quote, LayerID=lid, LayerType=typ, Material=mat)  # noqa: E731
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "bare NiOx"},
                        {"sample": "S2", "paper_name": "target", "description": "PEAI interlayer"}],
            "rows": [layer("S1", "1", "Anode", "ITO"), layer("S1", "2", "HTL", "NiOx"), layer("S2", "1", "Anode", "ITO"),
                     layer("S2", "2", "HTL", "NiOx"), layer("S2", "3", "HTL treatment", "PEAI")]}


def fake_extractor(body):
    task = last_user_text(body)
    if 'SHEET "Properties"' in task:
        pce = lambda dev, v: row(dev, "Properties", 2, "table", "Table 1", Properties="Champion PCE", Values=v, Unit="%")  # noqa: E731
        return {"rows": [pce("S1", "20.1"), pce("S2", "23.9")]}  # S2 is wrong on purpose
    return {"rows": []}


class EndToEnd(unittest.TestCase):
    def test_extract_score_report_and_resume(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        record = []
        for n, sheets in enumerate((record_s1(), record_s2()), 1):
            sheets["Stability"] = []
            sheets["Process"] = [r for r in sheets["Process"] if r["LayerID"] == 2]
            record.append(write_workbook(sheets, tmp / f"10.1234_test.5678_Sample{n}.xlsx"))
        rt, fake = runtime({"mapper": fake_mapper, "extract": fake_extractor})
        pair = PaperPair("101234test5678", pdf, record)
        evals = evaluate_pairs(Extractor(rt, decide_rows=False), [pair], tmp / "out")
        ev = evals[0]
        self.assertEqual(ev.errors, [])
        self.assertEqual(len(fake.by_role("decision")), 0)  # scoring needs no decision model
        by = {(c.device, c.sheet, c.row, c.column): c for c in ev.cells}
        self.assertEqual(by[("10.1234_test.5678_Sample1", "Properties", "Champion PCE", "Values")].status, "match")
        self.assertEqual(by[("10.1234_test.5678_Sample2", "Properties", "Champion PCE", "Values")].status, "mismatch")
        self.assertEqual(by[("10.1234_test.5678_Sample2", "Layer", "slot 3 HTL treatment", "Material")].status, "match")
        self.assertEqual(by[("10.1234_test.5678_Sample1", "Process", "L2 Annealing", "ProcessType")].status, "not_found")
        self.assertTrue(by[("10.1234_test.5678_Sample1", "Process", "L2 Annealing", "Parameter")].likely)  # 300 and 60 are in the text
        self.assertFalse(by[("10.1234_test.5678_Sample2", "Properties", "Avg PCE", "Values")].likely)  # 21.8 is not in the text
        self.assertIsNotNone(load_eval(tmp / "out" / pair.key))

        calls = len(fake.requests)
        again = evaluate_pairs(Extractor(rt, decide_rows=False), [pair], tmp / "out")  # finished papers are not redone
        self.assertEqual(len(fake.requests), calls)
        self.assertEqual(len(again[0].cells), len(ev.cells))

        s = summarize(evals)
        self.assertEqual(s["papers"], 1)
        report = write_report(evals, tmp / "out", {"provider": "fake", "model": "big/model"}).read_text(encoding="utf-8")
        self.assertIn("## Headline", report)
        self.assertIn("Champion PCE".upper().split()[0], report.upper())
        self.assertTrue((tmp / "out" / "cells_all.csv").exists())
        rows = (tmp / "out" / "cells_all.csv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(rows[0].split(",")[:3], ["paper", "device", "ex_device"])
        self.assertEqual(json.loads((tmp / "out" / pair.key / "eval.json").read_text(encoding="utf-8"))["key"], pair.key)


def fake_auditor(body):
    task = last_user_text(body)
    import re
    item_ids = re.findall(r"\[(item_\d+)\]", task)
    audits = []
    for iid in item_ids:
        audits.append({"id": iid, "verdict": "extractor_correct", "resolved_value": "23.9", "resolved_unit": "%",
                       "evidence": "champion PCE of 23.9% was achieved", "page": 2,
                       "explanation": "Paper states 23.9% for the target device; human record had old value 22.5%."})
    return {"audits": audits}


class AuditorWorkflow(unittest.TestCase):
    def test_audit_discrepancies(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "p.pdf", PAGE1, page_texts=[PAGE2 + " In addition, champion PCE of 23.9% was achieved."])
        record = [write_workbook(record_s1(), tmp / "gt_S1.xlsx"),
                  write_workbook(record_s2(), tmp / "gt_S2.xlsx")]
        rt, fake = runtime({"mapper": fake_mapper, "extract": fake_extractor, "auditor": fake_auditor})
        pair = PaperPair("101234test5678", pdf, record)
        auditor = Auditor(rt)
        evals = evaluate_pairs(Extractor(rt, decide_rows=False), [pair], tmp / "out", auditor=auditor, force=True)
        ev = evals[0]
        self.assertEqual(ev.errors, [])
        pce = next(c for c in ev.cells if c.sheet == "Properties" and c.row == "Champion PCE" and c.ex == "23.9")
        self.assertEqual(pce.status, "mismatch")
        self.assertEqual(pce.audit_verdict, "extractor_correct")
        self.assertEqual(pce.audit_resolved, "23.9 %")
        self.assertIn("p.2 champion PCE of 23.9%", pce.audit_evidence)
        self.assertIn("Paper states 23.9%", pce.audit_explanation)

        report = write_report(evals, tmp / "out").read_text(encoding="utf-8")
        self.assertIn("## Auditor Discrepancy Resolution", report)
        self.assertIn("Adjusted Match Rate", report)
        self.assertIn("extractor_correct", report)


if __name__ == "__main__":
    unittest.main()
