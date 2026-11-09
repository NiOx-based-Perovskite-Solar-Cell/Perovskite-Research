"""Minimal OpenRouter chat-completions client: retries, on-disk response cache, usage and cost."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenRouterError(RuntimeError):
    """The request failed, returned no content, or was cut off by max_tokens."""


@dataclass
class Completion:
    content: str
    model: str  # model that actually answered
    usage: dict  # OpenRouter usage object (tokens, cost in USD)
    cached: bool  # served from the local cache, nothing billed
    tool_calls: list[dict] = field(default_factory=list)  # OpenAI shape: id, function.name, function.arguments
    message: dict = field(default_factory=dict)  # the assistant message, to append to the history


class CachedClient:
    """What every model client shares: the on-disk response cache and the reply -> Completion step.

    A client answers ``complete(model, messages, *, schema, tools, tool_choice, max_tokens, extra)``;
    ``Runtime`` only needs that method, so OpenRouter and the Claude Code subscription are interchangeable.
    Replies are kept in OpenRouter's response shape (``choices[0].message``, ``usage``), also in the cache.
    """

    cache_dir: Path | None = None

    def _completion(self, data: dict, model: str, max_tokens: int, cached: bool) -> Completion:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        tool_calls = message.get("tool_calls") or []
        finish = choice.get("finish_reason")
        if finish == "length":
            raise OpenRouterError(f"{model}: output hit max_tokens={max_tokens}; raise it or send fewer rows")
        if not content and not tool_calls:
            raise OpenRouterError(f"{model}: empty response (finish_reason={finish})")
        usage = data.get("usage") or {}
        log.info("%s: %s prompt + %s completion tokens, $%.4f%s", data.get("model", model),
                 usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost") or 0,
                 " (cache)" if cached else "")
        assistant = {"role": "assistant", "content": content or None}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return Completion(content or "", data.get("model", model), usage, cached, tool_calls, assistant)

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None

    def _cache_get(self, key: str) -> dict | None:
        p = self._cache_path(key)
        if p is None or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_put(self, key: str, data: dict) -> None:
        p = self._cache_path(key)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)


class OpenRouterClient(CachedClient):
    """Thread-safe; share one instance across workers.

    ``api_key`` defaults to ``$OPENROUTER_API_KEY``. With ``cache_dir`` set, identical requests
    are answered from disk, so re-running a comparison after a code change costs nothing.
    ``transport`` is passed to httpx (tests use ``httpx.MockTransport``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 600.0,
        max_retries: int = 4,
        cache_dir: str | os.PathLike | None = None,
        app_title: str = "psc-agent",
        transport: httpx.BaseTransport | None = None,
    ):
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OpenRouter API key missing: pass api_key or set OPENROUTER_API_KEY")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {key}", "X-Title": app_title},
        )
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        schema: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int = 32000,
        extra: dict | None = None,
    ) -> Completion:
        """One chat completion. ``schema`` = ``{"name", "strict", "schema"}`` for structured output;
        ``tools`` = OpenAI-style function specs (send them on every call of a tool loop). Either one
        routes the request only to providers that support it. ``extra`` is merged into the body
        (e.g. ``{"reasoning": {"effort": "low"}}`` or ``{"provider": {"order": [...]}}``)."""
        body = self._body(model, messages, schema, tools, tool_choice, max_tokens, extra)
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        data = self._cache_get(key)
        cached = data is not None
        if not cached:
            data = self._post(body)
        comp = self._completion(data, model, max_tokens, cached)
        if not cached:
            self._cache_put(key, data)
        return comp

    def _body(self, model, messages, schema, tools, tool_choice, max_tokens, extra) -> dict:
        body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        if schema is not None or tools:
            body["provider"] = {"require_parameters": True}  # OpenRouter: route only to providers that support them
        for k, v in (extra or {}).items():
            body[k] = {**body[k], **v} if isinstance(v, dict) and isinstance(body.get(k), dict) else v
        return body

    def _post(self, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                r = self._http.post("/chat/completions", json=body)
            except httpx.TransportError as e:  # connection errors and timeouts
                err = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        data = {"error": {"code": 502, "message": f"non-JSON body: {r.text[:200]}"}}
                    if "error" not in data:
                        return data
                    # errors raised after the provider started answering arrive as 200 + error
                    e = data["error"] if isinstance(data["error"], dict) else {"message": data["error"]}
                    err = f"provider error {e.get('code')}: {e.get('message')}"
                    if not (isinstance(e.get("code"), int) and (e["code"] in _RETRY_STATUS or e["code"] >= 500)):
                        raise OpenRouterError(err)
                elif r.status_code in _RETRY_STATUS:
                    err = f"HTTP {r.status_code}: {r.text[:300]}"
                    retry_after = r.headers.get("retry-after")
                else:
                    raise OpenRouterError(f"HTTP {r.status_code}: {r.text[:500]}")
            if attempt == self.max_retries:
                raise OpenRouterError(err)
            delay = min(60.0, 2**attempt + random.random())
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning("OpenRouter %s; retry %d/%d in %.0fs", err, attempt + 1, self.max_retries, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")
