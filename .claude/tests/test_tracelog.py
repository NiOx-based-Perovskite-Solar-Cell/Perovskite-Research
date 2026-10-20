import json
import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401
import tracelog as t


class Records(unittest.TestCase):
    def test_redaction(self):
        s = t.redact('export OPENROUTER_API_KEY="sk-or-v1-FAKE-test-key-not-real-0000"; curl -H "Authorization: Bearer abc.def-123456"')
        self.assertNotIn("not-real", s)
        self.assertNotIn("abc.def-123456", s)
        self.assertIn("OPENROUTER_API_KEY=[REDACTED]", s)
        self.assertEqual(t.redact("max_tokens=128000"), "max_tokens=128000")  # lowercase names are not secrets

    def test_from_hook_events(self):
        post = t.from_hook({"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash",
                            "tool_input": {"command": "ls", "description": "list"},
                            "tool_response": {"stdout": "a\nb", "stderr": "warn"}, "duration_ms": 12})
        self.assertEqual((post["ok"], post["exit"], post["ms"]), (True, 0, 12))
        self.assertIn("[stderr] warn", post["out"])
        fail = t.from_hook({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                            "tool_input": {"command": "false"}, "error": "Exit code 3\nboom"})
        self.assertEqual((fail["ok"], fail["exit"]), (False, 3))
        write = t.from_hook({"hook_event_name": "PostToolUse", "tool_name": "Write",
                             "tool_input": {"file_path": "x.py", "content": "secret body"}, "tool_response": {}})
        self.assertEqual(write["input"], {"file_path": "x.py", "chars": 11})
        self.assertIsNone(write["out"])
        self.assertEqual(t.from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix it"})["text"], "fix it")
        self.assertIsNone(t.from_hook({"hook_event_name": "UserPromptSubmit", "prompt": "<task-notification>..."}))

    def test_clip_keeps_head_and_tail(self):
        out = t.clip("HEAD" + "x" * 5000 + "KeyError: 'col'", 600)
        self.assertTrue(out.startswith("HEAD") and out.endswith("KeyError: 'col'") and len(out) < 700)

    def test_append_and_load(self):
        path = Path(tempfile.mkdtemp()) / "trace.jsonl"
        for i in range(3):
            t.append(t.record(tool="Bash", inp={"command": f"echo {i}"}), path)
        t.append(None, path)
        self.assertEqual([r["input"]["command"] for r in t.load_trace(path)], ["echo 0", "echo 1", "echo 2"])


class Digest(unittest.TestCase):
    def test_signature_and_normalize(self):
        self.assertEqual(t.signature('cd "D:/x y" && PYTHONPATH=src python -m unittest discover -s tests'),
                         "python -m unittest discover")
        self.assertEqual(t.signature(t.normalize('cd "D:/x y" && python run_pipeline.py verify')),
                         "python run_pipeline.py verify")
        self.assertEqual(t.signature('SP="/tmp/a" && rm -rf "$SP/w"'), "rm")
        self.assertEqual(t.signature("python - << 'EOF'\nprint(1)\nEOF"), "python <heredoc>")

    def test_masked_exit(self):
        self.assertTrue(t.masked_exit("python a.py 2>&1 | tail -5"))
        self.assertTrue(t.masked_exit('python a.py > log; echo "exit=$?" >> log'))
        self.assertFalse(t.masked_exit("cd a && python a.py"))

    def test_transcript_and_fixes(self):
        lines = [
            {"type": "user", "sessionId": "s", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "run tests"}},
            {"type": "assistant", "sessionId": "s", "timestamp": "2026-01-01T00:00:01Z",
             "message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                      "input": {"command": "python -c \"import x; print(x['$k'])\""}}]}},
            {"type": "user", "sessionId": "s", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "is_error": True,
                 "content": "Exit code 1\nbash: line 1: syntax error: operand expected"}]}},
            {"type": "assistant", "sessionId": "s", "timestamp": "2026-01-01T00:00:02Z",
             "message": {"content": [{"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "python -c 1"}}]}},
            {"type": "user", "sessionId": "s", "toolUseResult": {"stdout": "fine", "stderr": ""},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "is_error": False, "content": "fine"}]}},
        ]
        path = Path(tempfile.mkdtemp()) / "s.jsonl"
        path.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
        recs = t.load_transcript(path)
        self.assertEqual([r["event"] for r in recs], ["prompt", "tool", "tool"])
        self.assertEqual((recs[1]["ok"], recs[1]["exit"], recs[2]["out"]), (False, 1, "fine"))
        d = t.digest(recs)
        self.assertEqual(d["commands"][0]["signature"], "python -c")
        self.assertEqual((d["commands"][0]["ok"], d["commands"][0]["fail"]), (1, 1))
        self.assertIn("syntax error", d["fixes"][0]["failed"]["error"])


if __name__ == "__main__":
    unittest.main()
