"""Data tools for the analyst model: tested functions over the processed CSV tables (needs pandas).

They implement the project's method so the model does not have to:
- a paper is its DOI key (letters and digits of the device_id prefix), so DOIs written two ways merge;
- a modified device is compared with the control of the same paper (control = treatment column empty/False);
- pooled results count papers, not devices (several treated levels in one paper are averaged first).
Bad table or column names raise ToolError listing the valid or similar names.
"""
from __future__ import annotations

import difflib
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.agent import Tool, ToolError

OPS = ("==", "!=", ">", ">=", "<", "<=", "in", "contains", "notnull", "isnull")
_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n"}


def doi_key(s) -> str:
    return re.sub(r"[^0-9a-z]", "", str(s).lower())


def sign_test(positive: int, n: int) -> float | None:
    """Two-sided exact binomial test against p = 0.5 (ties already dropped)."""
    if n == 0:
        return None
    k = min(positive, n - positive)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def _r(x):
    if x is None:
        return None
    x = float(x)
    if math.isnan(x):
        return None
    if x != 0 and (abs(x) >= 1e4 or abs(x) < 1e-3):  # trap densities, tiny p-values: keep 4 significant digits
        return float(f"{x:.4g}")
    return round(x, 4)


def spearman(a, b) -> float | None:
    ra, rb = pd.Series(a, dtype=float).rank(), pd.Series(b, dtype=float).rank()
    if len(ra) < 3 or ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _pool(v: pd.Series) -> dict:
    pos, neg = int((v > 0).sum()), int((v < 0).sum())
    return {"n_papers": int(len(v)), "positive": pos, "negative": neg, "median": _r(v.median()) if len(v) else None,
            "mean": _r(v.mean()) if len(v) else None, "sign_test_p": _r(sign_test(pos, pos + neg))}


def load_catalog(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """processed/eda/column_catalog.csv -> {(table, column): (role, meaning)} (columns ตาราง คอลัมน์ บทบาท คำอธิบาย)."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    cols = list(df.columns)
    if len(cols) < 4:
        return {}
    table, column, role, meaning = cols[0], cols[1], cols[2], cols[-1]
    return {(str(r[table]), str(r[column])): (str(r[role]), str(r[meaning])) for _, r in df.iterrows()}


class DataTools:
    def __init__(self, data_dir, *, catalog=None, main_table: str = "target_table", treatment: str = "htl_mod_kind",
                 id_column: str = "device_id"):
        self.data_dir = Path(data_dir)
        self.main_table, self.treatment, self.id_column = main_table, treatment, id_column
        cat = Path(catalog) if catalog else self.data_dir.parent / "eda" / "column_catalog.csv"
        self.catalog = load_catalog(cat) if cat.is_file() else {}
        self._cache: dict[str, pd.DataFrame] = {}
        self._labels: dict[str, str] = {}

    # ------------------------------------------------------------ helpers

    def tables(self) -> list[str]:
        return sorted(p.stem for p in self.data_dir.glob("*.csv"))

    def table(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            names = self.tables()
            if name not in names:
                close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
                raise ToolError(f"no table {name!r}; tables: {', '.join(names)}" + (f" (did you mean {close[0]}?)" if close else ""))
            df = pd.read_csv(self.data_dir / f"{name}.csv", encoding="utf-8-sig", low_memory=False)
            if self.id_column in df.columns:
                raw = df[self.id_column].astype(str).str.rsplit("_Sample", n=1).str[0]
                # concat, not insert: pandas 3 keeps one block per Arrow string column, so wide tables
                # (process has 118 columns) warn on every insert
                df = pd.concat([df, raw.map(doi_key).rename("paper_key")], axis=1)
                for k, label in zip(df["paper_key"], raw):
                    self._labels.setdefault(k, label)
            self._cache[name] = df
        return self._cache[name]

    def label(self, key: str) -> str:
        return self._labels.get(key, key)

    def _column(self, table: str, column: str) -> pd.Series:
        df = self.table(table)
        if column not in df.columns:
            close = difflib.get_close_matches(str(column), [str(c) for c in df.columns], n=5, cutoff=0.5)
            raise ToolError(f"no column {column!r} in {table}" +
                            (f"; similar: {', '.join(close)}" if close else f"; call describe_table('{table}')"))
        return df[column]

    def _numeric(self, table: str, column: str) -> pd.Series:
        s = self._column(table, column)
        if pd.api.types.is_bool_dtype(s) or not pd.api.types.is_numeric_dtype(s):
            raise ToolError(f"{table}.{column} is not numeric; describe_table shows the type of every column")
        return s

    def _treated(self, df: pd.DataFrame, treatment: str) -> pd.Series:
        s = df[treatment]
        if pd.api.types.is_bool_dtype(s):
            return s.fillna(False).astype(bool)
        text = s.astype("string").str.strip().str.lower()
        values = set(text.dropna().unique())
        if values and values <= _TRUE | _FALSE:
            return text.isin(_TRUE).fillna(False).astype(bool)
        return (text.notna() & (text != "")).fillna(False).astype(bool)

    # ------------------------------------------------------------ tools

    def list_tables(self) -> dict:
        out = []
        for t in self.tables():
            df = self.table(t)
            out.append({"table": t, "rows": len(df), "columns": len(df.columns),
                        "papers": int(df["paper_key"].nunique()) if "paper_key" in df else None})
        return {"tables": out, "main_table": self.main_table,
                "treatment": f"{self.treatment} (empty or False = control device of its paper)"}

    def describe_table(self, table: str, columns: list[str] | None = None) -> dict:
        """Without ``columns``: one compact line per column. With ``columns``: full detail for those."""
        df = self.table(table)
        out = []
        for c in columns or list(df.columns):
            s = self._column(table, c)
            filled = s.dropna()
            if not pd.api.types.is_numeric_dtype(filled):
                filled = filled[filled.astype("string").str.strip() != ""]
            kind = ("bool" if pd.api.types.is_bool_dtype(s) else
                    "number" if pd.api.types.is_numeric_dtype(s) else "text")
            info = {"column": c, "type": kind, "filled": int(len(filled))}
            if "paper_key" in df.columns:
                info["papers"] = int(df.loc[filled.index, "paper_key"].nunique())
            meta = self.catalog.get((table, str(c)))
            if meta:
                info["role"] = meta[0]
            if columns:
                info["rows"] = len(df)
                info["distinct"] = int(filled.nunique())
                info["examples"] = [str(v)[:40] for v in pd.unique(filled)[:3]]
                if kind == "number" and len(filled):
                    info.update(min=_r(filled.min()), median=_r(filled.median()), max=_r(filled.max()))
                if meta:
                    info["meaning"] = meta[1][:300]
            out.append(info)
        return {"table": table, "rows": len(df), "columns": out}

    def column_values(self, table: str, column: str, top: int = 20) -> dict:
        s = self._column(table, column)
        counts = s.astype("string").fillna("<empty>").value_counts().head(max(1, min(int(top), 100)))
        return {"table": table, "column": column, "distinct": int(s.nunique(dropna=True)), "empty": int(s.isna().sum()),
                "values": [{"value": str(k), "count": int(v)} for k, v in counts.items()]}

    def query_rows(self, table: str, columns: list[str], where: list[list[str]] | None = None, limit: int = 20) -> dict:
        """``where`` items: ["pce", ">", "20"], ["htl_mod_kind", "notnull"], ["etl_family", "in", "C60|PCBM"]."""
        df = self.table(table)
        mask = pd.Series(True, index=df.index)
        for cond in where or []:
            if not isinstance(cond, (list, tuple)) or len(cond) not in (2, 3):
                raise ToolError('each where item is [column, op] or [column, op, value], e.g. ["pce", ">", "20"]')
            col, op = cond[0], str(cond[1])
            value = cond[2] if len(cond) == 3 else None
            mask &= self._condition(table, col, op, value)
        cols = [c for c in columns if self._column(table, c) is not None]
        sub = df.loc[mask, cols].head(max(1, min(int(limit), 100)))
        return {"table": table, "matched": int(mask.sum()), "rows": sub.astype(object).where(sub.notna(), None)
                .to_dict("records")}

    def _condition(self, table, col, op, value) -> pd.Series:
        s = self._column(table, col)
        if op == "notnull":
            return s.notna()
        if op == "isnull":
            return s.isna()
        if op not in OPS:
            raise ToolError(f"unknown op {op!r}; use one of {', '.join(OPS)}")
        if value is None:
            raise ToolError(f"op {op!r} needs a value")
        if op == "contains":
            return s.astype("string").str.contains(str(value), case=False, regex=False).fillna(False).astype(bool)
        if op == "in":
            options = [v.strip() for v in str(value).split("|")] if not isinstance(value, list) else [str(v) for v in value]
            return s.astype("string").isin(options).fillna(False).astype(bool)
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ToolError(f"{col} is numeric; give a number for {op!r}") from None
        else:
            s, value = s.astype("string"), str(value)
        result = {"==": s == value, "!=": s != value, ">": s > value, ">=": s >= value, "<": s < value,
                  "<=": s <= value}[op]
        return result.fillna(False).astype(bool)

    def paper_deltas(self, metrics: list[str], treatment: str | None = None, level_column: str | None = None,
                     table: str | None = None) -> dict:
        """Within-paper delta (mean treated − mean control) per paper, pooled with one vote per paper."""
        table = table or self.main_table
        treatment = treatment or self.treatment
        df = self.table(table)
        for m in metrics:
            self._numeric(table, m)
        self._column(table, treatment)
        if level_column:
            self._column(table, level_column)
        if "paper_key" not in df.columns:
            raise ToolError(f"{table} has no {self.id_column}; paper_deltas needs device rows")
        treated = self._treated(df, treatment)
        rows, no_control = [], []
        for key, g in df.groupby("paper_key", sort=True):
            t_mask = treated.loc[g.index]
            control, modified = g[~t_mask], g[t_mask]
            if control.empty or modified.empty:
                if not modified.empty:
                    no_control.append(self.label(key))
                continue
            for level, sub in (modified.groupby(level_column, dropna=False) if level_column else [(None, modified)]):
                rec = {"paper": self.label(key), "n_control": len(control), "n_treated": len(sub)}
                if level_column:
                    rec["level"] = None if pd.isna(level) else str(level)
                for m in metrics:
                    c, t = control[m].dropna(), sub[m].dropna()
                    rec[f"d_{m}"] = _r(t.mean() - c.mean()) if len(c) and len(t) else None
                rows.append(rec)
        per = pd.DataFrame(rows)
        summary, by_level = {}, None
        if not per.empty:
            cols = [f"d_{m}" for m in metrics]
            by_paper = per.groupby("paper")[cols].mean()  # one vote per paper
            summary = {m: _pool(by_paper[f"d_{m}"].dropna()) for m in metrics}
            if level_column:
                by_level = {str(lv): {m: _pool(g[f"d_{m}"].dropna()) for m in metrics}
                            for lv, g in per.groupby("level", dropna=False)}
        merged = {self.label(k): sorted(set(v)) for k, v in
                  df[self.id_column].astype(str).str.rsplit("_Sample", n=1).str[0].groupby(df["paper_key"]).unique().items()
                  if len(set(v)) > 1}
        return {"table": table, "treatment": f"{treatment}: empty/False = control", "metrics": metrics,
                "papers_with_pairs": int(per["paper"].nunique()) if not per.empty else 0,
                "summary": summary, "by_level": by_level, "per_paper": rows[:80],
                "papers_without_control": no_control[:40], "merged_doi_spellings": merged}

    def within_paper_correlation(self, x: str, y: str, mode: str = "delta", treatment: str | None = None,
                                 table: str | None = None) -> dict:
        """mode "delta": Spearman between per-paper deltas of x and y (one point per paper).
        mode "demeaned": Spearman across devices after subtracting each paper's mean (papers with >= 2 devices)."""
        table = table or self.main_table
        if mode == "delta":
            d = self.paper_deltas([x, y], treatment=treatment, table=table)
            per = pd.DataFrame(d["per_paper"])
            if per.empty:
                return {"mode": mode, "n_papers": 0, "rho": None, "p": None}
            per = per.groupby("paper")[[f"d_{x}", f"d_{y}"]].mean().dropna()
            a, b = per[f"d_{x}"].to_numpy(), per[f"d_{y}"].to_numpy()
            rho = spearman(a, b)
            return {"mode": mode, "x": x, "y": y, "n_papers": len(per), "rho": _r(rho),
                    "p": _r(_perm_p(a, b, None, rho)) if rho is not None else None,
                    "pairs": [{"paper": p, f"d_{x}": _r(r[f"d_{x}"]), f"d_{y}": _r(r[f"d_{y}"])}
                              for p, r in per.iterrows()][:60]}
        if mode != "demeaned":
            raise ToolError('mode is "delta" or "demeaned"')
        df = self.table(table)
        sx, sy = self._numeric(table, x), self._numeric(table, y)
        sub = pd.DataFrame({"paper": df["paper_key"], "x": sx, "y": sy}).dropna()
        sub = sub[sub.groupby("paper")["x"].transform("size") >= 2]
        if sub.empty:
            return {"mode": mode, "n_devices": 0, "n_papers": 0, "rho": None, "p": None}
        a = (sub["x"] - sub.groupby("paper")["x"].transform("mean")).to_numpy()
        b = (sub["y"] - sub.groupby("paper")["y"].transform("mean")).to_numpy()
        rho = spearman(a, b)
        return {"mode": mode, "x": x, "y": y, "n_devices": len(sub), "n_papers": int(sub["paper"].nunique()),
                "rho": _r(rho), "p": _r(_perm_p(a, b, sub["paper"].to_numpy(), rho)) if rho is not None else None}

    def paper_devices(self, paper: str, columns: list[str] | None = None) -> dict:
        df = self.table(self.main_table)
        key = doi_key(paper)
        sub = df[df["paper_key"] == key]
        if sub.empty:
            close = difflib.get_close_matches(key, list(df["paper_key"].unique()), n=3, cutoff=0.6)
            raise ToolError(f"no paper {paper!r}" + (f"; similar: {', '.join(self.label(c) for c in close)}" if close else ""))
        if columns:  # asked-for columns must exist; the default set adapts to the table
            cols = [self._column(self.main_table, c).name for c in columns]
        else:
            cols = [c for c in (self.id_column, self.treatment, "pce", "voc", "jsc", "ff") if c in df.columns]
        rows = sub[cols]
        return {"paper": self.label(key), "devices": rows.astype(object).where(rows.notna(), None).to_dict("records")}

    # ------------------------------------------------------------ specs

    def tools(self) -> list[Tool]:
        s, arr = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
        obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
        return [
            Tool("list_tables", "Tables with row/column counts and paper coverage.", obj({}, []), self.list_tables),
            Tool("describe_table", "Columns of a table: type, filled count, papers with a value, role. Pass columns "
                 "for full detail (examples, range, meaning).", obj({"table": s, "columns": arr}, ["table"]),
                 self.describe_table),
            Tool("column_values", "Value counts of one column.",
                 obj({"table": s, "column": s, "top": {"type": "integer"}}, ["table", "column"]), self.column_values),
            Tool("query_rows", 'Rows matching conditions. where items: ["pce", ">", "20"], ["htl_mod_kind", '
                 '"notnull"], ["etl_family", "in", "C60|PCBM"]. ops: ' + ", ".join(OPS),
                 obj({"table": s, "columns": arr, "where": {"type": "array", "items": arr},
                      "limit": {"type": "integer"}}, ["table", "columns"]), self.query_rows),
            Tool("paper_deltas", "Within-paper effect of the treatment: mean(treated) - mean(control) per paper for "
                 "each metric, pooled one vote per paper (median, positive/negative, sign test). level_column splits "
                 "treated devices (e.g. htl_mod_kind).",
                 obj({"metrics": arr, "treatment": s, "level_column": s, "table": s}, ["metrics"]), self.paper_deltas),
            Tool("within_paper_correlation", 'Spearman correlation that removes lab effects. mode "delta": per-paper '
                 'deltas of x vs y; mode "demeaned": devices after subtracting each paper\'s mean.',
                 obj({"x": s, "y": s, "mode": {"type": "string", "enum": ["delta", "demeaned"]}, "treatment": s,
                      "table": s}, ["x", "y"]), self.within_paper_correlation),
            Tool("paper_devices", "All devices of one paper (DOI in any spelling).",
                 obj({"paper": s, "columns": arr}, ["paper"]), self.paper_devices),
        ]


def _perm_p(a, b, groups, rho, n_perm: int = 2000, seed: int = 0) -> float:
    """Permutation p-value for |rho|; with ``groups``, y is shuffled within each group (paper)."""
    rng = np.random.default_rng(seed)
    ra = pd.Series(a, dtype=float).rank().to_numpy()
    rb = pd.Series(b, dtype=float).rank().to_numpy()
    idx = [np.flatnonzero(groups == g) for g in pd.unique(groups)] if groups is not None else [np.arange(len(rb))]
    hits = 0
    for _ in range(n_perm):
        perm = rb.copy()
        for ix in idx:
            perm[ix] = rb[rng.permutation(ix)]
        r = np.corrcoef(ra, perm)[0, 1]
        hits += abs(r) >= abs(rho) - 1e-12
    return (hits + 1) / (n_perm + 1)
