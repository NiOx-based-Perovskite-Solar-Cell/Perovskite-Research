import contextlib
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _setup  # noqa: F401
import plan as P

PY = '"' + sys.executable.replace("\\", "/") + '"'
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = fn(*a)
    return code, out.getvalue()


class PlanFlow(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        quiet(P.cmd_new, self.root, "Demo task", "g", ["tests pass", "UI shows total"],
              [f"{PY} -c \"print('3 tests OK')\""], False)

    def states(self):
        return {r["id"]: r["state"] for r in P.evaluate(P.load(self.root), self.root)}

    def test_lifecycle(self):
        self.assertEqual(self.states(), {"AC-1": "verify_more", "AC-2": "verify_more"})
        self.assertEqual(quiet(P.cmd_run, self.root, "AC-1", None, 60)[0], 0)
        self.assertEqual(self.states()["AC-1"], "needs_judge")
        evidence = self.root / P.load(self.root).acs[0].files()[0][1]
        self.assertIn("# exit: 0", evidence.read_text(encoding="utf-8"))
        self.assertIn("3 tests OK", evidence.read_text(encoding="utf-8"))

        self.assertEqual(quiet(P.cmd_verdict, self.root, "AC-1", "accept", "output shows OK", 0.9, "ac-judge")[0], 0)
        self.assertEqual(self.states()["AC-1"], "accept")
        time.sleep(0.01)
        evidence.write_text(evidence.read_text(encoding="utf-8") + "\nedited", encoding="utf-8")
        self.assertEqual(self.states()["AC-1"], "needs_judge")  # evidence changed -> verdict stale

        before, after = self.root / "b.png", self.root / "a.png"
        before.write_bytes(PNG)
        after.write_bytes(PNG + b"1")
        quiet(P.cmd_attach, self.root, "AC-2", "screenshot", [str(before), str(after)])
        self.assertEqual(self.states()["AC-2"], "needs_judge")
        quiet(P.cmd_verdict, self.root, "AC-1", "accept", "ok", 0.9, "ac-judge")
        quiet(P.cmd_verdict, self.root, "AC-2", "accept", "after shows total", 0.8, "ac-judge")
        text = (self.root / "PLAN.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("verdict: accept"), 2)  # replaced, not duplicated
        self.assertLess(text.index("evidence: screenshot"), text.index("### AC-2") + 400)

        self.assertEqual(quiet(P.cmd_close, self.root)[0], 0)
        self.assertFalse((self.root / "PLAN.md").exists())
        archived = list((self.root / ".claude" / "plan" / "archive").glob("*-demo-task.md"))
        self.assertEqual(len(archived), 1)
        self.assertIn("status: done", archived[0].read_text(encoding="utf-8"))

    def test_failing_check_blocks_accept(self):
        code, _ = quiet(P.cmd_run, self.root, "AC-1", f"{PY} -c \"import sys; sys.exit(3)\"", 60)
        self.assertEqual(code, 3)
        self.assertEqual(self.states()["AC-1"], "revise")
        code, out = quiet(P.cmd_verdict, self.root, "AC-1", "accept", "looks fine", 0.9, "ac-judge")
        self.assertEqual(code, 2)
        self.assertIn("refused", out)
        self.assertEqual(quiet(P.cmd_close, self.root)[0], 1)

    def test_evidence_lines_stay_in_their_ac(self):
        quiet(P.cmd_run, self.root, "AC-1", None, 60)
        f = self.root / "note.txt"
        f.write_text("x", encoding="utf-8")
        quiet(P.cmd_attach, self.root, "AC-1", "file", [str(f)])
        plan = P.load(self.root)
        self.assertEqual([k for k, _ in plan.acs[0].evidence], ["test", "file"])
        self.assertEqual(plan.acs[1].evidence, [])

    def test_code_change_after_evidence_makes_it_stale(self):
        git = lambda *a: subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, check=True)  # noqa: E731
        git("init", "-q")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        quiet(P.cmd_run, self.root, "AC-1", None, 60)
        quiet(P.cmd_verdict, self.root, "AC-1", "accept", "ok", 0.9, "ac-judge")
        self.assertEqual(self.states()["AC-1"], "accept")  # PLAN.md / evidence edits do not count as code
        (self.root / "app.py").write_text("x = 2\n", encoding="utf-8")
        row = next(r for r in P.evaluate(P.load(self.root), self.root) if r["id"] == "AC-1")
        self.assertEqual(row["state"], "verify_more")
        self.assertIn("code changed after", row["reason"])
        quiet(P.cmd_run, self.root, "AC-1", None, 60)
        self.assertEqual(self.states()["AC-1"], "needs_judge")

    def test_gate_blocks_once_per_state(self):
        payload = {"session_id": "s1"}
        first = P.gate(payload, self.root)
        self.assertEqual(first["decision"], "block")
        self.assertIn("AC-1 [verify_more]", first["reason"])
        second = P.gate(payload, self.root)
        self.assertNotIn("decision", second)
        self.assertIn("0/2 accepted", second["systemMessage"])
        quiet(P.cmd_run, self.root, "AC-1", None, 60)  # progress -> the gate speaks again
        self.assertEqual(P.gate(payload, self.root)["decision"], "block")
        self.assertIsNone(P.gate(payload, Path(tempfile.mkdtemp())))  # no PLAN.md -> no gate


if __name__ == "__main__":
    unittest.main()
