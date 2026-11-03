"""Small decision model: one typed decision per claim, "state in, decision out".

Each item is {"id", "claim", "evidence"} (plus optional context such as "criterion"). The "decision"
role (give it a small, fast model) returns choice ∈ {accept, verify_more, revise} with a probability p.
The code enforces the escalation rule: p < 0.7 becomes verify_more ("escalate:"). Any typed decision
service, e.g. TypeSafe's Jev, fits the same contract.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

CHOICES = ("accept", "verify_more", "revise")
ESCALATE_BELOW = 0.7


class Verdict(BaseModel):
    id: str
    choice: Literal["accept", "verify_more", "revise"]
    p: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    by: str = ""  # model id, or "rule" for deterministic pre-checks


class _Answer(BaseModel):
    verdicts: list[Verdict]


SCHEMA = {"name": "decisions", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "choice", "p", "reason"],
        "properties": {"id": {"type": "string"}, "choice": {"type": "string", "enum": list(CHOICES)},
                       "p": {"type": "number"}, "reason": {"type": "string"}}}}}}}


def rule(item_id: str, choice: str, reason: str) -> Verdict:
    """A deterministic verdict (e.g. no evidence at all), made without calling a model."""
    return Verdict(id=item_id, choice=choice, p=1.0, reason=reason, by="rule")


def decide(rt, kind: str, items: list[dict], *, usage=None, batch: int = 40) -> dict[str, Verdict]:
    """Verdicts by item id. Items the model skips get verify_more ("no decision returned")."""
    out: dict[str, Verdict] = {}
    model = rt.model_for("decision")
    for start in range(0, len(items), batch):
        chunk = items[start: start + batch]
        text = "Decide each item:\n" + "\n".join(json.dumps(i, ensure_ascii=False, default=str) for i in chunk)
        answer = rt.ask_json("decision", text, _Answer, schema=SCHEMA, usage=usage)
        wanted = {i["id"] for i in chunk}
        for v in answer.verdicts:
            if v.id not in wanted:
                continue
            v.by = model
            if v.p < ESCALATE_BELOW and v.choice != "verify_more":
                v.reason = f"escalate: (model said {v.choice} at p={v.p:.2f}) {v.reason}"
                v.choice = "verify_more"
            out[v.id] = v
    for i in items:
        out.setdefault(i["id"], Verdict(id=i["id"], choice="verify_more", p=0.0, reason="no decision returned",
                                        by=model))
    rt.hooks.emit("decision", kind=kind, items=items, verdicts=list(out.values()))
    return out
