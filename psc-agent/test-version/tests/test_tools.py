"""Data tools (the project's within-paper method) and the data-map skill. Needs pandas."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from psc_agent import SkillBook, ToolError
from psc_agent.analyze import DataMapHook
from psc_agent.analyze.datamap import refresh
from psc_agent.analyze.tools import DataTools, doi_key, sign_test

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / "processed" / "prepared_v2" / "target_table.csv"
REF = REPO / "processed" / "eda" / "within_paper_deltas.csv"

ROWS = [  # paper A spelled two ways (one control + two treated), paper B (control + coating + additive),
    ("10.1000_a.1_Sample1", None, 20.0, 80.0, 24.0, 0.9),  # paper C has no control
    ("10_1000_a_1_Sample2", "coating", 22.0, 82.0, 24.5, 0.5),
    ("10.1000_a.1_Sample3", "coating", 23.0, 83.0, 24.2, 0.4),
    ("10.2000_b.2_Sample1", None, 18.0, 78.0, 23.0, 1.0),
    ("10.2000_b.2_Sample2", "coating", 19.0, 79.0, 23.5, 0.7),
    ("10.2000_b.2_Sample3", "additive", 17.0, 77.0, 22.0, 1.2),
    ("10.3000_c.3_Sample1", "coating", 21.0, 81.0, 24.0, None),
]


def write_data(d: Path) -> Path:
    df = pd.DataFrame(ROWS, columns=["device_id", "htl_mod_kind", "pce", "ff", "jsc", "vtfl"])
    df.to_csv(d / "target_table.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"device_id": [r[0] for r in ROWS], "layer_id": range(len(ROWS))}).to_csv(d / "layers.csv", index=False)
    return d


class Tools(unittest.TestCase):
    def setUp(self):
        self.d = write_data(Path(tempfile.mkdtemp()))
        self.t = DataTools(self.d)

    def test_paper_deltas_merge_spellings_and_count_papers(self):
        out = self.t.paper_deltas(["pce", "ff"])
        self.assertEqual(out["papers_with_pairs"], 2)  # A (two spellings merged) and B; C has no control
        per = {r["paper"]: r for r in out["per_paper"]}
        a = per["10.1000_a.1"]
        self.assertEqual((a["n_control"], a["n_treated"], a["d_pce"]), (1, 2, 2.5))  # (22+23)/2 - 20
        self.assertEqual(per["10.2000_b.2"]["d_pce"], 0.0)  # (19+17)/2 - 18
        self.assertEqual(out["summary"]["pce"], {"n_papers": 2, "positive": 1, "negative": 0, "median": 1.25,
                                                 "mean": 1.25, "sign_test_p": 1.0})
        self.assertEqual(out["papers_without_control"], ["10.3000_c.3"])
        self.assertEqual(out["merged_doi_spellings"], {"10.1000_a.1": ["10.1000_a.1", "10_1000_a_1"]})

    def test_levels_vote_once_per_paper(self):
        out = self.t.paper_deltas(["pce"], level_column="htl_mod_kind")
        self.assertEqual(out["by_level"]["coating"]["pce"]["n_papers"], 2)
        self.assertEqual(out["by_level"]["additive"]["pce"]["median"], -1.0)
        self.assertEqual(out["summary"]["pce"]["n_papers"], 2)  # B's two levels are averaged into one vote

    def test_correlations(self):
        d = self.t.within_paper_correlation("pce", "ff")
        self.assertEqual((d["mode"], d["n_papers"]), ("delta", 2))
        m = self.t.within_paper_correlation("pce", "vtfl", mode="demeaned")
        self.assertEqual((m["n_devices"], m["n_papers"]), (6, 2))
        self.assertLess(m["rho"], 0)  # within each paper, higher PCE goes with lower vtfl

    def test_errors_name_the_valid_options(self):
        with self.assertRaisesRegex(ToolError, "similar: htl_mod_kind"):
            self.t.column_values("target_table", "htl_mod_kin")
        with self.assertRaisesRegex(ToolError, "did you mean target_table"):
            self.t.table("target_tabel")
        with self.assertRaisesRegex(ToolError, "not numeric"):
            self.t.paper_deltas(["htl_mod_kind"])
        with self.assertRaisesRegex(ToolError, "unknown op"):
            self.t.query_rows("target_table", ["pce"], [["pce", "~", "1"]])
        with self.assertRaisesRegex(ToolError, "give a number"):
            self.t.query_rows("target_table", ["pce"], [["pce", ">", "high"]])

    def test_inspection_tools(self):
        q = self.t.query_rows("target_table", ["device_id", "pce"], [["pce", ">", "20"], ["htl_mod_kind", "notnull"]])
        self.assertEqual(q["matched"], 3)
        self.assertEqual(self.t.query_rows("target_table", ["pce"], [["htl_mod_kind", "in", "additive|none"]])["matched"], 1)
        vals = {v["value"]: v["count"] for v in self.t.column_values("target_table", "htl_mod_kind")["values"]}
        self.assertEqual(vals, {"coating": 4, "<empty>": 2, "additive": 1})
        compact = self.t.describe_table("target_table")["columns"][2]
        self.assertEqual((compact["column"], compact["type"], compact["filled"], compact["papers"]), ("pce", "number", 7, 3))
        self.assertNotIn("examples", compact)
        self.assertIn("examples", self.t.describe_table("target_table", ["pce"])["columns"][0])
        self.assertEqual(len(self.t.paper_devices("10_1000_A_1")["devices"]), 3)  # any spelling
        self.assertEqual(self.t.list_tables()["tables"][0]["papers"], 3)

    def test_sign_test(self):
        self.assertAlmostEqual(sign_test(28, 29), 1.1176e-07, places=10)
        self.assertEqual(sign_test(1, 2), 1.0)
        self.assertIsNone(sign_test(0, 0))
        self.assertEqual(doi_key("10.1021_ACSAMI.5c16972"), doi_key("10_1021_acsami_5c16972"))

    @unittest.skipUnless(REAL.exists() and REF.exists(), "project data not available")
    def test_reproduces_the_projects_reference_deltas(self):
        t = DataTools(REAL.parent)
        per = {doi_key(r["paper"]): r["d_pce"] for r in t.paper_deltas(["pce"])["per_paper"]}
        ref = pd.read_csv(REF)
        diffs = [abs(per[doi_key(p)] - d) for p, d in zip(ref["paper"], ref["delta_pce"])]
        self.assertEqual(len(diffs), 22)
        self.assertLess(max(diffs), 1e-3)


class DataMap(unittest.TestCase):
    def test_map_is_a_skill_and_follows_the_data(self):
        data = write_data(Path(tempfile.mkdtemp()))
        skills = Path(tempfile.mkdtemp())
        self.assertTrue(refresh(data, skills))
        self.assertFalse(refresh(data, skills))  # nothing changed
        text = (skills / "data-map" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("- target_table: 7 rows", text)
        self.assertIn("`pce` number · 7 · 3 papers", text)
        book = SkillBook(skills)
        self.assertIn("data-map", [s.name for s in book.for_role("analyst")])
        self.assertNotIn("data-map", [s.name for s in book.for_role("recheck")])

        (data / "layers.csv").rename(data / "layer_rows.csv")  # a moved file -> the hook rebuilds the map

        class RT:  # just what the hook touches
            skills = book
        DataMapHook(data, skills).on_run_start(runtime=RT)
        self.assertIn("- layer_rows:", (skills / "data-map" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("layer_rows", book.skills["data-map"].body)  # runtime skills reloaded


if __name__ == "__main__":
    unittest.main()
