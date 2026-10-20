"""Look at project data instead of guessing its structure: CSV columns, workbook sheets, notebook cells.

Promoted from the ad-hoc scripts that dominated the work trace (pandas on processed/*.csv,
openpyxl surveys of Raw Data, json surgery on notebooks).

    python .claude/skills/project-cli/datactl.py csv processed/prepared_v2/target_table.csv [--values COL] [--head N]
    python .claude/skills/project-cli/datactl.py xlsx "Raw Data" [--sheet Process] [--units]
    python .claude/skills/project-cli/datactl.py nb NOTEBOOK.ipynb [--grep TEXT] [--cell N|ID]
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _kind(values: list[str]) -> str:
    def all_match(fn):
        try:
            return all(fn(v) is not None for v in values)
        except ValueError:
            return False
    if not values:
        return "empty"
    if all_match(int):
        return "int"
    if all_match(float):
        return "float"
    if {v.lower() for v in values} <= {"true", "false", "0", "1", "y", "n", "yes", "no"}:
        return "bool"
    return "text"


def cmd_csv(path: Path, values: str | None, head: int, top: int) -> int:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        print(f"{path}: empty")
        return 0
    header, rows = rows[0], rows[1:]
    print(f"{path}: {len(rows)} rows x {len(header)} columns")
    if values:
        if values not in header:
            close = difflib.get_close_matches(values, header, n=5, cutoff=0.5) or \
                [h for h in header if any(part in h for part in values.split("_") if len(part) > 2)][:5]
            print(f"no column {values!r}" + (f"; similar: {', '.join(close)}" if close else ""))
            return 1
        i = header.index(values)
        c = Counter(r[i] if i < len(r) else "" for r in rows)
        print(f"{values}: {len(c)} distinct")
        for v, n in c.most_common(top):
            print(f"  {n:6}  {v if v != '' else '<empty>'}")
        return 0
    width = min(max(len(h) for h in header), 34)
    for i, h in enumerate(header):
        vals = [r[i] for r in rows if i < len(r) and r[i].strip() != ""]
        ex = list(dict.fromkeys(vals))[:3]
        print(f"  {h:{width}} {_kind(vals):5} {len(vals):5}/{len(rows)} filled  {len(set(vals)):5} distinct  "
              f"e.g. {', '.join(e[:24] for e in ex)}")
    for r in rows[:head]:
        print("  | " + " | ".join(v[:16] for v in r))
    return 0


def cmd_xlsx(target: Path, sheet: str | None, units: bool, top: int) -> int:
    import openpyxl  # only this subcommand needs it

    files = [target] if target.is_file() else sorted(p for p in target.glob("*.xlsx") if not p.name.startswith("~$"))
    headers: dict[str, Counter] = defaultdict(Counter)
    nrows: Counter = Counter()
    unit_values: Counter = Counter()
    single: dict[str, list[tuple]] = {}
    for f in files:
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                name = ws.title.strip()
                if sheet and name != sheet:
                    continue
                rows = [r for r in ws.iter_rows(min_row=1, values_only=True) if any(v is not None for v in r)]
                if not rows:
                    continue
                hdr = tuple(str(h).strip() for h in rows[0] if h is not None)
                headers[name][hdr] += 1
                nrows[name] += len(rows) - 1
                if "Unit" in hdr:
                    col = [str(h).strip() if h is not None else None for h in rows[0]].index("Unit")
                    unit_values.update(str(r[col]).strip() for r in rows[1:] if col < len(r) and r[col] is not None)
                if len(files) == 1:
                    single[name] = rows[:11]
        finally:
            wb.close()
    print(f"{target}: {len(files)} workbook(s)")
    for name in sorted(headers, key=lambda n: -sum(headers[n].values())):
        print(f"\n[{name}] in {sum(headers[name].values())} workbook(s), {nrows[name]} data rows")
        for hdr, n in headers[name].most_common():
            print(f"  {n:4} x {' | '.join(hdr)}")
        for r in single.get(name, [])[1:]:
            print("       " + " | ".join("" if v is None else str(v)[:20] for v in r))
    if units:
        print(f"\nUnit column values ({len(unit_values)} distinct):")
        for v, n in unit_values.most_common(top):
            print(f"  {n:5}  {v}")
    return 0


def _source(cell) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else s


def _outputs(cell, limit: int) -> str:
    parts = []
    for o in cell.get("outputs", []):
        t = o.get("output_type")
        if t == "stream":
            parts.append("".join(o.get("text", [])))
        elif t == "error":
            parts.append(f"{o.get('ename')}: {o.get('evalue')}\n" + _ANSI.sub("", "\n".join(o.get("traceback", []))))
        else:
            data = o.get("data", {})
            text = data.get("text/plain")
            parts.append("".join(text) if isinstance(text, list) else (text or f"<{', '.join(data)}>"))
    out = "\n".join(parts)
    return out if len(out) <= limit else out[:limit] + f"\n... [{len(out) - limit} more chars]"


def cmd_nb(path: Path, cell: str | None, grep: str | None, limit: int) -> int:
    nb = json.loads(path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])
    if cell is not None:
        idx = int(cell) if cell.isdigit() else next((i for i, c in enumerate(cells) if c.get("id") == cell), None)
        if idx is None or not 0 <= idx < len(cells):
            print(f"no cell {cell!r} (cells 0..{len(cells) - 1})")
            return 1
        c = cells[idx]
        print(f"c{idx} id={c.get('id')} {c.get('cell_type')} exec={c.get('execution_count')}\n----- source -----")
        print(_source(c))
        if c.get("outputs"):
            print("----- outputs -----\n" + _outputs(c, limit))
        return 0
    print(f"{path}: {len(cells)} cells")
    for i, c in enumerate(cells):
        src = _source(c)
        if grep:
            hits = [l for l in src.splitlines() if grep.lower() in l.lower()]
            if hits:
                print(f"c{i} id={c.get('id')} {c.get('cell_type')}")
                for l in hits[:5]:
                    print(f"     {l.strip()[:150]}")
            continue
        outs = Counter(o.get("output_type") for o in c.get("outputs", []))
        err = next((o.get("ename") for o in c.get("outputs", []) if o.get("output_type") == "error"), None)
        first = next((l.strip() for l in src.splitlines() if l.strip()), "")
        print(f"c{i:<3} {str(c.get('id'))[:12]:12} {c.get('cell_type', '')[:4]:4} {len(src.splitlines()):4}L "
              f"{'ERR ' + err if err else ','.join(outs) or '-':12} {first[:80]}")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Thai text on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Inspect CSV / xlsx / notebooks without guessing")
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("csv", help="columns: type, fill rate, distinct, examples")
    c.add_argument("path", type=Path)
    c.add_argument("--values", metavar="COL", help="value counts of one column")
    c.add_argument("--head", type=int, default=0)
    c.add_argument("--top", type=int, default=30)
    x = sub.add_parser("xlsx", help="sheets and header variants of one workbook or a folder of them")
    x.add_argument("path", type=Path)
    x.add_argument("--sheet")
    x.add_argument("--units", action="store_true", help="value counts of the Unit columns")
    x.add_argument("--top", type=int, default=40)
    n = sub.add_parser("nb", help="notebook outline, grep, or one cell with its outputs")
    n.add_argument("path", type=Path)
    n.add_argument("--cell", help="index or cell id")
    n.add_argument("--grep")
    n.add_argument("--limit", type=int, default=4000)
    args = ap.parse_args(argv)
    if not args.path.exists():
        print(f"not found: {args.path}")
        return 1
    if args.action == "csv":
        return cmd_csv(args.path, args.values, args.head, args.top)
    if args.action == "xlsx":
        return cmd_xlsx(args.path, args.sheet, args.units, args.top)
    return cmd_nb(args.path, args.cell, args.grep, args.limit)


if __name__ == "__main__":
    sys.exit(main())
