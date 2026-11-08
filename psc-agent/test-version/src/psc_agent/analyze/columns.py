"""Every column of the processed tables with what it holds, so the dataset can be cut down to what the analysis needs.

``profile`` reads the tables (``<data>/*.csv``) and measures each column: type, how many rows have a value, distinct
values, how many papers, example values. It joins that with two hand-written sources:

- the column catalog (``<data>/../eda/column_catalog.csv``): role and a note per column, made while the data was prepared;
- the column notes (``<data>/../eda/column_notes.csv``): for each column, how much the analysis needs it
  (``relevance``), what it means (``description``) and why that rating (``why``).

Relevance is judged against the research goal: find insights from the extracted papers (which material classes change
the device results and how), not reproduce or tune a PCE:

    core         the analysis reads it: what was changed, what happened, through which channel
    context      needed to compare fairly (controls, perovskite composition, measurement conditions)
    provenance   identifiers and source traces: kept for joins and audit, not analysed
    drop         redundant, constant, empty or a raw leftover of cleaning: a candidate to remove
"""
from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

RELEVANCE = ("core", "context", "provenance", "drop")
NOTE_FIELDS = ("table", "column", "relevance", "description", "why")
_EXAMPLES = 3
_EXAMPLE_CHARS = 40


@dataclass
class Column:
    table: str
    column: str
    dtype: str  # number | flag | text
    rows: int
    non_null: int
    distinct: int
    papers: int | None  # papers with a value in this column (tables keyed by device_id)
    examples: list[str] = field(default_factory=list)
    role: str = ""  # from the column catalog
    catalog_note: str = ""
    relevance: str = ""  # from the column notes; empty when the column has no note yet
    description: str = ""
    why: str = ""

    @property
    def pct(self) -> float:
        return round(100 * self.non_null / self.rows, 1) if self.rows else 0.0

    def to_dict(self) -> dict:
        return asdict(self) | {"pct": self.pct}


def _paper(device_id: str) -> str:
    return str(device_id).split("_Sample")[0]


def _dtype(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "flag"
    vals = s.dropna()
    if pd.api.types.is_numeric_dtype(s):
        return "flag" if len(vals) and set(vals.unique()) <= {0, 1} and s.nunique() <= 2 else "number"
    if len(vals) and set(map(str, vals.unique())) <= {"True", "False"}:
        return "flag"
    return "text"


def _examples(s: pd.Series) -> list[str]:
    out = []
    for v, _ in s.dropna().astype(str).value_counts().head(_EXAMPLES).items():
        out.append(v if len(v) <= _EXAMPLE_CHARS else v[: _EXAMPLE_CHARS - 1] + "…")
    return out


def read_catalog(path) -> dict[tuple[str, str], dict]:
    """(table, column) -> {role, note} from the Thai column catalog."""
    p = Path(path)
    if not p.is_file():
        return {}
    df = pd.read_csv(p, encoding="utf-8-sig")
    return {(r["ตาราง"], r["คอลัมน์"]): {"role": str(r["บทบาท"]), "note": str(r["คำอธิบาย"])} for _, r in df.iterrows()}


def read_notes(path) -> dict[tuple[str, str], dict]:
    """(table, column) -> {relevance, description, why} from the column notes."""
    p = Path(path)
    if not p.is_file():
        return {}
    df = pd.read_csv(p, encoding="utf-8-sig").fillna("")
    return {(r["table"], r["column"]): {k: str(r[k]) for k in ("relevance", "description", "why")} for _, r in df.iterrows()}


def profile(data_dir, catalog=None, notes=None) -> list[Column]:
    data = Path(data_dir)
    cat = read_catalog(catalog or data.parent / "eda" / "column_catalog.csv")
    note = read_notes(notes or data.parent / "eda" / "column_notes.csv")
    out: list[Column] = []
    for f in sorted(data.glob("*.csv")):
        df = pd.read_csv(f, low_memory=False)
        papers = df["device_id"].map(_paper) if "device_id" in df.columns else None
        for c in df.columns:
            s = df[c]
            has = s.notna() & (s.astype(str).str.strip() != "")
            info = cat.get((f.stem, c), {})
            n = note.get((f.stem, c), {})
            out.append(Column(
                table=f.stem, column=c, dtype=_dtype(s), rows=len(df), non_null=int(has.sum()), distinct=int(s[has].nunique()),
                papers=int(papers[has].nunique()) if papers is not None else None, examples=_examples(s[has]),
                role=info.get("role", ""), catalog_note=info.get("note", ""), relevance=n.get("relevance", ""),
                description=n.get("description", ""), why=n.get("why", "")))
    return out


def summary(cols: list[Column]) -> dict:
    """Counts per table and per relevance, for the top of the page and the terminal print."""
    by_table: dict[str, dict] = {}
    for c in cols:
        t = by_table.setdefault(c.table, {"columns": 0, **{r: 0 for r in RELEVANCE}, "unrated": 0})
        t["columns"] += 1
        t[c.relevance if c.relevance in RELEVANCE else "unrated"] += 1
    total = {"columns": len(cols), **{r: sum(c.relevance == r for c in cols) for r in RELEVANCE}}
    total["unrated"] = len(cols) - sum(total[r] for r in RELEVANCE)
    return {"total": total, "tables": by_table}


def to_csv(cols: list[Column]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["table", "column", "relevance", "role", "dtype", "rows", "non_null", "pct", "distinct", "papers", "examples",
                "description", "why", "catalog_note"])
    for c in cols:
        w.writerow([c.table, c.column, c.relevance, c.role, c.dtype, c.rows, c.non_null, c.pct, c.distinct,
                    "" if c.papers is None else c.papers, " | ".join(c.examples), c.description, c.why, c.catalog_note])
    return buf.getvalue()


def to_markdown(cols: list[Column]) -> str:
    s = summary(cols)["total"]
    lines = [f"# Columns of the processed tables ({s['columns']})", "",
             "Relevance: " + ", ".join(f"{r} {s[r]}" for r in RELEVANCE) + (f", not rated {s['unrated']}" if s["unrated"] else ""), ""]
    tables = dict.fromkeys(c.table for c in cols)
    for t in tables:
        rows = [c for c in cols if c.table == t]
        lines += [f"## {t} ({len(rows)} columns, {rows[0].rows} rows)", "",
                  "| column | relevance | type | filled | papers | meaning | why |", "|---|---|---|---|---|---|---|"]
        for c in rows:
            esc = lambda x: str(x).replace("|", "\\|").replace("\n", " ")  # noqa: E731
            lines.append(f"| `{c.column}` | {c.relevance or '–'} | {c.dtype} | {c.pct:g}% | {'' if c.papers is None else c.papers} "
                         f"| {esc(c.description or c.catalog_note)} | {esc(c.why)} |")
        lines.append("")
    return "\n".join(lines)


def to_text(cols: list[Column], width: int = 100) -> str:
    """Plain text for the terminal."""
    import textwrap

    s = summary(cols)["total"]
    out = [f"{s['columns']} columns   " + "  ".join(f"{r} {s[r]}" for r in RELEVANCE) + (f"  not rated {s['unrated']}" if s["unrated"] else "")]
    for t in dict.fromkeys(c.table for c in cols):
        rows = [c for c in cols if c.table == t]
        out += ["", f"== {t}: {len(rows)} columns, {rows[0].rows} rows"]
        for c in rows:
            head = f"  {c.column}  [{c.relevance or '-'}] {c.dtype}, {c.pct:g}% filled" + ("" if c.papers is None else f", {c.papers} papers")
            out.append(head)
            for label, text in (("meaning", c.description or c.catalog_note), ("why", c.why)):
                if text:
                    out += textwrap.wrap(f"{label}: {text}", width, initial_indent="      ", subsequent_indent="        ")
    return "\n".join(out)
