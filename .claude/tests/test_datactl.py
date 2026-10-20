import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import openpyxl

import _setup  # noqa: F401
import datactl


def run(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = datactl.main(list(map(str, argv)))
    return code, out.getvalue()


class Datactl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_csv_schema_and_values(self):
        p = self.tmp / "t.csv"
        p.write_text("device_id,htl_mod_kind,pce\nd1,coating,20.1\nd2,,19.5\nd3,coating,21\n", encoding="utf-8-sig")
        code, out = run("csv", p)
        self.assertEqual(code, 0)
        self.assertIn("3 rows x 3 columns", out)
        self.assertRegex(out, r"pce\s+float\s+3/3 filled")
        self.assertRegex(out, r"htl_mod_kind\s+text\s+2/3 filled")
        code, out = run("csv", p, "--values", "htl_mod_kind")
        self.assertIn("2  coating", out)
        self.assertIn("<empty>", out)
        code, out = run("csv", p, "--values", "htl_dose")
        self.assertEqual(code, 1)
        self.assertIn("similar: htl_mod_kind", out)

    def test_xlsx_survey(self):
        for name, hdr in (("a_Sample1.xlsx", ("Properties", "Values", "Errors", "Unit")),
                          ("a_Sample2.xlsx", ("Properties", "Values", "Unit"))):
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Properties"
            ws.append(hdr)
            ws.append(["Voc", 1.1, None, "V"] if len(hdr) == 4 else ["Voc", 1.1, "V"])
            wb.save(self.tmp / name)
        code, out = run("xlsx", self.tmp, "--units")
        self.assertEqual(code, 0)
        self.assertIn("[Properties] in 2 workbook(s), 2 data rows", out)
        self.assertIn("1 x Properties | Values | Errors | Unit", out)
        self.assertIn("2  V", out)

    def test_notebook_outline_grep_cell(self):
        nb = {"cells": [{"cell_type": "markdown", "id": "m", "source": ["# T"]},
                        {"cell_type": "code", "id": "abc", "execution_count": 3, "source": ["def f(x):\n", "    return x\n"],
                         "outputs": [{"output_type": "error", "ename": "KeyError", "evalue": "'col'",
                                      "traceback": ["\x1b[31mKeyError\x1b[0m: 'col'"]}]}],
              "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        p = self.tmp / "n.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        self.assertIn("ERR KeyError", run("nb", p)[1])
        self.assertIn("c1 id=abc", run("nb", p, "--grep", "def f")[1])
        code, out = run("nb", p, "--cell", "abc")
        self.assertEqual(code, 0)
        self.assertIn("return x", out)
        self.assertIn("KeyError: 'col'", out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(run("nb", p, "--cell", "9")[0], 1)


if __name__ == "__main__":
    unittest.main()
