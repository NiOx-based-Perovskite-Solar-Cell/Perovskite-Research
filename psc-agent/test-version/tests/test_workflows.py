"""End to end with a fake model per role: analyze, extract, learn, and the recheck quote checkpoint."""
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime, tool_call
from _fixtures import write_pdf, write_workbook
from test_tools import write_data

from psc_agent import DataMapHook, Extractor, QuoteCheckHook, Rechecker, SkillBook, TraceHook, load_workbook
from psc_agent.analyze import Analyst, DataTools
from psc_agent.core.learn import digest, learn, load, render


def first_user(body) -> str:
    return next(m["content"] for m in body["messages"] if m["role"] == "user")


def n_tool_results(body) -> int:
    return sum(m["role"] == "tool" for m in body["messages"])


def planner(body):
    return {"notes": "coverage is thin", "questions": [
        {"id": "q1", "question": "Does HTL modification raise PCE?", "criterion": "per-paper dPCE with a sign test",
         "tools": ["paper_deltas"]},
        {"id": "q2", "question": "Is dFF linked to dJsc?", "criterion": "Spearman over papers", "tools": []}]}


def analyst(body):
    q, tools_seen = first_user(body), n_tool_results(body)
    if q.startswith("AC-1"):
        if tools_seen == 0:
            return {"tool_calls": [tool_call("paper_deltas", {"metrics": ["pce"]})]}
        return json.dumps({"claim": "PCE rises in 1 of 2 papers, median +1.25", "numbers": {"median_dpce": 1.25},
                           "evidence_calls": ["T1"], "caveats": ["2 papers only"]})
    if "decision model said" not in q:  # AC-2, first round: a claim with no evidence
        return json.dumps({"claim": "dFF and dJsc are unrelated", "numbers": {}, "evidence_calls": [], "caveats": []})
    if tools_seen == 0:
        return {"tool_calls": [tool_call("column_values", {"table": "target_table", "column": "htl_dose"}, "a")]}
    if tools_seen == 1:
        return {"tool_calls": [tool_call("within_paper_correlation", {"x": "ff", "y": "jsc"}, "b")]}
    return "Here you go: " + json.dumps({"claim": "too few papers to tell", "numbers": {"n_papers": 2},
                                         "evidence_calls": ["T2"], "caveats": []})


def decision(body):
    items = [json.loads(l) for l in last_user_text(body).splitlines()[1:]]
    return {"verdicts": [{"id": i["id"], "choice": "accept", "p": 0.9 if i["id"] == "AC-1" else 0.6,
                          "reason": "numbers match the cited result"} for i in items]}


class Analyze(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = write_data(self.tmp / "data" if (self.tmp / "data").mkdir() is None else None)
        self.skills = self.tmp / "skills"
        self.trace = self.tmp / "out" / "trace.jsonl"
        self.rt, self.fake = runtime({"planner": planner, "analyst": analyst, "decision": decision,
                                      "reporter": lambda b: "# Key insights\n- AC-1: PCE rises (tentative)",
                                      "learner": lambda b: "- prefer paper_deltas over raw queries"},
                                     hooks=[TraceHook(self.trace), DataMapHook(self.data, self.skills)],
                                     skills_dirs=[self.skills])

    def test_plan_tools_decide_report(self):
        out = self.tmp / "out"
        result = Analyst(self.rt, DataTools(self.data)).run("what drives PCE gains?", out)
        self.assertEqual(result.counts(), {"accept": 1, "verify_more": 1})
        ac1, ac2 = result.acs
        self.assertEqual((ac1.verdict.choice, ac1.verdict.by, ac1.rounds), ("accept", "small/model", 1))
        self.assertTrue(ac2.verdict.reason.startswith("escalate:"))  # p=0.6 -> verify_more
        rounds = json.loads((out / "evidence" / "AC-2.json").read_text(encoding="utf-8"))["rounds"]
        self.assertEqual([r["verdict"]["by"] for r in rounds], ["rule", "small/model"])
        self.assertEqual(rounds[0]["verdict"]["reason"], "the claim cites no tool results")
        self.assertIn("similar: htl_mod_kind", rounds[1]["calls"][0]["result"])  # the model saw the valid name
        plan = (out / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("### AC-1: Does HTL modification raise PCE?", plan)
        self.assertIn("evidence: evidence/AC-1.json", plan)
        self.assertIn("verdict: accept p=0.90 by=small/model rounds=1", plan)
        self.assertIn("status: open (1/2 accepted)", plan)
        self.assertEqual((out / "INSIGHTS.md").read_text(encoding="utf-8").splitlines()[0], "# Key insights")
        analyst_system = self.fake.by_role("analyst")[0]["messages"][0]["content"]
        self.assertIn("## Skill: data-map", analyst_system)  # the hook built the compass before the run
        decision_item = json.loads(last_user_text(self.fake.by_role("decision")[0]).splitlines()[1])
        self.assertIn('"median": 1.25', decision_item["evidence"])
        self.assertEqual(decision_item["numbers_not_found_in_evidence"], [])

        # trace -> learned-rules skill for the next run
        records = load(self.trace)
        d = digest(records)
        self.assertEqual(d["runs"], {"analyze": 1})
        self.assertTrue(any("htl_dose" in e["call"] for e in d["tools_failed"]))
        self.assertIn("Tool calls that failed", render(d))
        path = learn(records, self.skills, runtime=self.rt)
        text = path.read_text(encoding="utf-8")
        self.assertIn('paper_deltas({"metrics": ["pce"]})', text)
        self.assertIn("similar: htl_mod_kind", text)
        self.assertIn("prefer paper_deltas over raw queries", text)
        self.assertIn("learned-rules", [s.name for s in SkillBook(self.skills).for_role("analyst")])


PAGE1 = ("doi: 10.1234/test.5678\nDevice fabrication. NiOx films were annealed at 300 C for 60 min in air. The control "
         "device used bare NiOx; the target device used a PEAI interlayer on NiOx.")
PAGE2 = "Results. Table 1 lists the champion PCE of 20.1% for the control and 22.5% for the target device."


def mapper(body):
    quote = "the target device used a PEAI interlayer on NiOx"
    layer = lambda dev, lid, typ, mat: {"device": dev, "sheet": "Layer", "page": 1, "source": "text", "evidence": quote,  # noqa: E731
                                         "note": None, "values": [{"column": "LayerID", "value": lid},
                                                                  {"column": "LayerType", "value": typ},
                                                                  {"column": "Material", "value": mat}]}
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "bare NiOx"},
                        {"sample": "S2", "paper_name": "target", "description": "PEAI interlayer"}],
            "rows": [layer("S1", "1", "Anode", "ITO"), layer("S1", "2", "HTL", "NiOx"), layer("S2", "1", "Anode", "ITO"),
                     layer("S2", "2", "HTL", "NiOx"), layer("S2", "3", "HTL treatment", "PEAI")]}


def extractor(body):
    task = last_user_text(body)
    if 'SHEET "Process"' in task:
        anneal = lambda dev: {"device": dev, "sheet": "Process", "page": 1, "source": "text",  # noqa: E731
                              "evidence": "NiOx films were annealed at 300 C for 60 min in air", "note": None,
                              "values": [{"column": "LayerID", "value": "2"}, {"column": "StepOrder", "value": "1"},
                                         {"column": "ProcessType", "value": "Annealing"},
                                         {"column": "Parameter", "value": "300, 60"}, {"column": "Unit", "value": "C, min"}]}
        return {"rows": [anneal("S1"), anneal("S2"), dict(anneal("S9"))]}  # S9 is not in the paper map
    if 'SHEET "Properties"' in task:
        pce = lambda dev, v, ev, src: {"device": dev, "sheet": "Properties", "page": 2, "source": src, "evidence": ev,  # noqa: E731
                                       "note": None, "values": [{"column": "Properties", "value": "Champion PCE"},
                                                                {"column": "Values", "value": v},
                                                                {"column": "Unit", "value": "%"}]}
        return {"rows": [pce("S1", "20.1", "Table 1", "table"),
                         pce("S2", "22.5", "the target device reached 23.9% efficiency in our lab", "text"),
                         {"device": "S2", "sheet": "Stability", "page": 2, "source": "figure", "evidence": "Fig. 5a",
                          "note": "stability curve: normalized PCE vs time, S2", "values": [{"column": "Environment", "value": "Ambient air"},
                                                   {"column": "Retention (%)", "value": "85"}]}]}
    return {"rows": []}


class Extract(unittest.TestCase):
    def test_paper_map_rows_checks_and_workbooks(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        def accept_all(body):
            items = [json.loads(l) for l in last_user_text(body).splitlines()[1:]]
            return {"verdicts": [{"id": i["id"], "choice": "accept", "p": 0.95, "reason": "supported"} for i in items]}

        rt, fake = runtime({"mapper": mapper, "extract": extractor, "decision": accept_all}, hooks=[QuoteCheckHook()],
                           models={"default": "mid/model", "mapper": "big/model", "decision": "small/model"})
        res = Extractor(rt).extract(pdf, tmp / "out")
        self.assertEqual(res.errors, [])
        self.assertEqual(fake.by_role("mapper")[0]["model"], "big/model")
        self.assertEqual(len(res.rows), 5 + 2 + 3)  # the S9 row was dropped
        names = sorted(Path(f).name for f in res.files)
        self.assertEqual(names, ["10.1234_test.5678_Sample1.xlsx", "10.1234_test.5678_Sample2.xlsx"])

        d2 = load_workbook(tmp / "out" / names[1])
        self.assertEqual([r.cells["Material"] for r in d2.sheets["Layer"]], ["ITO", "NiOx", "PEAI"])
        step = d2.sheets["Process"][0].cells
        self.assertEqual((step["LayerID"], step["Parameter"], step["Unit"]), (2, "300, 60", "C, min"))  # numbers typed
        self.assertEqual(d2.sheets["Stability"][0].cells["Retention (%)"], 85)  # the model's reading of the plot stays in the draft

        with open(tmp / "out" / "review.csv", encoding="utf-8-sig") as fh:
            review = {(r["device"], r["sheet"], r["values"][:40]): r for r in csv.DictReader(fh)}
        by_reason = {k: (r["decision"], r["reason"]) for k, r in review.items()}
        fake_quote = next(v for k, v in by_reason.items() if k[0] == "S2" and k[1] == "Properties")
        self.assertEqual(fake_quote, ("verify_more", "quote not found on page 2"))
        figure = next(v for k, v in by_reason.items() if k[1] == "Stability")
        self.assertEqual(figure[0], "human_read")
        self.assertIn("Fig. 5a", figure[1])
        self.assertIn("reads the plot again", figure[1])
        self.assertIn("stability curve", figure[1])  # the model's note on what the graph shows
        table = next(v for k, v in by_reason.items() if k[0] == "S1" and k[1] == "Properties")
        self.assertEqual(table[0], "accept")  # 20.1 is in the page text, so the model judged it
        # 5 Layer + 2 Process quotes verified on page 1, the table value found on page 2 -> 8 judged and accepted;
        # the invented quote stops at the rules -> verify_more; the plot estimate is for a person to read again -> human_read
        self.assertEqual(res.counts(), {"accept": 8, "verify_more": 1, "human_read": 1})


class RecheckQuoteCheckpoint(unittest.TestCase):
    def test_quote_not_on_page_downgrades_to_review(self):
        tmp = Path(tempfile.mkdtemp())
        wb = write_workbook(tmp / "10.1016_j.test.2020.123_Sample1.xlsx",
                            {"Layer": [("LayerID", "LayerType", "Material", "Thickness (nm)"), (1, "Anode", "ITO", 150),
                                       (2, "HTL", "NiOx", 20)]})
        pdf = write_pdf(tmp / "p.pdf", "doi 10.1016/j.test.2020.123",
                        page_texts=["The ITO substrate was 150 nm thick and the NiOx layer was 20 nm in all devices."])

        def recheck(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            quotes = {"D1:Layer:2": "The ITO substrate was 150 nm thick", "D1:Layer:3": "the NiOx layer was 35 nm thick"}
            return {"devices": [{"device_id": "D1", "paper_device": "control"}],
                    "rows": [{"row_id": i, "found": True, "values": [{"column": "Thickness (nm)", "value": v}],
                              "page": 2, "source": "text", "evidence": quotes[i], "note": None, "key_issue": None}
                             for i, v in zip(ids, ["150", "20"])]}

        rt, _ = runtime({"recheck": recheck}, hooks=[QuoteCheckHook()])
        report = Rechecker(runtime=rt, judge=False).recheck(pdf, [wb])
        st = {(f.excel_row, f.status): f.detail for f in report.findings}
        self.assertIn((2, "match"), st)
        self.assertIn((3, "review"), st)
        self.assertIn("evidence quote not found on page 2", st[(3, "review")])


if __name__ == "__main__":
    unittest.main()
