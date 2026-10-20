import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import _setup  # noqa: F401
import build_map as bm


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text('"""The package."""\n', encoding="utf-8")
    (root / "pkg" / "a.py").write_text(
        '"""Module A does things."""\nfrom .b import helper\nLIMIT = 3\n\n\ndef run():\n    return helper("processed/x/out.csv")\n',
        encoding="utf-8")
    (root / "pkg" / "b.py").write_text('def helper(p):\n    return p\n\n\nclass Thing:\n    def go(self):\n        pass\n',
                                       encoding="utf-8")
    (root / "main.py").write_text('"""Entry point."""\nfrom pkg import a\n\nif __name__ == "__main__":\n    a.run()\n',
                                  encoding="utf-8")
    nb = {"cells": [{"cell_type": "markdown", "id": "m1", "source": ["# Title\n", "## Part 1\n"]},
                    {"cell_type": "code", "id": "c1", "source": ["from pkg import b\n", "def f():\n",
                                                                "    return 'processed/x/out.csv'\n"],
                     "outputs": [{"output_type": "error", "ename": "KeyError"}]}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (root / "nb").mkdir()
    (root / "nb" / "analysis.ipynb").write_text(json.dumps(nb), encoding="utf-8")
    (root / "Raw Data").mkdir()
    (root / "Raw Data" / "x_Sample1.xlsx").write_bytes(b"xlsx")
    return root


class RepoMap(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.map = bm.paths(self.root)["map"]

    def test_build_content(self):
        kind, _ = bm.refresh(self.root)
        self.assertEqual(kind, "structure")
        text = self.map.read_text(encoding="utf-8")
        self.assertIn("**`pkg/a.py`** — Module A does things.", text)
        self.assertIn("- def: run L6", text)
        self.assertIn("- const: LIMIT L3", text)
        self.assertIn("- imports: `b.py`", text)  # relative import resolved, shown relative to pkg/
        self.assertIn("- used by: `../main.py`".replace("../", ""), text.replace("`main.py`", "`main.py`"))
        self.assertIn("- class Thing L5: go", text)
        self.assertIn("- runnable:", text)
        self.assertIn("- names data: `processed/x/out.csv`", text)
        self.assertIn("**`nb/analysis.ipynb`** — 2 cells", text)
        self.assertIn("error outputs in cells c1", text)
        self.assertIn("c0#m1 # Title", text)
        self.assertIn("- defs: f c1", text)
        self.assertIn("`Raw Data/`: 1 xlsx", text)
        self.assertIn("## Unannotated", text)
        self.assertIn("Repo compass", bm.compass(self.root))
        self.assertNotIn("## Python modules", bm.compass(self.root))

    def test_unchanged_then_move_then_edit(self):
        bm.refresh(self.root)
        before = self.map.read_text(encoding="utf-8")
        self.assertEqual(bm.refresh(self.root), ("unchanged", []))
        os.replace(self.root / "pkg" / "b.py", self.root / "pkg" / "c.py")  # a file move
        kind, changes = bm.refresh(self.root, structure_only=True)
        self.assertEqual((kind, changes), ("structure", ["+pkg/c.py", "-pkg/b.py"]))
        self.assertIn("pkg/c.py", self.map.read_text(encoding="utf-8"))
        self.assertNotEqual(before, self.map.read_text(encoding="utf-8"))
        time.sleep(0.01)
        (self.root / "pkg" / "c.py").write_text("def helper2():\n    pass\n", encoding="utf-8")
        self.assertEqual(bm.refresh(self.root, structure_only=True), ("unchanged", []))  # edits wait for Stop
        self.assertEqual(bm.refresh(self.root)[0], "content")
        self.assertIn("helper2 L1", self.map.read_text(encoding="utf-8"))

    def test_notes_annotate_layout(self):
        notes = bm.paths(self.root)["notes"]
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("- pkg/: the core package\n", encoding="utf-8")
        bm.refresh(self.root, force=True)
        text = self.map.read_text(encoding="utf-8")
        self.assertIn("| `pkg/` | 3 py | the core package |", text)
        self.assertNotIn("- `pkg/`", text.split("## Unannotated")[1])

    def test_deterministic(self):
        bm.refresh(self.root, force=True)
        first = self.map.read_text(encoding="utf-8")
        bm.refresh(self.root, force=True)
        self.assertEqual(first, self.map.read_text(encoding="utf-8"))

    def test_invisible_edits_keep_map_identical_and_fresh(self):
        doc = self.root / "PLAN.md"
        doc.write_text("# PLAN: x\n", encoding="utf-8")
        bm.refresh(self.root)
        first = self.map.read_text(encoding="utf-8")
        time.sleep(0.01)
        doc.write_text("# PLAN: x\nevidence: test a.txt\n", encoding="utf-8")  # body change, same title
        self.assertTrue(bm.is_fresh(self.root))
        self.assertEqual(bm.refresh(self.root), ("unchanged", []))
        self.assertEqual(first, self.map.read_text(encoding="utf-8"))
        (self.root / "pkg" / "a.py").write_text("def other():\n    pass\n", encoding="utf-8")
        self.assertFalse(bm.is_fresh(self.root))  # a visible change is stale until rebuilt


if __name__ == "__main__":
    unittest.main()
