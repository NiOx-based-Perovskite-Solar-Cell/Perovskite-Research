"""Skills → system prompts, hooks as checkpoints, runtime, decision model, tool loop."""
import json
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime, tool_call

from psc_agent import BudgetHook, HookBlock, SkillBook, Tool, ToolError, TraceHook, decide, run_tools
from psc_agent.core.evidence import quote_on_page
from psc_agent.core.skills import DEFAULT_DIR
from psc_agent.sources.workbook import STANDARD_HEADERS


class Skills(unittest.TestCase):
    def test_role_prompts_compose_skills_in_order(self):
        book = SkillBook()
        names = [s.name for s in book.for_role("recheck")]
        self.assertEqual(names[0], "psc-domain")
        self.assertIn("recheck-task", names)
        self.assertNotIn("plan-task", names)
        prompt = book.prompt("recheck")
        self.assertTrue(prompt.startswith("You are the recheck model"))
        self.assertLess(prompt.index("## Skill: workbook-schema"), prompt.index("## Skill: recheck-task"))

    def test_project_skill_overrides_and_extends(self):
        d = Path(tempfile.mkdtemp())
        (d / "psc-domain").mkdir()
        (d / "psc-domain" / "SKILL.md").write_text("---\nname: psc-domain\nroles: [analyst]\norder: 5\n---\nOUR LAB RULES",
                                                   encoding="utf-8")
        (d / "extra").mkdir()
        (d / "extra" / "SKILL.md").write_text("no front matter: applies to every role", encoding="utf-8")
        book = SkillBook(d)
        self.assertEqual(book.project_dir, d)
        self.assertIn("OUR LAB RULES", book.prompt("analyst"))
        self.assertNotIn("Inverted (p-i-n)", book.prompt("analyst"))  # the default psc-domain was replaced
        self.assertNotIn("psc-domain", [s.name for s in book.for_role("recheck")])
        self.assertIn("no front matter", book.prompt("judge"))

    def test_schema_skill_names_every_standard_column(self):
        text = (DEFAULT_DIR / "workbook-schema" / "SKILL.md").read_text(encoding="utf-8")
        missing = [c for cols in STANDARD_HEADERS.values() for c in cols if c not in text]
        self.assertEqual(missing, [])


class QuoteCheck(unittest.TestCase):
    PAGES = ["Intro only.", "The NiOx films were annealed at 300 °C for 60 min in air, then cooled.",
             "The champion device reached a PCE of 22.5% with negligible hyste-\nresis."]

    def test_quotes(self):
        self.assertTrue(quote_on_page(self.PAGES, 2, "annealed at 300 °C for 60 min in air"))
        self.assertTrue(quote_on_page(self.PAGES, 2, "“annealed at 300 °C for 60 min in air”"))  # curly quotes
        self.assertTrue(quote_on_page(self.PAGES, 3, "reached a PCE of 22.5% with negligible hysteresis"))  # hyphenation
        self.assertTrue(quote_on_page(self.PAGES, 1, "annealed at 300 °C for 60 min in air"))  # off by one page
        self.assertFalse(quote_on_page(self.PAGES, 2, "annealed at 450 °C for 30 min under nitrogen"))
        self.assertFalse(quote_on_page(self.PAGES, 9, "annealed at 300 °C for 60 min in air"))
        self.assertIsNone(quote_on_page(self.PAGES, 2, "Fig. 3b"))
        self.assertIsNone(quote_on_page(self.PAGES, 2, "300 °C"))  # too short to check
        self.assertIsNone(quote_on_page(self.PAGES, 2, "anything long enough to check", source="figure"))


class RuntimeAndHooks(unittest.TestCase):
    def test_complete_runs_hooks_and_trace(self):
        trace = Path(tempfile.mkdtemp()) / "trace.jsonl"
        rt, fake = runtime({"judge": lambda b: {"ok": 1}}, hooks=[TraceHook(trace)])
        rt.complete("judge", [{"role": "system", "content": rt.system("judge")}, {"role": "user", "content": "hi"}])
        events = [json.loads(l)["event"] for l in trace.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["model_call", "model_result"])
        self.assertEqual(fake.requests[0]["model"], "big/model")
        self.assertAlmostEqual(rt.usage.cost_usd, 0.001)

    def test_budget_hook_blocks(self):
        budget = BudgetHook(0.0015)
        rt, fake = runtime({"judge": lambda b: {"ok": 1}}, hooks=[budget])
        msgs = [{"role": "system", "content": rt.system("judge")}, {"role": "user", "content": "x"}]
        rt.complete("judge", msgs)
        rt.complete("judge", msgs)  # spent 0.002 after this one
        with self.assertRaises(HookBlock):
            rt.complete("judge", msgs)
        self.assertEqual(len(fake.requests), 2)

    def test_ask_json_repairs_once(self):
        replies = iter(["not json at all", {"verdicts": []}])
        rt, fake = runtime({"judge": lambda b: next(replies)})
        from psc_agent.recheck.prompts import JudgeAnswer, judge_schema

        self.assertEqual(rt.ask_json("judge", "q", JudgeAnswer, schema=judge_schema()).verdicts, [])
        self.assertIn("That JSON is invalid", last_user_text(fake.requests[1]))

    def test_models_per_role(self):
        rt, fake = runtime({"decision": lambda b: {"verdicts": []}})
        decide(rt, "t", [{"id": "a", "claim": "c", "evidence": "e"}])
        self.assertEqual(fake.requests[0]["model"], "small/model")


class Decision(unittest.TestCase):
    def test_typed_verdicts_and_escalation(self):
        trace = Path(tempfile.mkdtemp()) / "t.jsonl"

        def small(body):
            return {"verdicts": [{"id": "A", "choice": "accept", "p": 0.95, "reason": "shown"},
                                 {"id": "B", "choice": "revise", "p": 0.5, "reason": "unsure"},
                                 {"id": "zzz", "choice": "accept", "p": 1, "reason": "not asked"}]}

        rt, _ = runtime({"decision": small}, hooks=[TraceHook(trace)])
        items = [{"id": i, "claim": "c", "evidence": "e"} for i in "ABC"]
        v = decide(rt, "test", items)
        self.assertEqual((v["A"].choice, v["A"].by), ("accept", "small/model"))
        self.assertEqual(v["B"].choice, "verify_more")
        self.assertTrue(v["B"].reason.startswith("escalate:"))
        self.assertEqual((v["C"].choice, v["C"].reason), ("verify_more", "no decision returned"))
        self.assertNotIn("zzz", v)
        rec = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if '"decision"' in l][-1]
        self.assertEqual(rec["counts"], {"accept": 1, "verify_more": 2})


class ToolLoop(unittest.TestCase):
    def tools(self):
        def lookup(column):
            if column != "pce":
                raise ToolError(f"no column {column!r}; similar: pce")
            return {"column": "pce", "median": 21.0}
        return [Tool("lookup", "look up a column", {"type": "object", "properties": {"column": {"type": "string"}},
                                                    "required": ["column"]}, lookup)]

    def test_loop_feeds_errors_back_and_numbers_results(self):
        def analyst(body):
            n_results = sum(m["role"] == "tool" for m in body["messages"])
            if n_results == 0:
                return {"tool_calls": [tool_call("lookup", {"column": "pec"}, "a"), tool_call("nope", {}, "b")]}
            if n_results == 2:
                return {"tool_calls": [tool_call("lookup", {"column": "pce"}, "c")]}
            return "final answer citing T3"

        rt, fake = runtime({"analyst": analyst})
        out = run_tools(rt, "analyst", [{"role": "system", "content": rt.system("analyst")},
                                        {"role": "user", "content": "go"}], self.tools())
        self.assertEqual(out.final, "final answer citing T3")
        self.assertEqual([(c.id, c.ok) for c in out.calls], [("T1", False), ("T2", False), ("T3", True)])
        self.assertIn("similar: pce", out.calls[0].result)
        self.assertIn("unknown tool 'nope'", out.calls[1].result)
        tool_msgs = [m for m in fake.requests[-1]["messages"] if m["role"] == "tool"]
        self.assertTrue(tool_msgs[2]["content"].startswith("[T3] "))
        self.assertTrue(all("tools" in b for b in fake.requests))  # tools resent on every call

    def test_step_limit_forces_an_answer(self):
        def analyst(body):
            if body.get("tool_choice") == "none":
                return "best effort"
            return {"tool_calls": [tool_call("lookup", {"column": "pce"})]}

        rt, fake = runtime({"analyst": analyst})
        out = run_tools(rt, "analyst", [{"role": "system", "content": rt.system("analyst")},
                                        {"role": "user", "content": "go"}], self.tools(), max_steps=2)
        self.assertEqual((out.final, len(out.calls)), ("best effort", 2))
        self.assertEqual(fake.requests[-1]["tool_choice"], "none")


if __name__ == "__main__":
    unittest.main()
