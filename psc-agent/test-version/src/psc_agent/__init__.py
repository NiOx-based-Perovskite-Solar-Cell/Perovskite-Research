"""Research agent for perovskite solar cell papers, steered by skills and hooks.

    from psc_agent import Runtime, Rechecker, Extractor, TraceHook, QuoteCheckHook
    rt = Runtime({"default": "anthropic/claude-sonnet-5.5", "decision": "anthropic/claude-haiku-5.5"},
                 skills_dirs=["skills"], hooks=[TraceHook("trace.jsonl"), QuoteCheckHook()])
    Rechecker(runtime=rt).recheck("paper.pdf", ["x_Sample1.xlsx"]).model_dump()
    Extractor(rt).extract("new_paper.pdf", "out/")
    Analyst(rt, DataTools("processed/prepared_v2")).run("what drives PCE gains?", "out/")   # needs pandas

Layout: core/ (engine: models, skills, hooks, evidence, tool loop, decision, learn), sources/ (papers,
workbooks, pairing), recheck/ · extract/ · analyze/ (the three workflows), web/ (Flask service),
skills/ (default SKILL.md files). Everything public is importable from here.
"""
from .core.agent import Tool, ToolCall, ToolError, run_tools
from .core.decision import Verdict, decide
from .core.hooks import BudgetHook, HookBlock, Hooks, QuoteCheckHook, TraceHook
from .core.openrouter import OpenRouterClient, OpenRouterError
from .core.runtime import Runtime, Usage
from .core.skills import Skill, SkillBook
from .extract import ExtractResult, Extractor
from .recheck import STATUSES, DeviceInfo, Finding, PaperReport, Rechecker, recheck_folders
from .recheck.compare import compare_values
from .sources.pairing import pair_folders
from .sources.paper import Paper, load_paper
from .sources.workbook import Device, load_workbook, write_workbook

__version__ = "0.2.0"
_NEEDS_PANDAS = {"DataTools": "analyze", "Analyst": "analyze", "AnalysisResult": "analyze", "DataMapHook": "analyze"}


def __getattr__(name):  # the analysis workflow needs pandas: load it on first use only
    if name in _NEEDS_PANDAS:
        import importlib

        return getattr(importlib.import_module(f".{_NEEDS_PANDAS[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "STATUSES", "AnalysisResult", "Analyst", "BudgetHook", "DataMapHook", "DataTools", "Device", "DeviceInfo",
    "ExtractResult", "Extractor", "Finding", "HookBlock", "Hooks", "OpenRouterClient", "OpenRouterError", "Paper",
    "PaperReport", "QuoteCheckHook", "Rechecker", "Runtime", "Skill", "SkillBook", "Tool", "ToolCall", "ToolError",
    "TraceHook", "Usage", "Verdict", "compare_values", "decide", "load_paper", "load_workbook", "pair_folders",
    "recheck_folders", "run_tools", "write_workbook",
]
