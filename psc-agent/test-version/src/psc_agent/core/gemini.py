"""Google Gemini as a model client: whole PDFs in, JSON that follows the schema out.

Same ``complete`` as OpenRouterClient, so ``Runtime`` can use it (``psc-agent ... --provider gemini``). Differences:

- PDFs go in whole: a message part ``{"type": "file", "file": {"file_data": "data:application/pdf;base64,..."}}``
  (what ``paper_parts(pdf=True)`` builds) becomes Gemini ``inline_data``, so a paper is never cut into pages.
- Structured output uses Gemini's ``responseSchema``. It accepts only a subset of JSON Schema, so ``to_gemini_schema``
  rewrites ours: no ``additionalProperties``, ``"type": ["string", "null"]`` becomes ``nullable``, enums stay only on
  strings, ``$ref`` is inlined.
- Thinking tokens are billed as output and counted in ``completion_tokens``. ``effort`` (low, medium, high) maps to
  ``thinkingLevel``; without it the model's default is used.
- The key goes in the ``x-goog-api-key`` header, never in the URL. No tool calling here: use OpenRouter for the analyst.
- ``price`` = (USD per million input tokens, per million output tokens) turns usage into a cost; without it the cost
  is 0 (the free tier costs nothing and prices change), and the report says so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from pathlib import Path

import httpx

from .env import env_key
from .openrouter import CachedClient, Completion, OpenRouterError

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
KEY_NAMES = ("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
_RETRY_STATUS = {408, 500, 502, 503, 504}
_MAX_OUTPUT = 65536
_THINKING = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
# the only keys copied as they are; everything else of ours (additionalProperties, strict, title, default, ...) is left out
_KEEP = {"description", "format", "minItems", "maxItems", "minimum", "maximum"}


class GeminiError(OpenRouterError):
    """The Gemini call failed, was refused, or returned nothing usable."""


# ---------------------------------------------------------------- schema

def to_gemini_schema(schema: dict, _defs: dict | None = None) -> dict:
    """Our JSON Schema (OpenRouter's ``{"name", "strict", "schema"}`` wrapper is accepted too) -> Gemini ``responseSchema``."""
    if "schema" in schema and isinstance(schema["schema"], dict) and "type" not in schema:
        schema = schema["schema"]
    defs = _defs if _defs is not None else {**schema.get("definitions", {}), **schema.get("$defs", {})}
    return _convert(schema, defs)


def _convert(s, defs: dict) -> dict:
    if not isinstance(s, dict):
        return s
    if "$ref" in s:
        target = defs.get(str(s["$ref"]).rsplit("/", 1)[-1])
        if target is None:
            raise GeminiError(f"schema reference not found: {s['$ref']}")
        return _convert({**target, **{k: v for k, v in s.items() if k != "$ref"}}, defs)
    out: dict = {}
    nullable = bool(s.get("nullable"))
    t = s.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        nullable |= len(non_null) != len(t)
        t = non_null[0] if len(non_null) == 1 else None
    if "anyOf" in s:
        options = [o for o in s["anyOf"] if o != {"type": "null"}]
        nullable |= len(options) != len(s["anyOf"])
        if len(options) == 1:
            return _convert({**{k: v for k, v in s.items() if k != "anyOf"}, **options[0], "nullable": nullable or None}, defs)
        out["anyOf"] = [_convert(o, defs) for o in options]
    if t:
        out["type"] = str(t).upper()
    for k in _KEEP:
        if k in s:
            out[k] = s[k]
    if "enum" in s:
        values = [v for v in s["enum"] if v is not None]
        nullable |= len(values) != len(s["enum"])
        if t == "string":  # Gemini enums are strings only
            out["enum"] = [str(v) for v in values]
    if "properties" in s:
        out["properties"] = {k: _convert(v, defs) for k, v in s["properties"].items()}
        out["propertyOrdering"] = list(s["properties"])
        req = [k for k in s.get("required", []) if k in s["properties"]]
        if req:
            out["required"] = req
    if "items" in s:
        out["items"] = _convert(s["items"], defs)
    if nullable:
        out["nullable"] = True
    return out


# ---------------------------------------------------------------- messages

_DATA_URL = re.compile(r"^data:([\w.+/-]+);base64,(.*)$", re.S)


def _part(p) -> dict | None:
    """One OpenAI-style content part -> a Gemini part (None for parts that carry nothing)."""
    if isinstance(p, str):
        return {"text": p} if p else None
    kind = p.get("type")
    if kind == "text":
        return {"text": p.get("text", "")}
    url = (p.get("image_url") or {}).get("url") if kind == "image_url" else (p.get("file") or {}).get("file_data")
    m = _DATA_URL.match(url or "")
    if not m:
        raise GeminiError(f"unsupported message part ({kind}): only base64 data URLs can be sent")
    return {"inline_data": {"mime_type": m.group(1), "data": m.group(2)}}


def to_contents(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style messages -> (system prompt, Gemini ``contents``)."""
    system, contents = [], []
    for m in messages:
        role, content = m["role"], m.get("content")
        if role == "tool" or m.get("tool_calls"):
            raise GeminiError("tool calling is not supported by the Gemini client; use --provider openrouter")
        parts = [content] if isinstance(content, str) else (content or [])
        converted = [x for x in (_part(p) for p in parts) if x]
        if role == "system":
            system += [x["text"] for x in converted if "text" in x]
        elif converted:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": converted})
    return "\n\n".join(system), contents


# ---------------------------------------------------------------- client

class GeminiClient(CachedClient):
    """Thread-safe. ``api_key`` defaults to ``$GOOGLE_GEMINI_API_KEY`` (also ``$GEMINI_API_KEY``, ``$GOOGLE_API_KEY``)."""

    def __init__(self, api_key: str | None = None, *, base_url: str = BASE_URL, timeout: float = 600.0, max_retries: int = 4,
                 effort: str | None = None, price: tuple[float, float] | None = None,
                 cache_dir: str | os.PathLike | None = None, transport: httpx.BaseTransport | None = None):
        key = api_key or env_key(*KEY_NAMES)
        if not key:
            raise ValueError(f"Gemini API key missing: pass api_key or set {KEY_NAMES[0]}")
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport, headers={"x-goog-api-key": key})
        self.max_retries, self.effort, self.price = max_retries, effort, price
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def close(self) -> None:
        self._http.close()

    def complete(self, model: str, messages: list[dict], *, schema: dict | None = None, tools: list[dict] | None = None,
                 tool_choice=None, max_tokens: int = 32000, extra: dict | None = None) -> Completion:
        if tools:
            raise GeminiError("tool calling is not supported by the Gemini client; use --provider openrouter")
        name = model.removeprefix("models/").removeprefix("google/")
        system, contents = to_contents(messages)
        config: dict = {"maxOutputTokens": min(max_tokens, _MAX_OUTPUT)}
        if schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = to_gemini_schema(schema)
        if self.effort in _THINKING:
            config["thinkingConfig"] = {"thinkingLevel": _THINKING[self.effort]}
        body: dict = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        key = hashlib.sha256(json.dumps([name, body], sort_keys=True).encode()).hexdigest()
        data = self._cache_get(key)
        cached = data is not None
        if not cached:
            data = self._as_response(self._post(name, body), name)
        comp = self._completion(data, name, max_tokens, cached)
        if not cached:
            self._cache_put(key, data)
        return comp

    # -- HTTP
    def _post(self, model: str, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            delay = min(60.0, 2**attempt + random.random())
            try:
                r = self._http.post(f"/models/{model}:generateContent", json=body)
            except httpx.TransportError as e:
                err = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError:
                        err = f"non-JSON body: {r.text[:200]}"
                else:
                    message, retry_in, daily = _error_info(r)
                    if r.status_code == 429 and not daily:
                        err = f"HTTP 429: {message}"
                        delay = max(delay, min(retry_in or 0.0, 120.0))
                    elif r.status_code in _RETRY_STATUS:
                        err = f"HTTP {r.status_code}: {message}"
                    else:
                        hint = " (no quota for this model on this key: the free tier has no Pro models)" if r.status_code == 429 else ""
                        raise GeminiError(f"HTTP {r.status_code}: {message}{hint}")
            if attempt == self.max_retries:
                raise GeminiError(err)
            log.warning("Gemini %s; retry %d/%d in %.0fs", err, attempt + 1, self.max_retries, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")

    # -- reply -> OpenRouter's response shape
    def _as_response(self, data: dict, model: str) -> dict:
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(f"{model}: no answer" + (f" (the prompt was blocked: {block})" if block else ""))
        cand = cands[0]
        reason = cand.get("finishReason")
        if reason not in (None, "STOP", "MAX_TOKENS"):
            raise GeminiError(f"{model}: answer stopped with {reason}")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata") or {}
        prompt = u.get("promptTokenCount") or 0
        out_tokens = (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)
        cost = (prompt * self.price[0] + out_tokens * self.price[1]) / 1e6 if self.price else 0.0
        return {"model": data.get("modelVersion") or model,
                "choices": [{"message": {"role": "assistant", "content": text},
                             "finish_reason": "length" if reason == "MAX_TOKENS" else "stop"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": out_tokens,
                          "prompt_tokens_details": {"cached_tokens": u.get("cachedContentTokenCount") or 0},
                          "thinking_tokens": u.get("thoughtsTokenCount") or 0, "cost": cost, "priced": self.price is not None}}


def _error_info(r: httpx.Response) -> tuple[str, float | None, bool]:
    """(message, seconds to wait before retrying, the quota is a daily one) from an error response."""
    try:
        err = r.json().get("error", {})
    except ValueError:
        return r.text[:300], None, False
    message = str(err.get("message", ""))[:300].replace("\n", " ")
    retry_in, daily = None, "perday" in json.dumps(err).replace("_", "").replace(" ", "").lower()
    for d in err.get("details", []) or []:
        m = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
        if m:
            retry_in = float(m.group(1))
    return message, retry_in, daily
