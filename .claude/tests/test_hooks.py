"""hooks.py end to end: real subprocess, hook-shaped JSON on stdin, a throwaway project root."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _setup import HOOKS


def hook(root: Path, event: str, payload) -> tuple[int, dict | None]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    p = subprocess.run([sys.executable, "-I", str(HOOKS), event], input=data, capture_output=True, env=env, timeout=60)
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None)


class Hooks(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "app.py").write_text('"""App."""\ndef main():\n    pass\n', encoding="utf-8")
        self.trace = self.root / ".claude" / "trace" / "trace.jsonl"

    def records(self):
        return [json.loads(l) for l in self.trace.read_text(encoding="utf-8").splitlines()]

    def test_post_traces_and_rebuilds_map_on_structure_change(self):
        bash = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash",
                "tool_input": {"command": "git mv a b"}, "tool_response": {"stdout": "", "stderr": ""}}
        code, out = hook(self.root, "post", bash)
        self.assertEqual(code, 0)
        self.assertIn("REPO_MAP.md was rebuilt", out["hookSpecificOutput"]["additionalContext"])
        self.assertTrue((self.root / ".claude/skills/repo-map/REPO_MAP.md").exists())
        self.assertIsNone(hook(self.root, "post", bash)[1])  # nothing moved since
        (self.root / "app.py").rename(self.root / "main_app.py")
        out = hook(self.root, "post", bash)[1]
        self.assertIn("+main_app.py, -app.py", out["hookSpecificOutput"]["additionalContext"])
        read = {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": "..."}
        self.assertIsNone(hook(self.root, "post", read)[1])  # Read never triggers a rebuild
        recs = self.records()
        self.assertEqual([r.get("tool") for r in recs if r["event"] == "tool"], ["Bash", "Bash", "Bash", "Read"])
        self.assertEqual([r["changes"] for r in recs if r["event"] == "map"][-1], ["+main_app.py", "-app.py"])

    def test_failure_and_prompt_trace(self):
        hook(self.root, "fail", {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                                 "tool_input": {"command": "pytest"}, "error": "Exit code 2\nE   assert 1 == 2"})
        hook(self.root, "prompt", {"hook_event_name": "UserPromptSubmit", "prompt": "please fix"})
        hook(self.root, "prompt", {"hook_event_name": "UserPromptSubmit", "prompt": "<task-notification>x"})
        recs = [r for r in self.records() if r["event"] != "map"]
        self.assertEqual([(r["event"], r.get("exit")) for r in recs], [("tool", 2), ("prompt", None)])

    def test_subagent_compass(self):
        hook(self.root, "post", {"hook_event_name": "PostToolUse", "tool_name": "Write",
                                 "tool_input": {"file_path": "app.py", "content": "x"}, "tool_response": {}})
        out = hook(self.root, "subagent", {"hook_event_name": "SubagentStart", "agent_type": "repo-scout"})[1]
        self.assertIn("Repo compass", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(hook(self.root, "subagent", {"hook_event_name": "SubagentStart", "agent_type": "ac-judge"})[1])

    def test_stop_gate(self):
        self.assertIsNone(hook(self.root, "stop", {"hook_event_name": "Stop", "session_id": "s"})[1])
        (self.root / "PLAN.md").write_text("# PLAN: t\nstatus: active\n\n## Acceptance criteria\n\n### AC-1: works\n",
                                           encoding="utf-8")
        out = hook(self.root, "stop", {"hook_event_name": "Stop", "session_id": "s"})[1]
        self.assertEqual(out["decision"], "block")
        out = hook(self.root, "stop", {"hook_event_name": "Stop", "session_id": "s", "stop_hook_active": True})[1]
        self.assertIn("0/1 accepted", out["systemMessage"])

    def test_fails_open(self):
        code, out = hook(self.root, "post", b"{not json")
        self.assertEqual((code, out), (0, None))
        self.assertIn("JSONDecodeError", (self.root / ".claude/trace/hook_errors.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
