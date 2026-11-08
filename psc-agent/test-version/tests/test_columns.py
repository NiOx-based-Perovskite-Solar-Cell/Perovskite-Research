"""The column listing: profile of the tables, the catalog and notes joined on, text/markdown/csv, the API and the CLI."""
import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

from psc_agent.analyze.columns import profile, summary, to_csv, to_markdown, to_text
from psc_agent.cli import main
from psc_agent.web import WebConfig, create_app


def make_data() -> Path:
    root = Path(tempfile.mkdtemp())
    data, eda = root / "processed" / "prepared_v2", root / "processed" / "eda"
    data.mkdir(parents=True), eda.mkdir(parents=True)
    pd.DataFrame({"device_id": ["a_Sample1", "a_Sample2", "b_Sample1"], "pce": [20.1, 21.5, None],
                  "htl_mod_kind": ["coating", "coating", "additive"], "is_coated": [True, True, False],
                  "empty": [None, None, None]}).to_csv(data / "target_table.csv", index=False)
    pd.DataFrame({"material": ["NiOx", "PEAI"], "unit": ["nm", "nm"]}).to_csv(data / "layers.csv", index=False)
    with open(eda / "column_catalog.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ตาราง", "คอลัมน์", "บทบาท", "ชนิด", "มีค่า", "%มีค่า", "ค่าไม่ซ้ำ", "อุปกรณ์", "เปเปอร์", "คำอธิบาย"])
        w.writerow(["target_table", "pce", "ผลลัพธ์ Y", "ตัวเลข", 2, 66, 2, 2, 1, "ประสิทธิภาพเซลล์"])
    with open(eda / "column_notes.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "column", "relevance", "description", "why"])
        w.writerow(["target_table", "htl_mod_kind", "core", "ชนิดการดัดแปลง HTL", "ตัวแปรต้นของงานวิเคราะห์"])
        w.writerow(["target_table", "empty", "drop", "ว่างทั้งคอลัมน์", "ไม่มีข้อมูล"])
    return root


class Profile(unittest.TestCase):
    def setUp(self):
        self.root = make_data()
        self.cols = profile(self.root / "processed" / "prepared_v2")
        self.by = {(c.table, c.column): c for c in self.cols}

    def test_measures_every_column(self):
        self.assertEqual(len(self.cols), 7)
        pce = self.by[("target_table", "pce")]
        self.assertEqual((pce.dtype, pce.non_null, pce.rows, pce.pct, pce.papers), ("number", 2, 3, 66.7, 1))
        self.assertEqual(self.by[("target_table", "is_coated")].dtype, "flag")
        kind = self.by[("target_table", "htl_mod_kind")]
        self.assertEqual((kind.dtype, kind.distinct, kind.papers, kind.examples[0]), ("text", 2, 2, "coating"))
        self.assertEqual(self.by[("target_table", "empty")].non_null, 0)
        self.assertIsNone(self.by[("layers", "material")].papers)  # no device_id: no paper count

    def test_joins_the_catalog_and_the_notes(self):
        pce = self.by[("target_table", "pce")]
        self.assertEqual((pce.role, pce.catalog_note, pce.relevance), ("ผลลัพธ์ Y", "ประสิทธิภาพเซลล์", ""))
        kind = self.by[("target_table", "htl_mod_kind")]
        self.assertEqual((kind.relevance, kind.description, kind.why), ("core", "ชนิดการดัดแปลง HTL", "ตัวแปรต้นของงานวิเคราะห์"))

    def test_summary_counts_unrated_columns(self):
        s = summary(self.cols)
        self.assertEqual(s["total"], {"columns": 7, "core": 1, "context": 0, "provenance": 0, "drop": 1, "unrated": 5})
        self.assertEqual(s["tables"]["layers"]["unrated"], 2)

    def test_formats(self):
        text, md, csv_text = to_text(self.cols), to_markdown(self.cols), to_csv(self.cols)
        self.assertIn("htl_mod_kind  [core] text", text)
        self.assertIn("meaning: ประสิทธิภาพเซลล์", text)  # the catalog note stands in until a description exists
        self.assertIn("| `htl_mod_kind` | core |", md)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["table"], "layers")


class Surfaces(unittest.TestCase):
    def test_api(self):
        root = make_data()
        app = create_app(WebConfig(root=root, require_key=False))
        app.testing = True
        c = app.test_client()
        body = c.get("/api/columns").get_json()
        self.assertEqual(body["summary"]["total"]["columns"], 7)
        self.assertEqual({x["column"] for x in body["columns"] if x["relevance"] == "drop"}, {"empty"})
        r = c.get("/api/columns?format=csv")
        self.assertEqual((r.status_code, r.mimetype), (200, "text/csv"))
        self.assertIn(b"htl_mod_kind", r.data)
        self.assertEqual(c.get("/api/columns?format=md").mimetype, "text/markdown")

    def test_cli_filters(self):
        root = make_data()
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["columns", "--data", str(root / "processed" / "prepared_v2"), "--relevance", "drop", "--format", "csv"])
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual((code, [r["column"] for r in rows]), (0, ["empty"]))
        target = root / "cols.md"
        main(["columns", "--data", str(root / "processed" / "prepared_v2"), "--table", "layers", "--format", "md", "--out", str(target)])
        self.assertIn("## layers (2 columns, 2 rows)", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
