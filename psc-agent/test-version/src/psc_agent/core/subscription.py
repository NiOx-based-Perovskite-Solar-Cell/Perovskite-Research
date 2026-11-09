"""Claude Code on a subscription as a model client: no API key, same ``complete`` as OpenRouterClient.

Every call starts ``claude -p`` headless, with all tools, hooks and settings switched off, so the model sees
only the system prompt and the message (text and page images) and answers in plain text or, with a schema, as
validated JSON. It lets the workflows run on the Claude plan the user already has. To move to OpenRouter,
build ``Runtime`` with an ``OpenRouterClient`` instead (``psc-agent ... --provider openrouter``); nothing
else changes. Limits: Claude models only, and the cost in the usage is the API-equivalent price at list rates,
not a charge.

Tool calling is emulated: the tools are described in the system prompt and the model answers with a structured
reply, either calls to make or a final answer, which comes back in OpenAI's ``tool_calls`` shape. The agent loop
(``core.agent``) cannot tell the difference.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .openrouter import CachedClient, Completion, OpenRouterError

log = logging.getLogger(__name__)

# Variables that would make the child use an API key instead of the plan, or think it runs inside another session.
_DROP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION",
             "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET")
_TOOL_REPLY = {"name": "reply", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["tool_calls", "answer"],
    "properties": {
        "tool_calls": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "arguments"],
            "properties": {"name": {"type": "string"},
                           "arguments": {"type": "string", "description": "the arguments as one JSON object, encoded as a string"}}}},
        "answer": {"type": "string"}}}}
_TOOL_RULES = """
You can call these tools. Each reply is JSON with two fields:
- tool_calls: the calls to make now (several are allowed, they run in order); empty when you answer.
- answer: your final answer, written only when tool_calls is empty; empty otherwise.
The tools exist and work, but only through this reply: the tool_calls field IS how you call them (any other tool you
may see, such as StructuredOutput, is only the carrier of this reply, not a tool to use). Do not look for another
interface and never answer that tools are unavailable. Call a tool whenever you need data: results come back in the next message,
each labelled [Tn]. Do not guess values.

Tools:
"""
_MAX_CMDLINE = 24000  # Windows allows 32767 characters for the whole command line


class SubscriptionError(OpenRouterError):
    """The Claude Code call failed (not logged in, limit reached, bad output). Workflows treat it like a model error."""


def find_claude() -> list[str]:
    """The ``claude`` command: ``$CLAUDE_BIN``, else the real ``claude.exe`` behind the npm shim, else PATH."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return [explicit]
    found = shutil.which("claude.exe") or shutil.which("claude")
    if not found:  # not on PATH (e.g. npm's global folder of another Node version): the VS Code extension bundles one
        bundled = sorted(Path.home().glob(".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*"))
        bundled = [b for b in bundled if b.suffix.lower() in (".exe", "")]
        if bundled:
            return [str(bundled[-1])]
        raise SubscriptionError("Claude Code CLI not found: install it (npm i -g @anthropic-ai/claude-code) or set CLAUDE_BIN")
    p = Path(found)
    if p.suffix.lower() in (".cmd", ".bat", ".ps1", ""):  # npm shim: call the exe it points to, so JSON args survive
        exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return [str(exe)]
        if p.suffix.lower() in (".cmd", ".bat"):
            return ["cmd", "/c", str(p)]
    return [str(p)]


def cli_model(model: str) -> str:
    """OpenRouter id -> Claude Code id: ``anthropic/claude-sonnet-5.5`` -> ``claude-sonnet-5-5``."""
    m = model.removeprefix("anthropic/")
    if m in ("sonnet", "opus", "haiku") or re.fullmatch(r"claude-[a-z0-9-]+", m):
        return m
    if m.startswith("claude-"):
        return re.sub(r"(?<=\d)\.(?=\d)", "-", m)
    raise SubscriptionError(f"{model!r}: the Claude subscription can only run Claude models")


def _blocks(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style messages -> (system prompt, user content blocks). Earlier assistant turns (a repair retry)
    become quoted text, because a headless call takes a single user turn."""
    system, blocks = [], []
    for m in messages:
        role, content = m["role"], m.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        if role == "system":
            system += [p.get("text", "") for p in parts]
        elif role == "tool":
            body = "".join(p.get("text", "") for p in parts)
            blocks.append({"type": "text", "text": f"[Tool result {m.get('tool_call_id', '')}]\n{body}"})
        else:
            if role == "assistant":
                blocks.append({"type": "text", "text": "[Your previous reply]"})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    blocks.append({"type": "text", "text": f"[You called {fn.get('name')} with {fn.get('arguments')}]"})
            for p in parts:
                if p.get("type") == "image_url":
                    media, _, data = p["image_url"]["url"].removeprefix("data:").partition(";base64,")
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
                else:  # cache_control marks are OpenRouter's business
                    blocks.append({"type": "text", "text": p.get("text", "")})
    return "\n\n".join(system), blocks


class ClaudeCodeClient(CachedClient):
    """Thread-safe. ``effort`` is Claude Code's thinking level (low, medium, high, xhigh, max); ``max_parallel``
    caps simultaneous ``claude`` processes so a batch does not burn the plan's rate limit.
    ``executable`` replaces the auto-detected command (a list; tests point it at a stub)."""

    def __init__(self, *, effort: str = "medium", timeout: float = 1200.0, max_retries: int = 2, max_parallel: int = 3,
                 cache_dir: str | os.PathLike | None = None, executable: list[str] | None = None,
                 workdir: str | os.PathLike | None = None):
        self.effort, self.timeout, self.max_retries = effort, timeout, max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._exe = executable or find_claude()
        self._workdir = Path(workdir) if workdir else Path(os.environ.get("TEMP", "/tmp")) / "psc-agent-claude"
        self._slots = threading.BoundedSemaphore(max_parallel)

    def close(self) -> None:  # the interface of OpenRouterClient
        pass

    def complete(self, model: str, messages: list[dict], *, schema: dict | None = None, tools: list[dict] | None = None,
                 tool_choice=None, max_tokens: int = 32000, extra: dict | None = None) -> Completion:
        name = cli_model(model)
        system, blocks = _blocks(messages)
        use_tools = bool(tools) and tool_choice != "none"
        if use_tools:
            schema = _TOOL_REPLY
            system += _TOOL_RULES + "\n".join(_describe(t) for t in tools)
        schema_json = json.dumps(schema["schema"], separators=(",", ":")) if schema else None
        if len(system) + len(schema_json or "") > _MAX_CMDLINE:  # too long for a command line: send it as the first block
            blocks.insert(0, {"type": "text", "text": system})
            system = "Follow the instructions in the first part of the user message."
        key = hashlib.sha256(json.dumps([name, self.effort, system, blocks, schema_json], sort_keys=True).encode()).hexdigest()
        data = self._cache_get(key)
        cached = data is not None
        if not cached:
            data = self._run(name, system, blocks, schema_json, max_tokens)
            if use_tools:
                data = _tool_message(data)
        comp = self._completion(data, model, max_tokens, cached)
        if not cached:
            self._cache_put(key, data)
        return comp

    def _run(self, model: str, system: str, blocks: list[dict], schema_json: str | None, max_tokens: int) -> dict:
        cmd = [*self._exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--tools", "", "--setting-sources", "", "--no-session-persistence", "--model", model,
               "--effort", self.effort, "--system-prompt", system]
        if schema_json:
            cmd += ["--json-schema", schema_json]
        turn = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}) + "\n"
        env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(min(max_tokens, 64000))
        self._workdir.mkdir(parents=True, exist_ok=True)  # a neutral folder: no project CLAUDE.md or memory is picked up
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                with self._slots:
                    p = subprocess.run(cmd, input=turn, capture_output=True, text=True, encoding="utf-8", env=env,
                                       cwd=self._workdir, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                err = f"timed out after {self.timeout:.0f}s"
            except OSError as e:
                raise SubscriptionError(f"cannot start Claude Code ({self._exe[0]}): {e}") from e
            else:
                result = _final_event(p.stdout)
                if result is None:
                    err = f"no result from Claude Code (exit {p.returncode}): {(p.stderr or p.stdout)[-400:].strip()}"
                elif result.get("is_error") or result.get("subtype") != "success":
                    text = str(result.get("result") or result.get("subtype"))
                    if re.search(r"limit|login|auth|credit|quota", text, re.I):
                        raise SubscriptionError(f"Claude Code refused the call: {text[:300]}")
                    err = f"Claude Code error: {text[:300]}"
                else:
                    return _as_response(result, model, schema_json is not None, time.time() - started)
            if attempt == self.max_retries:
                raise SubscriptionError(err)
            delay = 5.0 * (attempt + 1)
            log.warning("Claude Code %s; retry %d/%d in %.0fs", err, attempt + 1, self.max_retries, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")


def _describe(tool: dict) -> str:
    fn = tool.get("function") or tool
    schema = json.dumps(fn.get("parameters") or {}, separators=(",", ":"))
    return f"- {fn.get('name')}: {fn.get('description', '')}\n  arguments (JSON schema): {schema}"


def _tool_message(data: dict) -> dict:
    """The structured reply (calls or answer) rewritten as an OpenAI assistant message."""
    msg = data["choices"][0]["message"]
    try:
        reply = json.loads(msg["content"])
        calls, answer = reply.get("tool_calls") or [], str(reply.get("answer") or "")
    except (ValueError, AttributeError):
        calls, answer = [], str(msg.get("content") or "")  # not JSON: treat the text as the final answer
    out = {"role": "assistant", "content": answer if not calls else None}
    if calls:
        out["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": c.get("name", ""), "arguments": c.get("arguments") or "{}"}}
                             for i, c in enumerate(calls, 1)]
    return {**data, "choices": [{**data["choices"][0], "message": out}]}


def _final_event(stdout: str) -> dict | None:
    last = None
    for line in stdout.splitlines():
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "result":
                last = ev
    return last


def _as_response(result: dict, model: str, structured: bool, seconds: float) -> dict:
    """The CLI's final event in OpenRouter's response shape."""
    if structured:
        out = result.get("structured_output")
        content = json.dumps(out, ensure_ascii=False) if out is not None else str(result.get("result") or "")
    else:
        content = str(result.get("result") or "")
    u = result.get("usage") or {}
    cache_read = u.get("cache_read_input_tokens") or 0
    prompt = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + cache_read
    return {"model": model,
            "choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "length" if result.get("stop_reason") == "max_tokens" else "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": u.get("output_tokens") or 0,
                      "prompt_tokens_details": {"cached_tokens": cache_read},
                      "cost": result.get("total_cost_usd") or 0.0,  # API-equivalent price, not charged
                      "subscription": True, "seconds": round(seconds, 1)}}
