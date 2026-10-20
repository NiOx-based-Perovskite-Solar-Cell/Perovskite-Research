"""Data map: the analysts' compass, written as the generated skill ``data-map``.

It lists the tables (rows, columns, papers) and, for the main table, every column with its role, coverage
(devices and papers with a value) and meaning, taken from processed/eda/column_catalog.csv when it exists.
The front matter holds a fingerprint of the data files; DataMapHook rebuilds the map at the start of a run
whenever a file was added, moved, removed or changed (needs pandas).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..core.skills import parse_skill
from .tools import DataTools

NAME = "data-map"


def fingerprint(data_dir, catalog=None) -> str:
    h = hashlib.sha1()
    files = sorted(Path(data_dir).glob("*.csv")) + ([Path(catalog)] if catalog and Path(catalog).is_file() else [])
    for p in files:
        st = p.stat()
        h.update(f"{p.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def build(data_dir, catalog=None, main_table: str = "target_table", treatment: str = "htl_mod_kind") -> str:
    tools = DataTools(data_dir, catalog=catalog, main_table=main_table, treatment=treatment)
    overview = tools.list_tables()["tables"]
    lines = ["---", f"name: {NAME}",
             "description: Compass of the analysis data (generated) - tables, columns with role, coverage and meaning",
             "roles: [planner, analyst, reporter, decision]", "order: 15",
             f"fingerprint: {fingerprint(data_dir, catalog or _default_catalog(data_dir))}", "---",
             f"Data folder `{Path(data_dir).as_posix()}`: {len(overview)} tables. Main table `{main_table}` has one row "
             f"per device. The tools add `paper_key` (DOI key) to every table with device_id. Treatment `{treatment}`: "
             f"empty or False marks the control device of its paper.",
             "Read this before calling tools. Before relying on a column, confirm it with describe_table. Columns "
             "marked ⛔ in their meaning are excluded on purpose: do not base claims on them. Counts below are live; "
             "meanings come from the column catalog and may predate the current data.", "", "## Tables"]
    lines += [f"- {t['table']}: {t['rows']} rows, {t['columns']} columns" +
              (f", {t['papers']} papers" if t["papers"] is not None else "") for t in overview]
    if main_table in tools.tables():
        info = tools.describe_table(main_table)["columns"]
        df = tools.table(main_table)
        lines += ["", f"## {main_table} columns (filled devices / {len(df)} · papers with a value · meaning)"]
        by_role: dict[str, list[str]] = {}
        for c in info:
            role, meaning = tools.catalog.get((main_table, c["column"]), ("other", ""))
            meaning = re.sub(r"\s+", " ", meaning).strip()
            text = f"- `{c['column']}` {c['type']} · {c['filled']} · {c.get('papers', '?')} papers"
            by_role.setdefault(role or "other", []).append(text + (f" — {meaning[:110]}" if meaning else ""))
        for role, items in by_role.items():
            lines += [f"### {role}", *items]
    others = [t["table"] for t in overview if t["table"] != main_table]
    if others:
        lines += ["", "## Other tables (columns)"]
        for t in others:
            cols = [str(c) for c in tools.table(t).columns]
            lines.append(f"- {t}: {', '.join(cols[:40])}" + (f", ... (+{len(cols) - 40})" if len(cols) > 40 else ""))
    return "\n".join(lines) + "\n"


def _default_catalog(data_dir) -> Path | None:
    p = Path(data_dir).parent / "eda" / "column_catalog.csv"
    return p if p.is_file() else None


def refresh(data_dir, skills_dir, catalog=None, main_table: str = "target_table", treatment: str = "htl_mod_kind",
            force: bool = False) -> bool:
    """Write ``<skills_dir>/data-map/SKILL.md`` when missing or stale. Returns True when it was (re)built."""
    target = Path(skills_dir) / NAME / "SKILL.md"
    current = fingerprint(data_dir, catalog or _default_catalog(data_dir))
    if not force and target.is_file() and f"fingerprint: {current}" in target.read_text(encoding="utf-8")[:600]:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(data_dir, catalog, main_table, treatment), encoding="utf-8", newline="\n")
    parse_skill(target)  # fail loudly if the front matter is broken
    return True


class DataMapHook:
    """Keeps the data-map skill (the analysts' compass) current: at the start of every run it is
    rebuilt if a data file was added, moved, removed or changed, and the runtime's skills reload."""

    def __init__(self, data_dir, skills_dir, catalog=None, main_table: str = "target_table",
                 treatment: str = "htl_mod_kind"):
        self.args = dict(data_dir=data_dir, skills_dir=skills_dir, catalog=catalog, main_table=main_table,
                         treatment=treatment)

    def on_run_start(self, runtime=None, **_):
        if refresh(**self.args) and runtime is not None:
            runtime.skills.reload()
