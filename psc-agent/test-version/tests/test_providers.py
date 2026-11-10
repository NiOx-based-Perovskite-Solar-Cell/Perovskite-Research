"""Gemini and Ollama Cloud clients (against a mock HTTP server), native-PDF extraction, urls-only SI finder, run comparison."""
import base64
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import httpx
from _fake import runtime, tool_call
from _fixtures import write_pdf
from test_evaluate import PAGE1, PAGE2, fake_mapper, record_s1, record_s2, row

from psc_agent import Extractor, Runtime, load_paper, write_workbook
from psc_agent.cli import main
from psc_agent.core.decision import SCHEMA as DECISION_SCHEMA
from psc_agent.core.gemini import GeminiClient, GeminiError, to_contents, to_gemini_schema
from psc_agent.core.ollama import OllamaCloudClient
from psc_agent.core.openrouter import OpenRouterError
from psc_agent.evaluate import evaluate_pairs
from psc_agent.evaluate.audit import _schema as audit_schema
from psc_agent.evaluate.versus import compare_runs, load_run, write_comparison
from psc_agent.extract.extractor import _schema as extract_schema
from psc_agent.recheck.prompts import extraction_schema, judge_schema
from psc_agent.sources.paper import paper_parts
from psc_agent.sources.pairing import PaperPair
from psc_agent.sources.si import SiFinder

NO_KEYS = ("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_CLOUD_API_KEY", "OLLAMA_API_KEY")


def check_gemini_schema(s: dict) -> None:
    """What Gemini's responseSchema accepts: one upper-case type, string enums, required inside properties, no extras."""
    assert not set(s) & {"additionalProperties", "strict", "$ref", "name", "$defs"}, s
    if "type" in s:
        assert isinstance(s["type"], str) and s["type"].isupper(), s
    assert all(isinstance(x, str) for x in s.get("enum", [])), s
    assert set(s.get("required", [])) <= set(s.get("properties", {})), s
    for v in s.get("properties", {}).values():
        check_gemini_schema(v)
    if "items" in s:
        check_gemini_schema(s["items"])
    for o in s.get("anyOf", []):
        check_gemini_schema(o)


class Schema(unittest.TestCase):
    def test_what_changes(self):
        s = {"name": "t", "strict": True, "schema": {"type": "object", "additionalProperties": False, "required": ["a", "b", "c"], "properties": {
            "a": {"type": ["string", "null"]}, "b": {"type": "integer", "enum": [1, 2]},
            "c": {"type": "array", "items": {"type": "string", "enum": ["x", "y", None]}}}}}
        self.assertEqual(to_gemini_schema(s), {
            "type": "OBJECT", "required": ["a", "b", "c"], "propertyOrdering": ["a", "b", "c"],
            "properties": {"a": {"type": "STRING", "nullable": True}, "b": {"type": "INTEGER"},
                           "c": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["x", "y"], "nullable": True}}}})

    def test_refs_and_any_of(self):
        s = {"$defs": {"D": {"type": "string", "description": "d"}}, "type": "object", "required": ["x", "y", "z"], "properties": {
            "x": {"$ref": "#/$defs/D"}, "y": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "z": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
        g = to_gemini_schema(s)
        self.assertEqual(g["properties"]["x"], {"type": "STRING", "description": "d"})
        self.assertEqual(g["properties"]["y"], {"type": "NUMBER", "nullable": True})
        self.assertEqual(g["properties"]["z"], {"anyOf": [{"type": "STRING"}, {"type": "INTEGER"}]})

    def test_every_schema_the_workflows_send_is_accepted(self):
        schemas = [extract_schema("paper_map", ["Layer"], True), extract_schema("sheet_rows", ["Vender", "Materials"], False),
                   extraction_schema(["Material", "Thickness (nm)"]), judge_schema(), DECISION_SCHEMA, audit_schema()]
        for s in schemas:
            with self.subTest(s.get("name")):
                check_gemini_schema(to_gemini_schema(s))


class Messages(unittest.TestCase):
    def test_conversion(self):
        system, contents = to_contents([
            {"role": "system", "content": "Be exact."},
            {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                                         {"type": "file", "file": {"filename": "p.pdf", "file_data": "data:application/pdf;base64,JVBERg=="}}]},
            {"role": "assistant", "content": "{bad"}, {"role": "user", "content": "fix it"}])
        self.assertEqual(system, "Be exact.")
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])
        self.assertEqual(contents[0]["parts"], [{"text": "q"}, {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}},
                                                {"inline_data": {"mime_type": "application/pdf", "data": "JVBERg=="}}])

    def test_what_it_refuses(self):
        with self.assertRaisesRegex(GeminiError, "tool calling"):
            to_contents([{"role": "tool", "content": "x", "tool_call_id": "1"}])
        with self.assertRaisesRegex(GeminiError, "base64 data URLs"):
            to_contents([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x.example/a.png"}}]}])


def reply(text='{"ok": true}', finish="STOP", **meta):
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "let me think", "thought": True}, {"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 30, **meta},
        "modelVersion": "gemini-3.5-flash-lite"})


def client(handler, **kw):
    kw.setdefault("max_retries", 2)
    return GeminiClient("g-test-key", transport=httpx.MockTransport(handler), **kw)


SCHEMA = {"name": "t", "strict": True, "schema": {"type": "object", "additionalProperties": False, "required": ["ok"],
                                                  "properties": {"ok": {"type": ["boolean", "null"]}}}}
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]


class GeminiHttp(unittest.TestCase):
    def test_request_and_reply(self):
        seen = []

        def handler(request):
            seen.append(request)
            return reply()

        comp = client(handler, effort="low").complete("gemini-3.5-flash-lite", MSGS, schema=SCHEMA, max_tokens=5000)
        req, body = seen[0], json.loads(seen[0].content)
        self.assertEqual(req.url.path, "/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(req.headers["x-goog-api-key"], "g-test-key")
        self.assertNotIn("g-test-key", str(req.url))  # the key is a header, never in the address
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        cfg = body["generationConfig"]
        self.assertEqual((cfg["maxOutputTokens"], cfg["responseMimeType"], cfg["thinkingConfig"]), (5000, "application/json", {"thinkingLevel": "low"}))
        self.assertEqual(cfg["responseSchema"]["properties"]["ok"], {"type": "BOOLEAN", "nullable": True})
        self.assertEqual(json.loads(comp.content), {"ok": True})  # the thought part is not part of the answer
        self.assertEqual((comp.usage["prompt_tokens"], comp.usage["completion_tokens"], comp.usage["cost"]), (100, 50, 0.0))
        self.assertFalse(comp.usage["priced"])

    def test_price_turns_tokens_into_cost(self):
        comp = client(lambda r: reply(), price=(1.0, 10.0)).complete("gemini-3.5-flash-lite", MSGS)
        self.assertAlmostEqual(comp.usage["cost"], (100 * 1.0 + 50 * 10.0) / 1e6)

    def test_cut_off_blocked_and_tools(self):
        with self.assertRaises(OpenRouterError):  # MAX_TOKENS: the same error the other clients raise
            client(lambda r: reply(finish="MAX_TOKENS")).complete("m", MSGS)
        blocked = lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})  # noqa: E731
        with self.assertRaisesRegex(GeminiError, "blocked: SAFETY"):
            client(blocked).complete("m", MSGS)
        with self.assertRaisesRegex(GeminiError, "stopped with SAFETY"):
            client(lambda r: reply(finish="SAFETY")).complete("m", MSGS)
        with self.assertRaisesRegex(GeminiError, "tool calling"):
            client(lambda r: reply()).complete("m", MSGS, tools=[{"type": "function"}])

    def test_a_minute_quota_is_waited_out_a_daily_one_is_not(self):
        calls = []

        def per_minute(request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": 429, "message": "slow down", "details": [{"retryDelay": "7s"}]}})
            return reply()

        with mock.patch("psc_agent.core.gemini.time.sleep") as sleep:
            client(per_minute).complete("m", MSGS)
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(sleep.call_args[0][0], 7)  # the delay the server asked for

        daily = lambda r: httpx.Response(429, json={"error": {"code": 429, "message": "quota", "details": [  # noqa: E731
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
        n = []
        with self.assertRaisesRegex(GeminiError, "429"):
            client(lambda r: (n.append(1), daily(r))[1]).complete("m", MSGS)
        self.assertEqual(len(n), 1)  # no retries

    def test_other_errors(self):
        with self.assertRaisesRegex(GeminiError, "HTTP 404.*no longer available"):
            client(lambda r: httpx.Response(404, json={"error": {"message": "no longer available to new users"}})).complete("m", MSGS)
        calls = []

        def flaky(request):
            calls.append(1)
            return httpx.Response(503, text="overloaded") if len(calls) < 3 else reply()

        with mock.patch("psc_agent.core.gemini.time.sleep"):
            client(flaky).complete("m", MSGS)
        self.assertEqual(len(calls), 3)

    def test_identical_requests_are_cached(self):
        calls = []
        c = client(lambda r: (calls.append(1), reply())[1], cache_dir=tempfile.mkdtemp())
        first, second = c.complete("m", MSGS, schema=SCHEMA), c.complete("m", MSGS, schema=SCHEMA)
        self.assertEqual((first.cached, second.cached, len(calls)), (False, True, 1))

    def test_key_names(self):
        with mock.patch.dict(os.environ):
            for n in NO_KEYS:
                os.environ.pop(n, None)
            with self.assertRaisesRegex(ValueError, "GOOGLE_GEMINI_API_KEY"):
                GeminiClient()
            os.environ["GEMINI_API_KEY"] = "alt-name"
            self.assertEqual(GeminiClient()._http.headers["x-goog-api-key"], "alt-name")


class NativePdf(unittest.TestCase):
    def test_parts(self):
        tmp = Path(tempfile.mkdtemp())
        paper = load_paper(write_pdf(tmp / "a paper.pdf", "doi 10.1234/test.5678", page_texts=["Results."]))
        parts = paper_parts(paper, dpi=72, all_pages=False, pdf=True)
        self.assertEqual([p["type"] for p in parts], ["file", "text"])  # no page images: the file holds every page
        self.assertEqual(parts[0]["file"]["filename"], "a paper.pdf")
        self.assertEqual(base64.b64decode(parts[0]["file"]["file_data"].split(",", 1)[1]), paper.data)
        self.assertIn("=== Page 2 ===", parts[1]["text"])  # the page labels the quotes are checked against
        self.assertIn("image_url", [p["type"] for p in paper_parts(paper, dpi=72, all_pages=False)])

    def test_the_extractor_sends_the_whole_pdf_to_gemini(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "p.pdf", PAGE1, page_texts=[PAGE2])
        bodies = []

        def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            role = re.match(r"You are the (\w+) model", body["systemInstruction"]["parts"][0]["text"]).group(1)
            payload = ({"devices": [{"sample": "S1", "paper_name": "control", "description": "d"}], "rows": []}
                       if role == "mapper" else {"rows": []})
            return reply(json.dumps(payload))

        rt = Runtime("gemini-3.5-flash-lite", client=client(handler), native_pdf=True)
        result = Extractor(rt, decide_rows=False).extract(pdf, tmp / "out")
        self.assertEqual((len(result.devices), result.errors), (1, []))
        self.assertEqual(len(bodies), 4)  # the map and three sheet groups
        for b in bodies:
            parts = b["contents"][0]["parts"]
            mimes = [p["inline_data"]["mime_type"] for p in parts if "inline_data" in p]
            self.assertEqual(mimes, ["application/pdf"])  # one file, no page images
            self.assertTrue(any("=== Page 1 ===" in p.get("text", "") for p in parts))


class Ollama(unittest.TestCase):
    def test_openai_format_at_ollama_with_its_own_key(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"model": "deepseek-v4-pro", "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                                             "choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]})

        with mock.patch.dict(os.environ, {"OLLAMA_CLOUD_API_KEY": "o-test-key"}):
            c = OllamaCloudClient(transport=httpx.MockTransport(handler), max_retries=0)
        tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object"}}}]
        comp = c.complete("deepseek-v4-pro", MSGS, schema=SCHEMA, tools=tools)
        req, body = seen[0], json.loads(seen[0].content)
        self.assertEqual(str(req.url), "https://ollama.com/v1/chat/completions")
        self.assertEqual(req.headers["authorization"], "Bearer o-test-key")
        self.assertNotIn("provider", body)  # OpenRouter's routing field is not sent
        self.assertEqual((body["model"], body["tools"], body["response_format"]["type"]), ("deepseek-v4-pro", tools, "json_schema"))
        self.assertEqual((comp.usage["prompt_tokens"], comp.usage.get("cost", 0)), (5, 0))

    def test_no_credits_is_explained_and_a_missing_key_is_named(self):
        c = OllamaCloudClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(402, json={"error": "Add usage credits"})), max_retries=0)
        with self.assertRaisesRegex(OpenRouterError, "HTTP 402.*ollama.com/plans"):
            c.complete("deepseek-v4-pro", MSGS)
        with mock.patch.dict(os.environ):
            for n in NO_KEYS:
                os.environ.pop(n, None)
            with self.assertRaisesRegex(ValueError, "OLLAMA_CLOUD_API_KEY"):
                OllamaCloudClient()


class StubWeb:
    def search(self, query, n=8):
        return [{"title": "SI", "url": "https://pubs.example/si.pdf", "snippet": "supporting information"}]

    def page(self, url, max_chars=6000):
        return {"url": url, "title": "x", "text": "t", "links": []}

    def download_pdf(self, url, dest):
        raise AssertionError("urls-only must not download")


class UrlsOnly(unittest.TestCase):
    def test_the_agent_answers_with_urls_it_saw_and_downloads_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        paper = load_paper(write_pdf(tmp / "m.pdf", "doi: 10.1021/acsami.5c13836 text"))
        seen = {}
        steps = iter([{"tool_calls": [tool_call("web_search", {"query": "x SI"})]},
                      json.dumps({"status": "found", "notes": "the SI file", "links": ["https://pubs.example/si.pdf.", "https://invented.example/x"]})])

        def responder(body):
            seen.setdefault("tools", {t["function"]["name"] for t in body["tools"]})
            seen.setdefault("task", body["messages"][1]["content"])
            return next(steps)

        rt, _ = runtime({"si_finder": responder})
        res = SiFinder(rt, web=StubWeb(), download=False).find(paper, tmp / "SI", key="k")
        self.assertEqual(seen["tools"], {"web_search", "read_page"})  # no download tool to call
        self.assertIn("Do not download anything", seen["task"])
        self.assertEqual((res.status, res.files), ("links", []))
        self.assertEqual(res.links[0], "https://pubs.example/si.pdf")  # seen in a result, trailing dot trimmed
        self.assertNotIn("https://invented.example/x", res.links)
        self.assertIn("https://doi.org/10.1021/acsami.5c13836", res.links)  # the publisher fallback stays behind it


def good_extractor(body):
    from _fake import last_user_text

    if 'SHEET "Properties"' in last_user_text(body):
        pce = lambda dev, v: row(dev, "Properties", 2, "table", "Table 1", Properties="Champion PCE", Values=v, Unit="%")  # noqa: E731
        return {"rows": [pce("S1", "20.1"), pce("S2", "22.5")]}
    return {"rows": []}


def bad_extractor(body):
    from _fake import last_user_text

    if 'SHEET "Properties"' in last_user_text(body):
        pce = lambda dev, v: row(dev, "Properties", 2, "table", "Table 1", Properties="Champion PCE", Values=v, Unit="%")  # noqa: E731
        return {"rows": [pce("S1", "20.1"), pce("S2", "23.9")]}  # S2 is wrong
    return {"rows": []}


class Versus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(self.tmp / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        record = []
        for n, sheets in enumerate((record_s1(), record_s2()), 1):
            sheets["Stability"] = []
            sheets["Process"] = [r for r in sheets["Process"] if r["LayerID"] == 2]
            record.append(write_workbook(sheets, self.tmp / f"10.1234_test.5678_Sample{n}.xlsx"))
        self.pair = PaperPair("101234test5678", pdf, record)
        for name, extractor in (("a", bad_extractor), ("b", good_extractor)):
            rt, _ = runtime({"mapper": fake_mapper, "extract": extractor})
            evaluate_pairs(Extractor(rt, decide_rows=False), [self.pair], self.tmp / name)

    def test_compare_two_runs(self):
        r = compare_runs(load_run(self.tmp / "a"), load_run(self.tmp / "b"))
        self.assertEqual(r["papers"], ["101234test5678"])
        self.assertEqual(r["agree"]["only_b"], 1)  # the one value A got wrong and B got right
        self.assertEqual(r["agree"]["only_a"], 0)
        self.assertGreater(r["b"]["values"]["match_rate"], r["a"]["values"]["match_rate"])
        text = write_comparison(load_run(self.tmp / "a"), load_run(self.tmp / "b"), ("bad", "good"), self.tmp / "vs.md").read_text(encoding="utf-8")
        for part in ("# bad vs good", "## Headline", "## Cell by cell", "only good matches", "## Where they disagree", "Champion PCE"):
            self.assertIn(part, text)

    def test_rescoring_a_saved_run_needs_no_model_and_keeps_its_usage(self):
        from psc_agent.evaluate.run import rescore_paper

        before = load_run(self.tmp / "a")[0]
        ev = rescore_paper(self.pair, self.tmp / "a")
        self.assertEqual([(c.row, c.column, c.status) for c in ev.cells], [(c.row, c.column, c.status) for c in before.cells])
        self.assertEqual((ev.usage, ev.seconds), (before.usage, before.seconds))
        self.assertIsNone(rescore_paper(self.pair, self.tmp / "never_ran"))
        with redirect_stdout(io.StringIO()):  # the command does it for every selected paper, and writes the report
            code = main(["evaluate", "--raw", str(self.tmp), "--pdf", str(self.tmp), "--rescore", "--out", str(self.tmp / "a")])
        self.assertIn(code, (0, 1))
        self.assertTrue((self.tmp / "a" / "report.md").is_file())

    def test_only_papers_in_both_runs_count(self):
        a = load_run(self.tmp / "a")
        self.assertEqual(compare_runs(a, [])["papers"], [])

    def test_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["compare", str(self.tmp / "a"), str(self.tmp / "b"), "--labels", "bad", "good"])
        self.assertEqual(code, 0)
        self.assertIn("bad", out.getvalue())
        self.assertTrue((self.tmp / "b" / "vs_a.md").is_file())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["compare", str(self.tmp / "a"), str(self.tmp / "missing")]), 1)


if __name__ == "__main__":
    unittest.main()
