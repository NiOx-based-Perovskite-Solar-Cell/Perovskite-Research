"""ชั้นเรียก model — สลับ Mock <-> OpenRouter โดย agent ไม่ต้องรู้"""
from __future__ import annotations

import json
import os
from typing import Protocol


class LLM(Protocol):
    def complete(self, system: str, user: str, meta: dict) -> str: ...


class MockLLM:
    """ไม่เรียก API: คืน JSON ที่เตรียมไว้ตาม doi ใน meta (ใช้ทดสอบท่อ + compare)"""

    def __init__(self, canned: dict[str, dict]):
        self.canned = canned

    def complete(self, system: str, user: str, meta: dict) -> str:
        return json.dumps(self.canned.get(meta["doi_raw"], {"devices": []}))


class OpenRouterLLM:
    """โครงไว้ก่อน — ยังไม่ได้ใช้ใน PoC (ข้าม API key)
    endpoint เข้ากันกับ OpenAI chat completions
    """
    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.0):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.temperature = temperature

    def complete(self, system: str, user: str, meta: dict) -> str:
        if not self.api_key:
            raise RuntimeError("ไม่มี OPENROUTER_API_KEY")
        import httpx
        r = httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            },
            timeout=300,
        )
        r.raise_for_status()
        # TODO: เก็บ usage/cost ต่อเปเปอร์ไว้เทียบหลายโมเดล, retry เมื่อ JSON พัง
        return r.json()["choices"][0]["message"]["content"]
