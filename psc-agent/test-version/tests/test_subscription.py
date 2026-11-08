"""The Claude Code subscription client, run against a stub `claude` so no plan quota is used."""
import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

from pydantic import BaseModel

from psc_agent import Runtime
from psc_agent.core.subscription import ClaudeCodeClient, SubscriptionError, cli_model

STUB = textwrap.dedent('''
    import json, os, sys
    argv = sys.argv[1:]
    arg = lambda name: argv[argv.index(name) + 1] if name in argv else None
    turn = json.loads(sys.stdin.readline())
    log = os.environ["STUB_LOG"]
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"model": arg("--model"), "effort": arg("--effort"), "system": arg("--system-prompt"),
                             "schema": arg("--json-schema"), "tools": arg("--tools"), "env_key": os.environ.get("ANTHROPIC_API_KEY"),
                             "blocks": turn["message"]["content"], "max_out": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")}) + "\\n")
    n = sum(1 for _ in open(log, encoding="utf-8"))
    mode = os.environ.get("STUB_MODE", "ok")
    if mode == "flaky" and n == 1:
        sys.stderr.write("socket hang up"); sys.exit(1)
    print(json.dumps({"type": "system", "subtype": "init"}))
    if mode == "limit":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "You've hit your usage limit"}))
        sys.exit(0)
    reply = json.loads(os.environ.get("STUB_REPLY", '{"ok": true}'))
    out = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn", "total_cost_usd": 0.0123,
           "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100, "output_tokens": 7}}
    if arg("--json-schema"):
        out["structured_output"] = reply
        out["result"] = json.dumps(reply)
    else:
        out["result"] = "plain answer"
    print(json.dumps(out))
''')

SCHEMA = {"name": "t", "strict": True, "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}


class Subscription(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "claude_stub.py").write_text(STUB, encoding="utf-8")
        self.log = self.tmp / "calls.jsonl"
        os.environ["STUB_LOG"] = str(self.log)
        os.environ["ANTHROPIC_API_KEY"] = "sk-must-not-reach-the-child"
        for k in ("STUB_MODE", "STUB_REPLY"):
            os.environ.pop(k, None)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("STUB_LOG", "STUB_MODE", "STUB_REPLY", "ANTHROPIC_API_KEY")])

    def client(self, **kw):
        return ClaudeCodeClient(executable=[sys.executable, str(self.tmp / "claude_stub.py")], workdir=self.tmp / "work",
                                cache_dir=kw.pop("cache_dir", None), **kw)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_request_shape_and_reply(self):
        msgs = [{"role": "system", "content": "You are the judge model."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                    {"type": "text", "text": "Is it ok?", "cache_control": {"type": "ephemeral"}}]}]
        comp = self.client(effort="low").complete("anthropic/claude-sonnet-5.5", msgs, schema=SCHEMA, max_tokens=5000)
        self.assertEqual(json.loads(comp.content), {"ok": True})
        self.assertEqual((comp.usage["prompt_tokens"], comp.usage["completion_tokens"], comp.usage["cost"]), (115, 7, 0.0123))
        self.assertEqual(comp.usage["prompt_tokens_details"], {"cached_tokens": 100})
        call = self.calls()[0]
        self.assertEqual((call["model"], call["effort"], call["system"], call["tools"]), ("claude-sonnet-5-5", "low", "You are the judge model.", ""))
        self.assertEqual(json.loads(call["schema"]), SCHEMA["schema"])  # the inner schema, not OpenRouter's wrapper
        self.assertEqual(call["blocks"], [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
                                          {"type": "text", "text": "Is it ok?"}])  # cache_control dropped
        self.assertIsNone(call["env_key"], "the API key must not reach the child, or it would bill the key instead of the plan")
        self.assertEqual(call["max_out"], "5000")

    def test_plain_text_without_schema(self):
        comp = self.client().complete("claude-sonnet-5-5", [{"role": "user", "content": "hi"}])
        self.assertEqual(comp.content, "plain answer")

    def test_second_identical_call_is_cached(self):
        c = self.client(cache_dir=self.tmp / "cache")
        msgs = [{"role": "user", "content": "same"}]
        first, second = c.complete("claude-sonnet-5-5", msgs, schema=SCHEMA), c.complete("claude-sonnet-5-5", msgs, schema=SCHEMA)
        self.assertEqual((first.cached, second.cached, len(self.calls())), (False, True, 1))

    def test_transient_failure_is_retried_but_a_usage_limit_is_not(self):
        os.environ["STUB_MODE"] = "flaky"
        c = self.client(max_retries=1)
        patch = mock.patch("psc_agent.core.subscription.time.sleep")  # no waiting in the test
        patch.start()
        self.addCleanup(patch.stop)
        self.assertEqual(json.loads(c.complete("claude-sonnet-5-5", [{"role": "user", "content": "x"}], schema=SCHEMA).content), {"ok": True})
        self.assertEqual(len(self.calls()), 2)
        self.log.unlink()
        os.environ["STUB_MODE"] = "limit"
        with self.assertRaisesRegex(SubscriptionError, "usage limit"):
            c.complete("claude-sonnet-5-5", [{"role": "user", "content": "y"}], schema=SCHEMA)
        self.assertEqual(len(self.calls()), 1)  # not retried

    def test_a_repair_turn_is_sent_as_one_message(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
                {"role": "assistant", "content": "{bad"}, {"role": "user", "content": "That JSON is invalid"}]
        self.client().complete("claude-sonnet-5-5", msgs, schema=SCHEMA)
        texts = [b["text"] for b in self.calls()[0]["blocks"]]
        self.assertEqual(texts, ["q", "[Your previous reply]", "{bad", "That JSON is invalid"])

    def test_what_it_refuses(self):
        c = self.client()
        with self.assertRaisesRegex(SubscriptionError, "tool calling"):
            c.complete("claude-sonnet-5-5", [{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        with self.assertRaisesRegex(SubscriptionError, "only run Claude models"):
            c.complete("openai/gpt-5", [{"role": "user", "content": "x"}])
        self.assertEqual([cli_model(m) for m in ("anthropic/claude-opus-5.5", "claude-haiku-5-5", "sonnet")],
                         ["claude-opus-5-5", "claude-haiku-5-5", "sonnet"])

    def test_it_plugs_into_runtime(self):
        class Answer(BaseModel):
            ok: bool

        rt = Runtime("anthropic/claude-sonnet-5.5", client=self.client())
        got = rt.ask_json("judge", "is it?", Answer, schema=SCHEMA)
        self.assertTrue(got.ok)
        self.assertEqual((rt.usage.calls, rt.usage.prompt_tokens, rt.usage.cost_usd), (1, 115, 0.0123))


if __name__ == "__main__":
    unittest.main()
