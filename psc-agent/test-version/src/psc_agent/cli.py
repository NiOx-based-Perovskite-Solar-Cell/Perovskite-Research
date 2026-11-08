"""Command line: psc-agent pairs | recheck | extract | evaluate | analyze | map | trace | learn | skills | serve."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core.hooks import BudgetHook, QuoteCheckHook, TraceHook
from .core.runtime import Runtime
from .core.skills import SkillBook
from .recheck.report import STATUSES

DEFAULT_MODEL = {"openrouter": "anthropic/claude-sonnet-5.5", "claude-code": "claude-sonnet-5-5"}


def _model_args(p: argparse.ArgumentParser, out: str, *, subscription: bool = True) -> None:
    providers = ["openrouter", "claude-code"] if subscription else ["openrouter"]
    p.add_argument("--provider", choices=providers, default="openrouter",
                   help="openrouter = OPENROUTER_API_KEY; claude-code = your Claude subscription through the `claude` CLI, no key "
                        "(Claude models only, no tool calling)")
    p.add_argument("--model", help="default model id for every role (image input + structured output); "
                                   f"default {DEFAULT_MODEL['openrouter']} / {DEFAULT_MODEL['claude-code']}")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="thinking effort (claude-code: default medium; openrouter: sent as reasoning.effort when given)")
    p.add_argument("--out", default=out)
    p.add_argument("--skills", default="skills", help="project skills folder (added to the defaults; receives generated skills)")
    p.add_argument("--budget", type=float, help="stop calling models after this many USD")
    p.add_argument("--max-tokens", type=int, default=32000)
    p.add_argument("--no-cache", action="store_true", help="do not reuse cached model responses")


def _runtime(args, out: Path, models: dict, hooks=()) -> Runtime:
    hooks = [TraceHook(out / "trace.jsonl"), QuoteCheckHook(), *hooks]
    if args.budget:
        hooks.append(BudgetHook(args.budget))
    cache = None if args.no_cache else out / ".cache"
    client = extra = None
    if args.provider == "claude-code":
        from .core.subscription import ClaudeCodeClient

        client = ClaudeCodeClient(effort=args.effort or "medium", cache_dir=cache)
    elif args.effort:
        extra = {"reasoning": {"effort": args.effort}}
    return Runtime({k: v for k, v in models.items() if v}, client=client, cache_dir=cache, extra=extra,
                   skills_dirs=[args.skills] if args.skills else [], hooks=hooks, max_tokens=args.max_tokens)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # PDF names and Thai text on a Windows console
    ap = argparse.ArgumentParser(prog="psc-agent", description="Research agent for perovskite solar cell papers.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pairs", help="show which PDF belongs to which workbooks")
    p.add_argument("--raw", required=True, help="folder of <doi>_Sample<N>.xlsx workbooks")
    p.add_argument("--pdf", required=True, help="folder of source PDFs")

    r = sub.add_parser("recheck", help="recheck existing workbooks against their PDFs")
    r.add_argument("--raw", required=True)
    r.add_argument("--pdf", required=True)
    _model_args(r, "recheck_out")
    r.add_argument("--judge-model", help="model for text equivalence (default: --model)")
    r.add_argument("--no-judge", action="store_true", help="leave differing text as 'review'")
    r.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs")
    r.add_argument("--limit", type=int, help="at most this many papers")
    r.add_argument("--workers", type=int, default=1, help="papers in parallel")
    r.add_argument("--force", action="store_true", help="redo papers that already have a report")
    r.add_argument("--dpi", type=int, default=150)
    r.add_argument("--all-pages", action="store_true", help="send images of every page, not only pages with figures")

    x = sub.add_parser("extract", help="extract workbooks for papers that have none (drafts for review)")
    x.add_argument("pdfs", nargs="+", type=Path)
    _model_args(x, "extract_out")
    x.add_argument("--mapper-model", help="Big model for the paper map (default: --model)")
    x.add_argument("--decision-model", help="small model for accept / verify_more / revise (default: --model)")
    x.add_argument("--no-decide", action="store_true", help="rules only, no decision model")
    x.add_argument("--dpi", type=int, default=150)
    x.add_argument("--all-pages", action="store_true")

    ev = sub.add_parser("evaluate", help="extract papers that already have workbooks, then score the extraction against them")
    ev.add_argument("--raw", default="Raw Data", help="folder of the hand-entered <doi>_Sample<N>.xlsx workbooks (the record)")
    ev.add_argument("--pdf", default="original paper", help="folder of source PDFs")
    _model_args(ev, "evaluate_out")
    ev.add_argument("--mapper-model", help="Big model for the paper map (default: --model)")
    ev.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    ev.add_argument("--exclude", nargs="+", metavar="DOI", help="skip these DOIs")
    ev.add_argument("--limit", type=int, help="at most this many papers")
    ev.add_argument("--workers", type=int, default=1, help="papers in parallel")
    ev.add_argument("--force", action="store_true", help="redo papers that already have an evaluation")
    ev.add_argument("--report-only", action="store_true", help="rebuild the report from finished papers, call no model")
    ev.add_argument("--tol", type=float, default=0.01, help="relative tolerance for numbers (rounding is also forgiven)")
    ev.add_argument("--dpi", type=int, default=150)
    ev.add_argument("--all-pages", action="store_true")
    ev.add_argument("--no-images", action="store_true", help="send the text layer only, no page images")
    ev.add_argument("--audit", action="store_true", help="run auditor agent on non-matching cells")
    ev.add_argument("--audit-model", help="model for auditor agent (default: --model)")

    a = sub.add_parser("analyze", help="find key insights in the processed data (plan → tools → decide → report)")
    a.add_argument("--goal", required=True)
    a.add_argument("--data", required=True, help="folder of processed CSV tables, e.g. processed/prepared_v2")
    a.add_argument("--catalog", help="column catalog CSV (default: <data>/../eda/column_catalog.csv)")
    _model_args(a, "analyze_out", subscription=False)
    a.add_argument("--analyst-model", help="model for the tool loop (default: --model)")
    a.add_argument("--decision-model", help="small model for accept / verify_more / revise (default: --model)")
    a.add_argument("--questions", type=int, default=6)
    a.add_argument("--rounds", type=int, default=2, help="investigation attempts per question")

    m = sub.add_parser("map", help="(re)build the data-map skill")
    m.add_argument("--data", required=True)
    m.add_argument("--skills", default="skills")
    m.add_argument("--catalog")

    t = sub.add_parser("trace", help="digest of trace.jsonl files")
    t.add_argument("traces", nargs="+", type=Path)

    lr = sub.add_parser("learn", help="write the learned-rules skill from trace.jsonl files")
    lr.add_argument("traces", nargs="+", type=Path)
    lr.add_argument("--skills", default="skills")
    lr.add_argument("--model", help="also let this (Big) model condense the digest into rules")

    s = sub.add_parser("skills", help="list skills, or print the system prompt of a role")
    s.add_argument("--skills", default="skills")
    s.add_argument("--role", help="print the composed system prompt of this role")

    w = sub.add_parser("serve", help="run the web service (Flask API + HTML/Tailwind/JS frontend)")
    w.add_argument("--root", default=".", help="project folder holding Raw Data/, original paper/, processed/")
    w.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (there is no login)")
    w.add_argument("--port", type=int, default=5000)
    w.add_argument("--skills", help="project skills folder (default: <root>/agent for research/skills)")
    w.add_argument("--runs", help="job folder (default: <root>/agent for research/runs)")
    w.add_argument("--workers", type=int, default=2, help="jobs that may run at the same time")
    w.add_argument("--dev", action="store_true", help="Flask development server instead of waitress")

    args = ap.parse_args(argv)
    if hasattr(args, "provider"):
        args.model = args.model or DEFAULT_MODEL[args.provider]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return COMMANDS[args.cmd](args)


def cmd_pairs(args) -> int:
    from .sources.pairing import pair_folders

    pairing = pair_folders(args.raw, args.pdf)
    for pair in pairing.pairs:
        note = f"  (ignored duplicate: {', '.join(d.name for d in pair.duplicate_pdfs)})" if pair.duplicate_pdfs else ""
        if pair.pdf_doi:
            note += f"  (TYPO? workbook names differ from PDF DOI {pair.pdf_doi})"
        print(f"{pair.key:32s} {len(pair.workbooks):3d} workbook(s)  {pair.pdf.name}{note}")
    for k, wbs in sorted(pairing.workbooks_without_pdf.items()):
        print(f"{k:32s} {len(wbs):3d} workbook(s)  NO PDF")
    for pdf in pairing.unmatched_pdfs:
        print(f"{'':32s}   0 workbook(s)  {pdf.name}  (DOI not among workbooks)")
    print(f"\n{len(pairing.pairs)} paired, {len(pairing.workbooks_without_pdf)} DOI(s) without PDF, "
          f"{len(pairing.unmatched_pdfs)} PDF(s) without workbooks")
    return 0


def cmd_recheck(args) -> int:
    from .recheck import Rechecker, recheck_folders

    out = Path(args.out)
    rt = _runtime(args, out, {"default": args.model, "judge": args.judge_model})
    rechecker = Rechecker(runtime=rt, judge=not args.no_judge, dpi=args.dpi, all_pages=args.all_pages)
    reports = recheck_folders(rechecker, args.raw, args.pdf, out, only=args.only, limit=args.limit,
                              workers=args.workers, force=args.force)
    total = {s: sum(r.counts().get(s, 0) for r in reports) for s in STATUSES}
    print(f"\n{len(reports)} paper(s) rechecked, ${sum(r.usage.cost_usd for r in reports):.4f}")
    print("  " + ", ".join(f"{s} {n}" for s, n in total.items() if n))
    errors = [f"{r.paper}: {e}" for r in reports for e in r.errors]
    for e in errors:
        print("  error:", e)
    print(f"findings: {out / 'findings.csv'}  trace: {out / 'trace.jsonl'}")
    return 1 if errors else 0


def cmd_extract(args) -> int:
    from .extract import Extractor

    out = Path(args.out)
    rt = _runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "decision": args.decision_model})
    extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=not args.no_decide)
    failed = 0
    for pdf in args.pdfs:
        res = extractor.extract(pdf, out / pdf.stem)
        failed += bool(res.errors)
        print(f"{pdf.name}: {len(res.devices)} device(s), {len(res.rows)} rows, {res.counts()}, "
              f"${res.usage.cost_usd:.4f} -> {out / pdf.stem}")
        for e in res.errors:
            print("  error:", e)
    return 1 if failed else 0


def cmd_evaluate(args) -> int:
    from .evaluate import Auditor, evaluate_pairs, load_eval, write_report
    from .evaluate.report import pct, stats
    from .extract import Extractor
    from .sources.pairing import doi_key, pair_folders

    out = Path(args.out)
    pairs = pair_folders(args.raw, args.pdf).pairs
    if args.only:
        wanted = [doi_key(o) for o in args.only]
        pairs = [p for p in pairs if any(w in p.key for w in wanted)]
    if args.exclude:
        skipped = [doi_key(o) for o in args.exclude]
        pairs = [p for p in pairs if not any(s in p.key for s in skipped)]
    pairs = pairs[: args.limit] if args.limit else pairs
    if not pairs:
        print("no paper selected")
        return 1

    rt = _runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "auditor": args.audit_model}) if (not args.report_only or args.audit) else None
    auditor = Auditor(rt, dpi=args.dpi, all_pages=args.all_pages, images=not args.no_images) if (args.audit and rt) else None

    if args.report_only:
        evals = []
        for p in pairs:
            ev = load_eval(out / p.key)
            if ev:
                if auditor and any(c.kind == "value" and c.status in ("mismatch", "unit_mismatch", "review") and not c.audit_verdict for c in ev.cells):
                    from .evaluate.run import _write
                    from .sources.paper import load_paper
                    paper = load_paper(p.pdf)
                    ev.cells = auditor.audit(paper, ev.cells)
                    _write(out / p.key, ev)
                evals.append(ev)
    else:
        extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=False, images=not args.no_images)
        evals = evaluate_pairs(extractor, pairs, out, workers=args.workers, force=args.force, tol=args.tol, auditor=auditor)
    if not evals:
        print("nothing evaluated yet")
        return 1
    report = write_report(evals, out, {"provider": args.provider, "model": args.model, "tol": args.tol})
    values = stats([c for ev in evals for c in ev.cells if c.kind == "value"])
    print(f"\n{len(evals)} paper(s): {pct(values['match_rate'])} of {values['n']} hand-entered values match "
          f"({pct(values['match_rate_likely'])} of those present in the PDF text)")
    errors = [f"{ev.key}: {e}" for ev in evals for e in ev.errors]
    for e in errors:
        print("  error:", e)
    print(f"report: {report}  cells: {out / 'cells_all.csv'}")
    return 1 if errors else 0


def cmd_analyze(args) -> int:
    from .analyze import Analyst, DataMapHook, DataTools

    out = Path(args.out)
    rt = _runtime(args, out, {"default": args.model, "analyst": args.analyst_model, "decision": args.decision_model},
                  hooks=[DataMapHook(args.data, args.skills, args.catalog)])
    result = Analyst(rt, DataTools(args.data, catalog=args.catalog), max_questions=args.questions,
                     rounds=args.rounds).run(args.goal, out)
    print(f"{len(result.acs)} AC(s): {result.counts()}, ${result.usage.cost_usd:.4f}")
    for e in result.errors:
        print("  error:", e)
    print(f"plan: {out / 'PLAN.md'}  insights: {out / 'INSIGHTS.md'}  trace: {out / 'trace.jsonl'}")
    return 1 if result.errors or not result.acs else 0


def cmd_map(args) -> int:
    from .analyze.datamap import refresh

    refresh(args.data, args.skills, catalog=args.catalog, force=True)
    print(Path(args.skills) / "data-map" / "SKILL.md")
    return 0


def cmd_trace(args) -> int:
    from .core.learn import digest, load, render

    print(render(digest(load(args.traces))))
    return 0


def cmd_learn(args) -> int:
    from .core.learn import learn, load

    rt = Runtime(args.model, skills_dirs=[args.skills]) if args.model else None
    print(learn(load(args.traces), args.skills, runtime=rt))
    return 0


def cmd_skills(args) -> int:
    book = SkillBook(args.skills)
    if args.role:
        print(book.prompt(args.role))
        return 0
    for s in sorted(book.skills.values(), key=lambda s: (s.order, s.name)):
        print(f"{s.order:3d} {s.name:20s} [{', '.join(s.roles)}] {s.description}  ({s.path})")
    return 0


def cmd_serve(args) -> int:
    import os

    from .web import WebConfig, create_app

    app = create_app(WebConfig(root=Path(args.root), skills_dir=args.skills, runs_dir=args.runs,
                               max_workers=args.workers))
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "MISSING (jobs will be refused)"
    print(f"PSC Agent on http://{args.host}:{args.port}  ·  OPENROUTER_API_KEY {key}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: the service has no login; anyone who can reach this address can start paid model runs")
    if args.dev:
        app.run(host=args.host, port=args.port, debug=True, use_reloader=False)
    else:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=8)
    return 0


COMMANDS = {"pairs": cmd_pairs, "recheck": cmd_recheck, "extract": cmd_extract, "evaluate": cmd_evaluate, "analyze": cmd_analyze,
            "map": cmd_map, "trace": cmd_trace, "learn": cmd_learn, "skills": cmd_skills, "serve": cmd_serve}

if __name__ == "__main__":
    sys.exit(main())
