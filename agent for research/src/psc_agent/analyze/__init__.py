"""Workflow: key insights from the processed dataset (needs pandas).

    analyst  plan → tool loop → decision → INSIGHTS.md
    tools    DataTools: the project's within-paper method as tested tools
    datamap  the data-map skill (analysts' compass) and DataMapHook that keeps it current
"""
from .analyst import AnalysisResult, Analyst
from .datamap import DataMapHook
from .tools import DataTools

__all__ = ["AnalysisResult", "Analyst", "DataMapHook", "DataTools"]
