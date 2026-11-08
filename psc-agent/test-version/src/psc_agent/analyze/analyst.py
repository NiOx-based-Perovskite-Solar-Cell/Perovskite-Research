"""Analyze the whole dataset for key insights: plan → investigate → decide → report (needs pandas).

    planner   (Big model)    goal + data map → questions, each with an acceptance criterion (AC)
    analyst   (tool loop)    answers one AC with the data tools; every number cites a tool result [Tn]
    decision  (small model)  accept / verify_more / revise for the claim against the results it cites;
                             a rule rejects claims that cite nothing; verify_more / revise send the
                             analyst back with the reason (``rounds``)
    reporter  (Big model)    INSIGHTS.md from accepted claims only

Writes to ``out_dir``: PLAN.md (each AC bound to its evidence file and verdict), evidence/AC-n.json (every
tool call, claim and verdict of every round), INSIGHTS.md and result.json.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.agent import ToolCall, run_tools
from ..core.decision import Verdict, decide, rule
from ..core.hooks import HookBlock
from ..core.openrouter import OpenRouterError
from ..core.runtime import Runtime, Usage, parse_json
from .tools import DataTools

log = logging.getLogger(__name__)
FAILURES = (OpenRouterError, ValueError, HookBlock)


class Question(BaseModel):
    id: str = ""
    question: str
    criterion: str
    tools: list[str] = []


class PlanAnswer(BaseModel):
    notes: str = ""
    questions: list[Question]


class Claim(BaseModel):
    claim: str
    numbers: dict[str, Any] = {}
    evidence_calls: list[str] = []
    caveats: list[str] = []


class ACResult(BaseModel):
    id: str
    question: str
    criterion: str
    claim: Claim | None = None
    verdict: Verdict
    rounds: int = 0
    evidence_file: str = ""


class AnalysisResult(BaseModel):
    goal: str
    notes: str = ""
    acs: list[ACResult] = Field(default_factory=list)
    report: str = ""
    usage: Usage = Field(default_factory=Usage)
    errors: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ac in self.acs:
            out[ac.verdict.choice] = out.get(ac.verdict.choice, 0) + 1
        return out


def _strict(props: dict, required: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": required}


PLAN_SCHEMA = {"name": "analysis_plan", "strict": True, "schema": _strict({
    "notes": {"type": "string"},
    "questions": {"type": "array", "items": _strict({
        "id": {"type": "string"}, "question": {"type": "string"}, "criterion": {"type": "string"},
        "tools": {"type": "array", "items": {"type": "string"}}}, ["id", "question", "criterion", "tools"])}},
    ["notes", "questions"])}

CLAIM_SCHEMA = {"name": "claim", "strict": True, "schema": _strict({
    "claim": {"type": "string"},
    "numbers": {"type": "array", "items": _strict({"name": {"type": "string"}, "value": {"type": "string"}},
                                                  ["name", "value"])},
    "evidence_calls": {"type": "array", "items": {"type": "string"}},
    "caveats": {"type": "array", "items": {"type": "string"}}}, ["claim", "numbers", "evidence_calls", "caveats"])}


class _ClaimRepair(BaseModel):  # strict-schema form of Claim (numbers as a list)
    claim: str
    numbers: list[dict[str, str]] = []
    evidence_calls: list[str] = []
    caveats: list[str] = []

    def to_claim(self) -> Claim:
        return Claim(claim=self.claim, numbers={n.get("name", ""): n.get("value") for n in self.numbers},
                     evidence_calls=self.evidence_calls, caveats=self.caveats)


def _numbers_missing(numbers: dict, evidence: str) -> list[str]:
    """Numbers of the claim that appear nowhere in the cited results (a hint for the decision model)."""
    missing = []
    for name, value in numbers.items():
        for token in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", str(value)):
            if token.lstrip("-") not in evidence:
                missing.append(f"{name}={value}")
                break
    return missing


class Analyst:
    """``tools``: a DataTools over the processed tables. ``rounds``: investigation attempts per question."""

    def __init__(self, runtime: Runtime, tools: DataTools, *, max_questions: int = 6, max_steps: int = 12,
                 rounds: int = 2):
        self.rt, self.data = runtime, tools
        self.tools = tools.tools()
        self.max_questions, self.max_steps, self.rounds = max_questions, max_steps, rounds

    def run(self, goal: str, out_dir) -> AnalysisResult:
        out = Path(out_dir)
        (out / "evidence").mkdir(parents=True, exist_ok=True)
        run = {"kind": "analyze", "goal": goal, "out": str(out)}
        self.rt.hooks.emit("run_start", run=run, runtime=self.rt)
        result = AnalysisResult(goal=goal)
        try:
            plan = self._plan(goal, result.usage)
        except FAILURES as e:
            result.errors.append(f"plan: {e}")
            plan = PlanAnswer(questions=[])
        result.notes = plan.notes
        questions = plan.questions[: self.max_questions]
        for i, q in enumerate(questions):
            self.rt.hooks.emit("progress", scope="analyze", done=i, total=len(questions) + 1)  # +1: the report
            result.acs.append(self._answer(q, out, result))
        self.rt.hooks.emit("progress", scope="analyze", done=len(questions), total=len(questions) + 1)
        try:
            result.report = self._report(goal, result)
        except FAILURES as e:
            result.errors.append(f"report: {e}")
        (out / "PLAN.md").write_text(render_plan(result), encoding="utf-8", newline="\n")
        (out / "INSIGHTS.md").write_text(result.report or "(no report: see result.json errors)\n", encoding="utf-8",
                                         newline="\n")
        (out / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        self.rt.hooks.emit("run_end", run=run, summary={"verdicts": result.counts(), "errors": len(result.errors),
                                                        "cost_usd": round(result.usage.cost_usd, 4)})
        return result

    def _plan(self, goal: str, usage: Usage) -> PlanAnswer:
        content = (f"Goal: {goal}\n\nTables: {json.dumps(self.data.list_tables(), ensure_ascii=False)}\n\n"
                   f"Plan at most {self.max_questions} questions. Return JSON: notes, questions "
                   "[{id, question, criterion, tools}].")
        plan = self.rt.ask_json("planner", content, PlanAnswer, schema=PLAN_SCHEMA, usage=usage)
        for i, q in enumerate(plan.questions, 1):
            q.id = f"AC-{i}"
        return plan

    def _answer(self, q: Question, out: Path, result: AnalysisResult) -> ACResult:
        calls: dict[str, ToolCall] = {}
        history, feedback, claim = [], None, None
        verdict = rule(q.id, "verify_more", "not investigated")
        rounds = 0
        for rounds in range(1, self.rounds + 1):
            try:
                claim, new = self._investigate(q, feedback, result.usage, len(calls) + 1)
            except FAILURES as e:
                result.errors.append(f"{q.id}: {e}")
                verdict = rule(q.id, "verify_more", f"investigation failed: {e}")
                break
            calls.update({c.id: c for c in new})
            verdict = self._decide(q, claim, calls, result)
            history.append({"round": rounds, "calls": [asdict(c) for c in new], "claim": claim.model_dump(),
                            "verdict": verdict.model_dump()})
            if verdict.choice == "accept":
                break
            feedback = (f"The decision model said {verdict.choice} (p={verdict.p:.2f}): {verdict.reason}. "
                        "Address this and answer again; earlier results keep their [T] ids.")
        path = out / "evidence" / f"{q.id}.json"
        path.write_text(json.dumps({"question": q.question, "criterion": q.criterion, "rounds": history},
                                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return ACResult(id=q.id, question=q.question, criterion=q.criterion, claim=claim, verdict=verdict,
                        rounds=rounds, evidence_file=f"evidence/{q.id}.json")

    def _investigate(self, q: Question, feedback: str | None, usage: Usage, first_id: int):
        text = (f"{q.id}: {q.question}\nAcceptance criterion: {q.criterion}\n"
                f"Suggested tools: {', '.join(q.tools) or 'any'}")
        if feedback:
            text += f"\n\n{feedback}"
        messages = [{"role": "system", "content": self.rt.system("analyst")}, {"role": "user", "content": text}]
        loop = run_tools(self.rt, "analyst", messages, self.tools, max_steps=self.max_steps, usage=usage,
                         first_id=first_id)
        try:
            claim = Claim.model_validate(parse_json(loop.final))
        except ValueError:
            claim = self.rt.ask_json("analyst", "Rewrite this answer as JSON with claim, numbers (name/value pairs), "
                                                f"evidence_calls and caveats:\n\n{loop.final}",
                                     _ClaimRepair, schema=CLAIM_SCHEMA, usage=usage).to_claim()
        return claim, loop.calls

    def _decide(self, q: Question, claim: Claim, calls: dict[str, ToolCall], result: AnalysisResult) -> Verdict:
        if not claim.evidence_calls:
            return rule(q.id, "verify_more", "the claim cites no tool results")
        missing = [c for c in claim.evidence_calls if c not in calls]
        if missing:
            return rule(q.id, "verify_more", f"cited results do not exist: {', '.join(missing)}")
        cited = [calls[c] for c in claim.evidence_calls]
        if not any(c.ok for c in cited):
            return rule(q.id, "verify_more", "every cited tool call failed")
        evidence = "\n".join(f"[{c.id}] {c.name}({json.dumps(c.args, ensure_ascii=False)}) -> {c.result[:4000]}"
                             for c in cited)
        item = {"id": q.id, "criterion": q.criterion, "claim": claim.claim, "numbers": claim.numbers,
                "caveats": claim.caveats, "evidence": evidence,
                "numbers_not_found_in_evidence": _numbers_missing(claim.numbers, evidence)}
        try:
            return decide(self.rt, "analysis", [item], usage=result.usage)[q.id]
        except FAILURES as e:
            result.errors.append(f"{q.id} decision: {e}")
            return rule(q.id, "verify_more", f"decision model failed: {e}")

    def _report(self, goal: str, result: AnalysisResult) -> str:
        acs = [{"id": a.id, "question": a.question, "decision": a.verdict.choice, "p": a.verdict.p,
                "reason": a.verdict.reason, "claim": a.claim.claim if a.claim else None,
                "numbers": a.claim.numbers if a.claim else {}, "caveats": a.claim.caveats if a.claim else []}
               for a in result.acs]
        content = json.dumps({"goal": goal, "planner_notes": result.notes, "acceptance_criteria": acs},
                             ensure_ascii=False, indent=1)
        comp = self.rt.complete("reporter", [{"role": "system", "content": self.rt.system("reporter")},
                                             {"role": "user", "content": content}], usage=result.usage)
        return comp.content


def render_plan(result: AnalysisResult) -> str:
    accepted = sum(a.verdict.choice == "accept" for a in result.acs)
    lines = [f"# PLAN: {result.goal}", f"status: {'done' if result.acs and accepted == len(result.acs) else 'open'} "
             f"({accepted}/{len(result.acs)} accepted)", "", "## Acceptance criteria", ""]
    for a in result.acs:
        v = a.verdict
        lines += [f"### {a.id}: {a.question}", f"criterion: {a.criterion}",
                  f"claim: {a.claim.claim if a.claim else '(none)'}", f"evidence: {a.evidence_file}",
                  f"verdict: {v.choice} p={v.p:.2f} by={v.by} rounds={a.rounds} | {v.reason}", ""]
    if result.notes:
        lines += ["## Planner notes", "", result.notes, ""]
    if result.errors:
        lines += ["## Errors", ""] + [f"- {e}" for e in result.errors] + [""]
    return "\n".join(lines)
