"""End to end against a fake OpenRouter (httpx.MockTransport): prompt -> answer -> comparison -> judge."""
import json
import re
import tempfile
import unittest
from pathlib import Path

import httpx
from _fixtures import LAYER, NOTES, PROCESS, PROPERTIES, STABILITY, write_pdf, write_workbook

from psc_agent import OpenRouterClient, Rechecker, recheck_folders

KEY = "sk-or-test-key"

# what the fake model "reads" in the paper, per row id (D1 = Sample1, D2 = Sample2)
PAPER = {
    "D1:Layer:2": dict(found=True, values={"Thickness (nm)": None}),
    "D1:Layer:3": dict(found=True, values={"Thickness (nm)": "25"}, page=2, source="text", evidence="25 nm NiOx"),
    "D1:Layer:4": dict(found=False, values={"Thickness (nm)": None}),
    "D1:Process:2": dict(found=True, values={"Descriptions": "deionized water and isopropanol", "Parameter": "20",
                                             "Unit": "min"}, page=2, source="text"),
    "D1:Process:3": dict(found=True, values={"Descriptions": None, "Parameter": "40, 3000", "Unit": "s, rpm"}),
    "D1:Properties:2": dict(found=True, values={"Values": "22.9", "Errors": None, "Unit": "%"}, page=1,
                            source="figure", evidence="Fig. 4b"),
    "D1:Properties:3": dict(found=True, values={"Values": "1.10", "Errors": None, "Unit": "V"}, page=1,
                            source="table", evidence="Table 1"),
    "D1:Properties:4": dict(found=True, values={"Values": "24.01", "Errors": None, "Unit": "mA/cm2"}),
    # D1:Properties:5 (Champion FF) deliberately missing from the answer
    "D1:Properties:6": dict(found=True, values={"Values": "21.5", "Errors": "0.4", "Unit": "%"}),
    "D1:Stability:2": dict(found=True, values={"Encapsulation": "N", "Temperature (°C)": "25", "Humidity (%)": "50-60",
                                               "Time (hr)": "1000", "Retention (%)": "85"}),
    "D2:Layer:2": dict(found=True, values={"Thickness (nm)": None}, key_issue="paper uses FTO, not ITO"),
    "D2:Layer:3": dict(found=True, values={"Thickness (nm)": "20"}),
    "D2:Layer:4": dict(found=False, values={"Thickness (nm)": None}),
}


class FakeOpenRouter:
    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        self.requests.append(body)
        name = body["response_format"]["json_schema"]["name"]
        if name == "recheck_rows":
            task = body["messages"][1]["content"][-1]["text"]
            if 'SHEET "Notes"' in task:
                return httpx.Response(400, json={"error": {"code": 400, "message": "bad request"}})
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", task, re.M)
            rows = [dict(row_id=i, found=PAPER[i]["found"],
                         values=[{"column": c, "value": v} for c, v in PAPER[i]["values"].items()],
                         page=PAPER[i].get("page"), source=PAPER[i].get("source", "text"),
                         evidence=PAPER[i].get("evidence"), note=None, key_issue=PAPER[i].get("key_issue"))
                    for i in ids if i in PAPER]
            content = {"devices": [{"device_id": "D1", "paper_device": "control"},
                                   {"device_id": "D2", "paper_device": "FTO device"}], "rows": rows}
        else:  # judge: everything it is asked about is the same
            items = [json.loads(line) for line in body["messages"][1]["content"].splitlines()[1:]]
            content = {"verdicts": [{"id": i["id"], "same": True, "reason": "synonyms"} for i in items]}
        return httpx.Response(200, json={
            "model": body["model"], "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.01}})


class RecheckEndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.tmp = tmp
        (tmp / "raw").mkdir(), (tmp / "pdf").mkdir()
        self.wb1 = write_workbook(tmp / "raw" / "10.1016_j.test.2020.123_Sample1.xlsx",
                                  {"Layer": LAYER, "Process": PROCESS, "Properties": PROPERTIES, "Stability": STABILITY,
                                   "Notes": NOTES})
        self.wb2 = write_workbook(tmp / "raw" / "10.1016_j.test.2020.123_Sample2.xlsx", {"Layer": LAYER})
        self.pdf = write_pdf(tmp / "pdf" / "paper.pdf", "https://doi.org/10.1016/j.test.2020.123")
        self.fake = FakeOpenRouter()

    def rechecker(self, cache_dir=None):
        client = OpenRouterClient(KEY, transport=httpx.MockTransport(self.fake), max_retries=0, cache_dir=cache_dir)
        return Rechecker("test/model", client=client)

    def test_findings(self):
        report = self.rechecker().recheck(self.pdf, [self.wb2, self.wb1])  # order is normalised by sample no.
        got = {(f.device_ref, f.sheet, f.excel_row, f.column): f for f in report.findings}
        st = {k: f.status for k, f in got.items()}

        self.assertEqual(st[("D1", "Layer", 3, "Thickness (nm)")], "mismatch")  # 20 recorded, 25 in paper
        self.assertEqual(got[("D1", "Layer", 3, "Thickness (nm)")].evidence, "25 nm NiOx")
        self.assertNotIn(("D1", "Layer", 4, "Thickness (nm)"), st)  # empty placeholder row, absent in paper
        self.assertEqual(st[("D1", "Process", 2, "Descriptions")], "match")  # settled by the judge
        self.assertIn("judge: synonyms", got[("D1", "Process", 2, "Descriptions")].detail)
        self.assertEqual(st[("D1", "Process", 2, "Parameter")], "match")
        self.assertEqual(st[("D1", "Process", 3, "Parameter")], "match")  # reordered values + units
        self.assertEqual(st[("D1", "Properties", 2, "Values")], "match")  # figure tolerance
        self.assertEqual(st[("D1", "Properties", 3, "Values")], "mismatch")
        self.assertEqual(st[("D1", "Properties", 4, "Values")], "match")
        self.assertEqual(st[("D1", "Properties", 5, "Values")], "not_checked")
        self.assertEqual(st[("D1", "Properties", 6, "Values")], "missing_in_record")
        self.assertEqual(st[("D1", "Properties", 6, "Errors")], "missing_in_record")
        self.assertEqual(st[("D2", "Layer", 2, "(row keys)")], "review")
        stability = [s for k, s in st.items() if k[1] == "Stability"]
        self.assertEqual(stability, ["match"] * 5)
        # the Notes call failed: its recorded values are reported as not checked, with the error
        notes = [f for f in report.findings if f.sheet == "Notes"]
        self.assertTrue(notes and all(f.status == "not_checked" and "HTTP 400" in f.detail for f in notes))
        self.assertEqual(len(report.errors), 1)

        self.assertEqual([d.paper_device for d in report.devices], ["control", "FTO device"])
        self.assertEqual(report.usage.calls, 4)  # Layer, Process, Properties+Stability, judge (Notes failed)
        self.assertAlmostEqual(report.usage.cost_usd, 0.04)
        self.assertNotIn("judgeable", report.model_dump_json())

        first = self.fake.requests[0]
        parts = first["messages"][1]["content"]
        self.assertEqual(sum(p["type"] == "image_url" for p in parts), 1)  # one figure page
        self.assertIn("cache_control", parts[-2])
        self.assertTrue(first["response_format"]["json_schema"]["strict"])
        self.assertEqual(first["provider"], {"require_parameters": True})
        # recorded values never reach the model; only key columns do
        line = re.search(r"^D1:Layer:3 \|.*$", parts[-1]["text"], re.M).group(0)
        self.assertEqual(line, "D1:Layer:3 | LayerID=2 | LayerType=HTL | Material=NiOx")

    def test_cache_and_batch(self):
        out = self.tmp / "out"
        rechecker = self.rechecker(cache_dir=self.tmp / "cache")
        reports = recheck_folders(rechecker, self.tmp / "raw", self.tmp / "pdf", out)
        self.assertEqual(len(reports), 1)
        n = len(self.fake.requests)
        self.assertTrue((out / "papers" / "101016jtest2020123.json").exists())
        csv_text = (out / "findings.csv").read_text(encoding="utf-8-sig")
        self.assertTrue(csv_text.startswith("paper,doi,device,device_ref,sheet,excel_row,column"))
        self.assertLess(csv_text.index(",mismatch,"), csv_text.index(",match,"))  # worst first

        self.assertEqual(recheck_folders(rechecker, self.tmp / "raw", self.tmp / "pdf", out), [])  # already done
        again = recheck_folders(rechecker, self.tmp / "raw", self.tmp / "pdf", out, force=True)[0]
        self.assertEqual(len(self.fake.requests), n + 1)  # only the failed (uncached) call is retried
        self.assertEqual(again.usage.cost_usd, 0)
        self.assertEqual(again.usage.cached_calls, 4)


if __name__ == "__main__":
    unittest.main()
