"""Ollama Cloud as a model client (``psc-agent ... --provider ollama``): hosted open models such as deepseek-v4-pro.

Ollama serves the OpenAI chat-completions format at https://ollama.com/v1 with ``Authorization: Bearer <key>``, so this
is OpenRouterClient with another address and key, and without OpenRouter's own request fields. Tool calling works,
which is what the supporting-information finder needs. Cost is not reported (the plan is flat or credit based), so
usage shows 0 USD and the report says it is not priced.
"""
from __future__ import annotations

import os

import httpx

from .env import env_key
from .openrouter import OpenRouterClient, OpenRouterError

BASE_URL = "https://ollama.com/v1"
KEY_NAMES = ("OLLAMA_CLOUD_API_KEY", "OLLAMA_API_KEY")


class OllamaCloudClient(OpenRouterClient):
    def __init__(self, api_key: str | None = None, *, base_url: str = BASE_URL, timeout: float = 600.0, max_retries: int = 4,
                 cache_dir: str | os.PathLike | None = None, transport: httpx.BaseTransport | None = None):
        key = api_key or env_key(*KEY_NAMES)
        if not key:
            raise ValueError(f"Ollama Cloud API key missing: pass api_key or set {KEY_NAMES[0]}")
        super().__init__(key, base_url=base_url, timeout=timeout, max_retries=max_retries, cache_dir=cache_dir,
                         app_title="psc-agent", transport=transport)

    def _body(self, model, messages, schema, tools, tool_choice, max_tokens, extra) -> dict:
        body = super()._body(model, messages, schema, tools, tool_choice, max_tokens, extra)
        body.pop("provider", None)  # OpenRouter routing; Ollama does not know it
        return body

    def _post(self, body: dict) -> dict:
        try:
            return super()._post(body)
        except OpenRouterError as e:
            if "HTTP 402" in str(e):
                raise OpenRouterError(f"{e} (the Ollama account has no credits or plan for cloud models: ollama.com/plans)") from e
            raise
