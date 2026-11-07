"""A fake OpenRouter that answers by role: the role is read from the first line of the system prompt
("You are the <role> model ..."), which SkillBook.prompt always writes."""
from __future__ import annotations

import json
import re

import httpx

from psc_agent import OpenRouterClient, Runtime


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeRouter:
    def __init__(self, responders: dict):
        self.responders = responders  # role -> fn(body) returning a dict / str / {"tool_calls": [...]}
        self.requests: list[dict] = []

    @staticmethod
    def role_of(body: dict) -> str:
        system = next((m["content"] for m in body["messages"] if m["role"] == "system"), "")
        m = re.match(r"You are the (\w+) model", system if isinstance(system, str) else "")
        return m.group(1) if m else "?"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        reply = self.responders[self.role_of(body)](body)
        if isinstance(reply, dict) and "tool_calls" in reply:
            message, finish = {"role": "assistant", "content": None, "tool_calls": reply["tool_calls"]}, "tool_calls"
        else:
            message = {"role": "assistant", "content": reply if isinstance(reply, str) else json.dumps(reply)}
            finish = "stop"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": message, "finish_reason": finish}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.001}})

    def by_role(self, role: str) -> list[dict]:
        return [b for b in self.requests if self.role_of(b) == role]


def runtime(responders: dict, *, models=None, hooks=(), skills_dirs=(), cache_dir=None):
    fake = FakeRouter(responders)
    client = OpenRouterClient("sk-test-key-0000000000", transport=httpx.MockTransport(fake), max_retries=0,
                              cache_dir=cache_dir)
    rt = Runtime(models or {"default": "big/model", "decision": "small/model"}, client=client, hooks=hooks,
                 skills_dirs=skills_dirs)
    return rt, fake


def last_user_text(body: dict) -> str:
    for m in reversed(body["messages"]):
        if m["role"] == "user":
            c = m["content"]
            return c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""
