"""REPO_MAP.md: a compass of this repository for agents.

Lists every Python module (docstring, top-level symbols with line numbers, imports, used-by, data
files it names), every notebook (headings with cell index/id, defs, data files, error cells), docs and
data folders. Built from source with the standard library; the output is deterministic, so a rebuild
without real changes leaves git clean.

    python .claude/skills/repo-map/build_map.py           # rebuild if anything changed
    python .claude/skills/repo-map/build_map.py --force
    python .claude/skills/repo-map/build_map.py --check   # exit 1 if stale; writes nothing
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[3])
SKILL = ".claude/skills/repo-map"
MAPPED = {".py", ".ipynb", ".md"}
DATA_DIRS = ("Raw Data", "original paper", "processed", "reports")  # summarized, not parsed
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".ipynb_checkpoints", "build", "dist", ".venv", "venv"}
_DATA_REF = re.compile(r"\.(?:csv|xlsx|xls|json|parquet|png|pdf|html)$|^(?:Raw Data|original paper|processed|reports)\b")
_PATHLIKE = re.compile(r"^[\w\-./*{}\\]+$")  # after removing "Raw Data"/"original paper": no prose


def _data_ref(s: str) -> bool:
    s = s.strip()
    return bool(_DATA_REF.search(s)) and bool(_PATHLIKE.match(s.replace("Raw Data", "x").replace("original paper", "x")))
_NB_DATA = re.compile(r"""['"]([^'"\n]{3,150}?\.(?:csv|xlsx|json|parquet|png|pdf|html))['"]""")
_NB_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ]+)|import\s+([\w.]+))", re.M)
MAX_HEADINGS = 40


def paths(root: Path) -> dict[str, Path]:
    d = root / SKILL
    return {"map": d / "REPO_MAP.md", "notes": d / "notes.md", "state": d / ".map_state.json"}


def list_files(root: Path) -> list[str]:
    """Repo files as git sees them (tracked + untracked, minus .gitignore), posix-style; walk without git."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True, timeout=30).stdout.decode("utf-8", "replace")
        rels = [p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        rels = []
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info"))
            rels += [Path(d, f).relative_to(root).as_posix() for f in files]
    return sorted(p for p in set(rels) if (root / p).is_file())


def _is_mapped(rel: str) -> bool:
    return (PurePosixPath(rel).suffix in MAPPED and not rel.startswith(DATA_DIRS)
            and rel != f"{SKILL}/REPO_MAP.md")


def state(root: Path, files: list[str], *, content: bool) -> dict:
    mapped = [p for p in files if _is_mapped(p)]
    data = Counter(p.split("/")[0] for p in files if p.startswith(DATA_DIRS))
    s = {"files": mapped,
         "structure": hashlib.sha1("\n".join(mapped + [f"{k}:{v}" for k, v in sorted(data.items())]).encode()).hexdigest()}
    if content:
        h = hashlib.sha1()
        for p in mapped + [f"{SKILL}/notes.md"]:
            f = root / p
            if f.exists():
                h.update(p.encode() + b"\0" + f.read_bytes())
        s["content"] = h.hexdigest()
    return s


def load_state(root: Path) -> dict | None:
    try:
        return json.loads(paths(root)["state"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- parsing


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0].strip()[:140] if (text or "").strip() else ""


def parse_py(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"error": f"syntax error at L{e.lineno}"}
    info: dict = {"doc": _first_line(ast.get_docstring(tree)), "defs": [], "classes": [], "consts": [],
                  "imports": [], "data": [], "main": False}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["defs"].append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and not (n.name.startswith("__") and n.name.endswith("__"))]
            info["classes"].append((node.name, node.lineno, methods))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1 and not t.id.startswith("_"):
                    info["consts"].append((t.id, node.lineno))
        elif (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
              and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            info["main"] = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info["imports"] += [(a.name, 0, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            info["imports"].append((node.module or "", node.level, [a.name for a in node.names]))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 160 \
                and _data_ref(node.value):
            info["data"].append(node.value.strip())
    info["data"] = list(dict.fromkeys(info["data"]))
    return info


def parse_nb(path: Path) -> dict:
    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"error": f"unreadable notebook: {e}"[:120]}
    heads, defs, imports, data, errors = [], [], [], [], []
    cells = nb.get("cells", [])
    for i, c in enumerate(cells):
        src = "".join(c.get("source", [])) if isinstance(c.get("source"), list) else (c.get("source") or "")
        if c.get("cell_type") == "markdown":
            for line in src.splitlines():
                m = re.match(r"^(#{1,3})\s+(.+)", line)
                if m:
                    heads.append((i, c.get("id"), len(m.group(1)), m.group(2).strip()[:90]))
        elif c.get("cell_type") == "code":
            defs += [(n, i) for n in re.findall(r"^(?:def|class)\s+(\w+)", src, re.M)]
            for frm, names, imp in _NB_IMPORT.findall(src):
                imports.append((frm or imp, 0, [n.strip() for n in names.split(",") if n.strip()]))
            data += [d for d in _NB_DATA.findall(src) if _data_ref(d)]
            if any(o.get("output_type") == "error" for o in c.get("outputs", [])):
                errors.append(i)
    if len(heads) > MAX_HEADINGS:
        heads = [h for h in heads if h[2] <= 2][:MAX_HEADINGS]
    return {"cells": len(cells), "heads": heads, "defs": defs, "imports": imports,
            "data": list(dict.fromkeys(data)), "errors": errors}


def module_index(py: list[str]) -> dict[str, list[str]]:
    """Every dotted suffix of every module path -> files ("psc_recheck.compare", "compare", ...)."""
    idx: dict[str, list[str]] = defaultdict(list)
    for rel in py:
        parts = PurePosixPath(rel).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            if all(p.isidentifier() for p in parts[i:]):
                idx[".".join(parts[i:])].append(rel)
    return idx


def resolve(importer: str, module: str, level: int, names: list[str], idx, files: set[str]) -> set[str]:
    def with_submodules(pkg_files):
        out = set(pkg_files)
        for t in pkg_files:
            if t.endswith("__init__.py"):
                out |= {f"{PurePosixPath(t).parent}/{n}.py" for n in names} & files
        return out

    if level:
        base = PurePosixPath(importer).parent
        for _ in range(level - 1):
            base = base.parent
        mod = base.joinpath(*module.split(".")) if module else base
        found = {f"{mod}.py", f"{mod}/__init__.py"} & files
        return with_submodules(found) | ({f"{mod}/{n}.py" for n in names} & files)
    hits = idx.get(module, [])
    if len(hits) > 1:  # ambiguous bare name: prefer the importer's own folder
        near = [h for h in hits if PurePosixPath(h).parent == PurePosixPath(importer).parent]
        hits = near if len(near) == 1 else []
    return with_submodules(hits)


# ---------------------------------------------------------------- rendering


def _notes(root: Path) -> dict[str, str]:
    notes = {}
    p = paths(root)["notes"]
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*-\s*`?([^`:]+?)`?\s*:\s*(.+)$", line)
            if m:
                notes[m.group(1).strip()] = m.group(2).strip()
    return notes


def _folder(rel: str, depth: int) -> str:
    parts = PurePosixPath(rel).parts
    return "/".join(parts[:depth]) + "/" if len(parts) > depth else ""


def _fmt_size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def _links(origin: str, targets) -> str:
    """Paths relative to ``origin``'s folder when they share it (keeps long package paths short)."""
    here = str(PurePosixPath(origin).parent)
    return ", ".join(f"`{t[len(here) + 1:]}`" if here != "." and t.startswith(here + "/") else f"`{t}`"
                     for t in sorted(targets))


def build(root: Path, files: list[str]) -> str:
    """The map text. Its version stamp hashes the rendered text itself, so inputs that change nothing
    visible (e.g. evidence lines added to PLAN.md) produce a byte-identical file."""
    text = _render(root, files)
    return text.replace("@@VERSION@@", hashlib.sha1(text.encode()).hexdigest()[:10])


def _render(root: Path, files: list[str]) -> str:
    mapped = [p for p in files if _is_mapped(p)]
    fileset = set(mapped)
    py = [p for p in mapped if p.endswith(".py")]
    nbs = [p for p in mapped if p.endswith(".ipynb")]
    docs = [p for p in mapped if p.endswith(".md")]
    notes = _notes(root)
    idx = module_index(py)
    info = {p: parse_py(root / p) for p in py}
    nbinfo = {p: parse_nb(root / p) for p in nbs}

    deps: dict[str, set[str]] = {}
    for p in py + nbs:
        imps = (info.get(p) or nbinfo.get(p) or {}).get("imports", [])
        deps[p] = set().union(*[resolve(p, m, lvl, names, idx, fileset) for m, lvl, names in imps]) - {p} \
            if imps else set()
    used_by: dict[str, set[str]] = defaultdict(set)
    for p, ds in deps.items():
        for d in ds:
            used_by[d].add(p)

    # layout: folders up to depth 2 that hold mapped files, plus data folders
    counts: dict[str, Counter] = defaultdict(Counter)
    for p in mapped:
        for depth in (1, 2):
            f = _folder(p, depth)
            if f:
                counts[f][PurePosixPath(p).suffix.lstrip(".")] += 1
    data_counts: dict[str, Counter] = defaultdict(Counter)
    for p in files:
        if p.startswith(DATA_DIRS):
            data_counts[_folder(p, 1)][PurePosixPath(p).suffix.lstrip(".").lower() or "other"] += 1

    out = ["# REPO_MAP", "",
           f"> Generated by `{SKILL}/build_map.py` (version `@@VERSION@@`); hooks rebuild it when files move "
           f"and at the end of each turn. Do not edit: annotate folders in `{SKILL}/notes.md`.",
           "> Use: find the area in **Layout**, open its section, then Read `path` at `L<line>` "
           "(notebook cells: `c<index>` / `#<cell id>`). Grep only inside files the map points to.", "",
           "## Layout", "", "| path | contents | purpose |", "|---|---|---|"]
    rows = sorted(set(counts) | set(data_counts))
    for f in rows:
        c = data_counts.get(f) or counts[f]
        what = ", ".join(f"{n} {ext}" for ext, n in c.most_common())
        purpose = notes.get(f, notes.get(f.rstrip("/"), ""))
        out.append(f"| `{f}` | {what} | {purpose} |")
    for p in mapped:
        if "/" not in p:
            purpose = notes.get(p) or (info.get(p) or {}).get("doc") or _doc_title(root / p)
            out.append(f"| `{p}` | file | {purpose} |")

    out += ["", "## Python modules", ""]
    for p in py:
        i = info[p]
        out.append(f"**`{p}`**" + (f" — {i['doc']}" if i.get("doc") else "") + (f" ({i['error']})" if i.get("error") else ""))
        if i.get("defs"):
            out.append("- def: " + ", ".join(f"{n} L{l}" for n, l in i["defs"]))
        for n, l, methods in i.get("classes", []):
            out.append(f"- class {n} L{l}" + (f": {', '.join(methods)}" if methods else ""))
        if i.get("consts"):
            out.append("- const: " + ", ".join(f"{n} L{l}" for n, l in i["consts"]))
        if deps.get(p):
            out.append("- imports: " + _links(p, deps[p]))
        if used_by.get(p):
            out.append("- used by: " + _links(p, used_by[p]))
        if i.get("data"):
            out.append("- names data: " + ", ".join(f"`{d}`" for d in i["data"][:10]))
        if i.get("main"):
            out.append("- runnable: `if __name__ == \"__main__\"`")
        out.append("")

    out += ["## Notebooks", ""]
    for p in nbs:
        n = nbinfo[p]
        if n.get("error"):
            out += [f"**`{p}`** ({n['error']})", ""]
            continue
        head = f"**`{p}`** — {n['cells']} cells, {_fmt_size((root / p).stat().st_size)}"
        out.append(head + (f", error outputs in cells {', '.join(f'c{x}' for x in n['errors'])}" if n["errors"] else ""))
        if n["heads"]:
            out.append("- " + " · ".join(f"c{i}{'#' + cid if cid else ''} {'#' * lvl} {t}" for i, cid, lvl, t in n["heads"]))
        if n["defs"]:
            out.append("- defs: " + ", ".join(f"{name} c{i}" for name, i in n["defs"]))
        if deps.get(p):
            out.append("- imports: " + _links(p, deps[p]))
        if n["data"]:
            out.append("- names data: " + ", ".join(f"`{d}`" for d in n["data"][:12]))
        out.append("")

    out += ["## Docs", ""] + [f"- `{p}` — {_doc_title(root / p)}" for p in docs] + [""]

    out += ["## Data folders (not code; inspect with `.claude/skills/project-cli/datactl.py`)", ""]
    for f in sorted(data_counts):
        sub = Counter(_folder(p, 2) for p in files if p.startswith(f) and _folder(p, 2))
        subs = ", ".join(f"`{s}` {n}" for s, n in sorted(sub.items()))
        out.append(f"- `{f}`: " + ", ".join(f"{n} {e}" for e, n in data_counts[f].most_common())
                   + (f" — {subs}" if subs else ""))

    missing = [f for f in rows if f not in notes and f.rstrip("/") not in notes]
    if missing:
        out += ["", "## Unannotated (Big Agent: add `- path/: purpose` lines to notes.md)", ""]
        out += [f"- `{f}`" for f in missing]
    return "\n".join(out) + "\n"


def _doc_title(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
    except OSError:
        return ""
    if lines and lines[0].strip() == "---":  # frontmatter: use name/description
        meta = {}
        for l in lines[1:]:
            if l.strip() == "---":
                break
            k, _, v = l.partition(":")
            meta[k.strip()] = v.strip()
        if meta.get("description"):
            return f"{meta.get('name', '')}: {meta['description']}"[:140]
    for l in lines:
        if l.startswith("#"):
            return l.lstrip("#").strip()[:140]
    return next((l.strip()[:140] for l in lines if l.strip()), "")


def refresh(root: Path = ROOT, *, structure_only: bool = False, force: bool = False) -> tuple[str, list[str]]:
    """Rebuild REPO_MAP.md when files were added/moved/removed ("structure") or, unless
    ``structure_only``, when mapped files changed ("content"). Returns (kind, ["+added", "-removed"])."""
    files = list_files(root)
    old = load_state(root)
    new = state(root, files, content=False)
    changes = []
    if old:
        changes = [f"+{p}" for p in sorted(set(new["files"]) - set(old["files"]))] + \
                  [f"-{p}" for p in sorted(set(old["files"]) - set(new["files"]))]
    if force or not old or old.get("structure") != new["structure"]:
        kind = "structure"
    elif structure_only:
        return "unchanged", []
    else:
        new = state(root, files, content=True)
        if old.get("content") == new["content"]:
            return "unchanged", []
        kind = "content"
    new = state(root, files, content=True)
    p = paths(root)
    p["map"].parent.mkdir(parents=True, exist_ok=True)
    text = build(root, files)
    same = p["map"].exists() and p["map"].read_text(encoding="utf-8") == text
    if not same:
        p["map"].write_text(text, encoding="utf-8", newline="\n")
    p["state"].write_text(json.dumps(new), encoding="utf-8")
    return ("unchanged", []) if same and kind == "content" else (kind, changes)


def is_fresh(root: Path = ROOT) -> bool:
    """True when a rebuild would not change REPO_MAP.md (reads everything; meant for checks, not hooks)."""
    p = paths(root)["map"]
    return p.exists() and p.read_text(encoding="utf-8") == build(root, list_files(root))


def compass(root: Path = ROOT) -> str:
    """Header + Layout of the map: the part every subagent gets at start."""
    p = paths(root)["map"]
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    layout = text.split("## Python modules")[0].strip()
    return (f"Repo compass (from {SKILL}/REPO_MAP.md). Before Grep/Glob, read the full map at "
            f"{SKILL}/REPO_MAP.md for symbols with line numbers, imports, notebooks and data files.\n\n{layout}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build REPO_MAP.md")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 if a rebuild would change the map; write nothing")
    args = ap.parse_args(argv)
    if args.check:
        fresh = is_fresh(ROOT)
        print("fresh" if fresh else "stale")
        return 0 if fresh else 1
    kind, changes = refresh(ROOT, force=args.force)
    print(f"REPO_MAP: {kind}" + (f" ({', '.join(changes[:10])})" if changes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
