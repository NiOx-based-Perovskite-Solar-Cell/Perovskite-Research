"""Command line: psc-agent pairs | si | run | loop | browser | agent-server | columns | recheck | extract | evaluate | analyze | map | trace | learn | skills | serve."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core.env import load_env
from .core.hooks import BudgetHook, QuoteCheckHook, TraceHook
from .core.runtime import Runtime
from .core.skills import SkillBook
from .recheck.report import STATUSES

DEFAULT_MODEL = {"openrouter": "anthropic/claude-sonnet-5.5", "claude-code": "claude-sonnet-5-5",
                 "gemini": "gemini-3.5-flash", "ollama": "deepseek-v4-pro"}


# `run`: the extract and recheck agents start on Haiku 5.5, the SI agent (web search with tools) on Sonnet 5.5
HAIKU = {"claude-code": "claude-haiku-5-5", "openrouter": "anthropic/claude-haiku-5.5"}
SONNET = {"claude-code": "claude-sonnet-5-5", "openrouter": "anthropic/claude-sonnet-5.5"}


def _model_args(p: argparse.ArgumentParser, out: str) -> None:
    p.add_argument("--provider", choices=list(DEFAULT_MODEL), default="openrouter",
                   help="openrouter = OPENROUTER_API_KEY; claude-code = your Claude subscription through the `claude` CLI, no key "
                        "(Claude models only; tool calling is emulated); gemini = GOOGLE_GEMINI_API_KEY, whole PDFs and schema-"
                        "bound JSON, no tool calling; ollama = OLLAMA_CLOUD_API_KEY (Ollama Cloud, e.g. deepseek-v4-pro)")
    p.add_argument("--model", help="default model id for every role (image input + structured output); default per provider: "
                                   + ", ".join(f"{k} {v}" for k, v in DEFAULT_MODEL.items()))
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="thinking effort (claude-code: default medium; openrouter: sent as reasoning.effort when given; "
                        "gemini: thinkingLevel low/medium/high when given)")
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
    elif args.provider == "gemini":
        from .core.gemini import GeminiClient

        client = GeminiClient(effort=args.effort, cache_dir=cache)
    elif args.provider == "ollama":
        from .core.ollama import OllamaCloudClient

        client = OllamaCloudClient(cache_dir=cache)
    elif args.effort:
        extra = {"reasoning": {"effort": args.effort}}
    return Runtime({k: v for k, v in models.items() if v}, client=client, cache_dir=cache, extra=extra,
                   skills_dirs=[args.skills] if args.skills else [], hooks=hooks, max_tokens=args.max_tokens,
                   native_pdf=args.provider == "gemini")  # Gemini reads whole PDFs: no page images


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # PDF names and Thai text on a Windows console
    load_env(Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")  # the working folder, then the project folder
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
    r.add_argument("--no-si", action="store_true", help="ignore the supporting information found in <pdf folder>/SI/")
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
    x.add_argument("--no-si", action="store_true", help="ignore the supporting information found in <pdf folder>/SI/")

    cm = sub.add_parser("columns", help="print every column of the processed tables with its meaning and how much the analysis needs it")
    cm.add_argument("--data", default="processed/prepared_v2", help="folder of the processed CSV tables")
    cm.add_argument("--catalog", help="column catalog CSV (default: <data>/../eda/column_catalog.csv)")
    cm.add_argument("--notes", help="column notes CSV (default: <data>/../eda/column_notes.csv)")
    cm.add_argument("--table", nargs="+", help="only these tables")
    cm.add_argument("--relevance", nargs="+", choices=["core", "context", "provenance", "drop", "none"], help="only columns rated so")
    cm.add_argument("--format", choices=["text", "md", "csv"], default="text")
    cm.add_argument("--out", help="write to this file instead of the terminal")

    rn = sub.add_parser("run", help="per paper: check for an SI, find it if needed, then ONE reading by the extract or recheck agent")
    rn.add_argument("--raw", required=True, help="folder of <doi>_Sample<N>.xlsx workbooks")
    rn.add_argument("--pdf", required=True, help="folder of source PDFs (SI files live in <pdf folder>/SI/)")
    _model_args(rn, "run_out")
    rn.add_argument("--mode", choices=["auto", "extract", "recheck"], default="auto",
                    help="auto: recheck papers that have workbooks, extract the PDFs that have none")
    rn.add_argument("--si-model", help="model of the SI agent (default: Sonnet 5.5; with tools it follows instructions more reliably than Haiku)")
    rn.add_argument("--extract-model", help="model of the extract agent, mapper included (default: --model)")
    rn.add_argument("--recheck-model", help="model of the recheck agent and its judge (default: --model)")
    rn.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    rn.add_argument("--limit", type=int, help="at most this many papers")
    rn.add_argument("--workers", type=int, default=1, help="papers in parallel (1 with --browser)")
    rn.add_argument("--no-si", action="store_true", help="ignore the SI: no check, no search, no SI files")
    rn.add_argument("--no-si-agent", action="store_true", help="never call the SI agent: papers whose SI link does not follow from the DOI get the DOI link")
    rn.add_argument("--no-si-reader", action="store_true", help="do not let a model read the paper for its SI reference (the SI model is --model): a text rule decides whether it refers to one")
    rn.add_argument("--browser", action="store_true", help="the SI agent downloads through your signed-in Chrome (see `browser login`)")
    rn.add_argument("--chrome-profile", help="Chrome profile folder for --browser")
    rn.add_argument("--cdp-url", help="attach to a Chrome started with --remote-debugging-port")
    rn.add_argument("--wait-human", type=float, default=300.0, help="seconds to leave a bot check open for you (default 300)")
    rn.add_argument("--no-judge", action="store_true", help="recheck: leave differing text as 'review'")
    rn.add_argument("--no-decide", action="store_true", help="extract: rules only, no decision model")
    rn.add_argument("--dpi", type=int, default=150)
    rn.add_argument("--all-pages", action="store_true")

    lp = sub.add_parser("loop", help="papers in, trusted rows out, no person in the loop: extract, a second reader (ideally another "
                                     "model), a precision gate, staging")
    lp.add_argument("--raw", required=True, help="folder of <doi>_Sample<N>.xlsx workbooks (papers that have some are not extracted)")
    lp.add_argument("--pdf", required=True, help="folder of source PDFs (SI files live in <pdf folder>/SI/)")
    _model_args(lp, "loop_out")
    lp.add_argument("--verifier-provider", choices=list(DEFAULT_MODEL), help="provider of the second reader (default: --provider)")
    lp.add_argument("--verifier-model", help="model of the second reader (default: the provider's default when it is another provider "
                                             "than --provider, else --model: then it is the same model, which repeats its own mistakes)")
    lp.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    lp.add_argument("--limit", type=int, help="at most this many papers")
    lp.add_argument("--workers", type=int, default=1, help="papers in parallel")
    lp.add_argument("--rounds", type=int, default=2, help="how often a failed sheet group or second reading is asked again (default 2)")
    lp.add_argument("--min-precision", type=float, default=0.9, help="the gate: staged rows need a calibrated precision of at least this")
    lp.add_argument("--sample", type=float, default=0.05, help="share of the accepted rows written to audit_sample.csv (blocks nothing)")
    lp.add_argument("--calibrate", action="store_true",
                    help="instead of staging: run on papers that HAVE hand-entered workbooks, measure the accepted rows against them "
                         "and write the calibration the gate needs")
    lp.add_argument("--calibration", default="calibration.json", help="where the calibration is written and read (default ./calibration.json)")
    lp.add_argument("--no-si-agent", action="store_true", help="never call the SI web-search agent")
    lp.add_argument("--no-si-reader", action="store_true", help="a text rule instead of a model decides whether the paper refers to an SI")
    lp.add_argument("--dpi", type=int, default=150)

    br = sub.add_parser("browser", help="the Chrome window the agent uses: sign in to the publishers once")
    br.add_argument("action", choices=["login"], help="login: open the window, sign in by hand, press Enter here")
    br.add_argument("--url", default="about:blank", help="a page to open first, e.g. the login page of your library")
    br.add_argument("--chrome-profile", help="profile folder (default ~/.psc-agent/chrome)")

    si = sub.add_parser("si", help="find and download the supporting information (SI) of papers, with web search")
    si.add_argument("--raw", required=True, help="folder of <doi>_Sample<N>.xlsx workbooks")
    si.add_argument("--pdf", required=True, help="folder of source PDFs; SI is saved in <pdf folder>/SI/")
    _model_args(si, "si_out")
    si.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    si.add_argument("--limit", type=int, help="at most this many papers")
    si.add_argument("--workers", type=int, default=1, help="papers in parallel")
    si.add_argument("--force", action="store_true", help="search again for papers that already have an SI file")
    si.add_argument("--browser", action="store_true",
                    help="let the agent use your Chrome (a window of its own, signed in once with `browser login`) to open "
                         "publisher pages and download the SI; papers run one after another")
    si.add_argument("--chrome-profile", help="Chrome profile folder for --browser (default ~/.psc-agent/chrome)")
    si.add_argument("--cdp-url", help="attach to a Chrome you started with --remote-debugging-port instead (e.g. http://127.0.0.1:9222)")
    si.add_argument("--wait-human", type=float, default=300.0,
                    help="seconds to leave a bot-check or sign-in page open for you to solve it in the window (default 300)")
    si.add_argument("--urls-only", action="store_true",
                    help="the agent searches the web but downloads nothing: it answers with the SI addresses (the file or its "
                         "page) for you to open; fits any provider, e.g. --provider ollama --model deepseek-v4-pro")
    si.add_argument("--links-only", action="store_true", help="no web search and no model: just list the pages to open")

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
    ev.add_argument("--rescore", action="store_true",
                    help="score the saved extractions again with the current comparison rules, call no model (use it on a copy "
                         "of an old run so two runs are scored by the same rules)")
    ev.add_argument("--report-only", action="store_true", help="rebuild the report from finished papers, call no model")
    ev.add_argument("--tol", type=float, default=0.01, help="relative tolerance for numbers (rounding is also forgiven)")
    ev.add_argument("--dpi", type=int, default=150)
    ev.add_argument("--all-pages", action="store_true")
    ev.add_argument("--no-images", action="store_true", help="send the text layer only, no page images")
    ev.add_argument("--no-si", action="store_true", help="ignore the supporting information found in <pdf folder>/SI/")
    ev.add_argument("--audit", action="store_true", help="run auditor agent on non-matching cells")
    ev.add_argument("--audit-model", help="model for auditor agent (default: --model)")

    cp = sub.add_parser("compare", help="put two `evaluate` runs side by side (two models, or with and without the SI)")
    cp.add_argument("run_a", type=Path, help="output folder of the first evaluate run (the baseline)")
    cp.add_argument("run_b", type=Path, help="output folder of the second evaluate run")
    cp.add_argument("--labels", nargs=2, metavar=("A", "B"), help="names for the two runs (default: the folder names)")
    cp.add_argument("--out", type=Path, help="report file (default: <run_b>/vs_<run_a>.md)")

    a = sub.add_parser("analyze", help="find key insights in the processed data (plan → tools → decide → report)")
    a.add_argument("--goal", required=True)
    a.add_argument("--data", required=True, help="folder of processed CSV tables, e.g. processed/prepared_v2")
    a.add_argument("--catalog", help="column catalog CSV (default: <data>/../eda/column_catalog.csv)")
    _model_args(a, "analyze_out")
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

    ag = sub.add_parser("agent-server", help="the SI agent with your own Chrome as a small server outside Docker; the web app calls it "
                                             "(needs AGENT_SERVER_TOKEN in .env)")
    ag.add_argument("--pdf", required=True, help="folder of source PDFs, the one the web app reads; SI is saved in <pdf folder>/SI/")
    ag.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (Docker Desktop reaches it as host.docker.internal)")
    ag.add_argument("--port", type=int, default=5100)
    ag.add_argument("--model", default=DEFAULT_MODEL["openrouter"], help="model of the SI agent (OpenRouter; the key comes from .env)")
    ag.add_argument("--budget", type=float, default=1.0, help="USD per paper")
    ag.add_argument("--chrome-profile", help="Chrome profile folder (default ~/.psc-agent/chrome; sign in once with `browser login`)")
    ag.add_argument("--cdp-url", help="attach to a Chrome you started with --remote-debugging-port instead (e.g. http://127.0.0.1:9222)")
    ag.add_argument("--wait-human", type=float, default=300.0,
                    help="seconds to leave a bot-check or sign-in page open for you to solve it in the window (default 300)")

    w = sub.add_parser("serve", help="run the web service (Flask API + HTML/Tailwind/JS frontend)")
    w.add_argument("--root", default=".", help="project folder holding Raw Data/, original paper/, processed/")
    w.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (there is no login)")
    w.add_argument("--port", type=int, default=5000)
    w.add_argument("--skills", help="project skills folder (default: <root>/agent for research/skills)")
    w.add_argument("--runs", help="job folder (default: <root>/agent for research/runs)")
    w.add_argument("--workers", type=int, default=2, help="jobs that may run at the same time")
    w.add_argument("--provider", dest="web_provider", choices=["openrouter", "gemini"],  # not `provider`: main() gives those a --model default
                   help="who answers the model calls (default: $PSC_PROVIDER, else openrouter); gemini = gemini-3.5-flash for every role, "
                        "no analyze and no web-search agent")
    w.add_argument("--dev", action="store_true", help="Flask development server instead of waitress")

    args = ap.parse_args(argv)
    if hasattr(args, "provider"):
        args.model = args.model or (HAIKU.get(args.provider) if args.cmd == "run" else None) or DEFAULT_MODEL[args.provider]
        if args.cmd == "run":
            args.si_model = args.si_model or SONNET.get(args.provider)
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


def cmd_columns(args) -> int:
    from .analyze.columns import profile, to_csv, to_markdown, to_text

    cols = profile(args.data, catalog=args.catalog, notes=args.notes)
    if args.table:
        cols = [c for c in cols if c.table in args.table]
    if args.relevance:
        cols = [c for c in cols if (c.relevance or "none") in args.relevance]
    if not cols:
        print("no column matches")
        return 1
    text = {"text": to_text, "md": to_markdown, "csv": to_csv}[args.format](cols)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8-sig" if args.format == "csv" else "utf-8")
        print(f"{len(cols)} column(s) -> {args.out}")
    else:
        print(text)
    return 0


def cmd_browser(args) -> int:
    from .core.browser import DEFAULT_PROFILE, Browser, BrowserError, find_chrome

    if not find_chrome():
        print("Chrome was not found: install it or set CHROME_BIN")
        return 1
    try:
        Browser(args.chrome_profile or DEFAULT_PROFILE).login(args.url)
    except BrowserError as e:
        print(f"error: {e}")
        return 1
    print("signed in sessions are kept in the profile; run `psc-agent si --browser ...`")
    return 0


def cmd_run(args) -> int:
    from .extract import Extractor
    from .recheck import Rechecker
    from .runner import collect, run_items
    from .sources.si import SiFinder, SiReader

    out = Path(args.out)
    items = collect(args.raw, args.pdf, only=args.only, limit=args.limit, mode=args.mode)
    if not items:
        print("no paper selected")
        return 1
    models = {"default": args.model, "si_finder": args.si_model, "mapper": args.extract_model, "extract": args.extract_model,
              "recheck": args.recheck_model, "judge": args.recheck_model}
    rt = _runtime(args, out, models)
    browser = None
    if args.browser and not args.no_si_agent:
        from .core.browser import DEFAULT_PROFILE, Browser

        browser = Browser(args.chrome_profile or DEFAULT_PROFILE, cdp_url=args.cdp_url, wait_human=args.wait_human)
        args.workers = 1
    if args.provider == "gemini" and not args.no_si_agent:  # the Gemini client has no tool calling: no web-search agent
        print("note: --provider gemini cannot run the SI web-search agent; the SI reader (Gemini) and the publisher patterns still run")
        args.no_si_agent = True
    finder = None if args.no_si_agent else SiFinder(rt, browser=browser, download=browser is not None)
    reader = None if args.no_si_reader else SiReader(rt)
    extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=not args.no_decide)
    rechecker = Rechecker(runtime=rt, judge=not args.no_judge, dpi=args.dpi, all_pages=args.all_pages)
    print(f"{len(items)} paper(s): {sum(bool(i.workbooks) for i in items)} to recheck, {sum(not i.workbooks for i in items)} to extract; "
          f"every agent on {args.model} unless set per role")
    try:
        outcomes = run_items(items, finder=finder, extractor=extractor, rechecker=rechecker, out=out, use_si=not args.no_si,
                             workers=args.workers, reader=reader)
    finally:
        if browser:
            browser.close()
    for o in outcomes:
        print(f"  {o.item.key:30s} {o.mode:8s} SI {o.si.status:15s} {o.counts}" + (f"  ERRORS: {len(o.errors)}" if o.errors else ""))
    print(f"\n{len(outcomes)} paper(s), ${sum(o.cost for o in outcomes):.2f} as reported, tokens/cost per call in {out / 'trace.jsonl'}")
    print(f"summary: {out / 'run_summary.csv'}  SI to download by hand: {out / 'si_links.md'}")
    return 1 if any(o.errors for o in outcomes) else 0


def cmd_loop(args) -> int:
    import argparse as _argparse

    from .autoloop import calibrate, gate_status, run_loop
    from .evaluate.report import pct
    from .extract import Extractor
    from .extract.verify import Verifier
    from .recheck import Rechecker
    from .runner import collect
    from .sources.si import SiFinder, SiReader

    out = Path(args.out)
    items = collect(args.raw, args.pdf, only=args.only, limit=args.limit, mode="recheck" if args.calibrate else "extract")
    if not items:
        print("no paper selected" + ("" if args.calibrate else " (papers that already have workbooks are not extracted: use --calibrate to measure on them)"))
        return 1
    rt = _runtime(args, out, {"default": args.model, "si_finder": SONNET.get(args.provider)})
    v_provider = args.verifier_provider or args.provider
    v_model = args.verifier_model or (DEFAULT_MODEL[v_provider] if v_provider != args.provider else args.model)
    vargs = _argparse.Namespace(**{**vars(args), "provider": v_provider, "model": v_model})
    (out / "verifier").mkdir(parents=True, exist_ok=True)
    vrt = _runtime(vargs, out / "verifier", {"default": v_model, "recheck": v_model, "judge": v_model})
    if (v_provider, v_model) == (args.provider, args.model):
        print("note: the second reader is the same model as the extractor; give --verifier-model or --verifier-provider for an independent one")
    finder = None if args.no_si_agent or args.provider == "gemini" else SiFinder(rt, download=False)
    reader = None if args.no_si_reader else SiReader(rt)
    extractor = Extractor(rt, dpi=args.dpi)
    verifier = Verifier(Rechecker(runtime=vrt, dpi=args.dpi))
    kw = dict(finder=finder, reader=reader, extractor=extractor, verifier=verifier, out=out, rounds=args.rounds, workers=args.workers)
    print(f"{len(items)} paper(s) · extract {args.model} ({args.provider}) · second reader {v_model} ({v_provider})")
    if args.calibrate:
        cal = calibrate(items, calibration=Path(args.calibration), **kw)
        a, b = cal["all_rows"], cal["accepted"]
        print(f"calibration on {cal['papers']} paper(s), {a['n']} hand-entered values -> {args.calibration}")
        print(f"  extractor alone : found {pct(a['found_rate'])}, correct when found {pct(a['accuracy_when_found'])}")
        print(f"  accepted by both: found {pct(b['found_rate'])}, correct when found {pct(b['accuracy_when_found'])}")
        print("gate:", gate_status(Path(args.calibration), args.min_precision, args.model, v_model)[1])
        return 0
    loops, open_, why = run_loop(items, calibration=Path(args.calibration), min_precision=args.min_precision, sample=args.sample, **kw)
    for lp in loops:
        print(f"  {lp.key[:40]:40s} rows {len(lp.records):4d}  accepted {lp.accepted:4d}  held {lp.held:4d}" + (f"  ERRORS {len(lp.errors)}" if lp.errors else ""))
    print(f"\ngate {'OPEN' if open_ else 'CLOSED'}: {why}\nreport: {out / 'REPORT.md'}")
    return 1 if any(lp.errors for lp in loops) else 0


def cmd_si(args) -> int:
    from concurrent.futures import ThreadPoolExecutor

    from .sources.pairing import SI_DIR, doi_key, pair_folders
    from .sources.paper import load_paper
    from .sources.si import SiFinder, SiResult, supplement_links, write_si_reports

    out = Path(args.out)
    pairs = pair_folders(args.raw, args.pdf).pairs
    if args.only:
        wanted = [doi_key(o) for o in args.only]
        pairs = [p for p in pairs if any(w in p.key for w in wanted)]
    have = [p for p in pairs if p.si]
    todo = pairs if args.force else [p for p in pairs if not p.si]
    todo = todo[: args.limit] if args.limit else todo
    print(f"{len(pairs)} paper(s): {len(have)} already have an SI file, {len(todo)} to search")
    if not todo:
        return 0
    browser = None
    if args.browser and not args.links_only:
        from .core.browser import DEFAULT_PROFILE, Browser

        browser = Browser(args.chrome_profile or DEFAULT_PROFILE, cdp_url=args.cdp_url, wait_human=args.wait_human)
        args.workers = 1  # one window, one thread
    finder = None if args.links_only else SiFinder(_runtime(args, out, {"default": args.model}), browser=browser,
                                                    download=not args.urls_only)
    si_dir = Path(args.pdf) / SI_DIR

    def one(pair):
        paper = load_paper(pair.pdf)
        if finder is None:  # no search: the publisher's pages for a person to open
            return pair, SiResult(pair.key, "manual", doi=paper.doi, links=supplement_links(paper.doi))
        return pair, finder.find(paper, si_dir, key=pair.key)

    try:
        if browser:  # Playwright belongs to the thread that started it: no pool
            results = [one(p) for p in todo]
        else:
            with ThreadPoolExecutor(max(1, args.workers)) as pool:
                results = list(pool.map(one, todo))
    finally:
        if browser:
            browser.close()
    by_hand = write_si_reports([(pair.key, pair.pdf.name, r) for pair, r in results], out, si_dir)
    for status in ("found", "links", "blocked", "not_found", "error", "manual"):
        n = sum(r.status == status for _, r in results)
        if n:
            print(f"  {status}: {n}")
    for key, _, r in by_hand:
        print(f"\n{key}  ->  {key}_SI.pdf\n  " + "\n  ".join(r.links or ["(no DOI found in the PDF)"]))
    print(f"\nreport: {out / 'si_report.csv'}  links: {out / 'si_links.md'}")
    return 0


def cmd_recheck(args) -> int:
    from .recheck import Rechecker, recheck_folders

    out = Path(args.out)
    rt = _runtime(args, out, {"default": args.model, "judge": args.judge_model})
    rechecker = Rechecker(runtime=rt, judge=not args.no_judge, dpi=args.dpi, all_pages=args.all_pages)
    reports = recheck_folders(rechecker, args.raw, args.pdf, out, only=args.only, limit=args.limit,
                              workers=args.workers, force=args.force, use_si=not args.no_si)
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
    from .sources.pairing import doi_key, local_si
    from .sources.paper import load_paper

    out = Path(args.out)
    rt = _runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "decision": args.decision_model})
    extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=not args.no_decide)
    failed = 0
    for pdf in args.pdfs:
        folder = out / pdf.stem[:40].strip()
        paper = load_paper(pdf)
        si = [] if args.no_si else local_si(pdf.parent, doi_key(paper.doi or ""))
        res = extractor.extract(load_paper(pdf, si=si) if si else paper, folder)
        failed += bool(res.errors)
        print(f"{pdf.name}: {len(res.devices)} device(s), {len(res.rows)} rows, {res.counts()}, "
              f"${res.usage.cost_usd:.4f} -> {folder}")
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

    needs_model = args.audit or not (args.report_only or args.rescore)
    rt = _runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "auditor": args.audit_model}) if needs_model else None
    auditor = Auditor(rt, dpi=args.dpi, all_pages=args.all_pages, images=not args.no_images) if (args.audit and rt) else None

    if args.rescore:  # score the saved extractions again with the current rules: no model call
        from .evaluate.run import rescore_paper

        evals = [ev for ev in (rescore_paper(p, out, tol=args.tol, use_si=not args.no_si) for p in pairs) if ev]
    elif args.report_only:
        evals = []
        for p in pairs:
            ev = load_eval(out / p.key)
            if ev:
                if auditor and any(c.kind == "value" and c.status in ("mismatch", "unit_mismatch", "review") and not c.audit_verdict for c in ev.cells):
                    from .evaluate.run import _write
                    from .sources.paper import load_paper
                    paper = load_paper(p.pdf, si=[] if args.no_si else p.si)
                    ev.cells = auditor.audit(paper, ev.cells)
                    _write(out / p.key, ev)
                evals.append(ev)
    else:
        extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=False, images=not args.no_images)
        evals = evaluate_pairs(extractor, pairs, out, workers=args.workers, force=args.force, tol=args.tol, auditor=auditor,
                               use_si=not args.no_si)
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


def cmd_compare(args) -> int:
    from .evaluate.report import pct
    from .evaluate.versus import compare_runs, load_run, write_comparison

    runs = []
    for folder in (args.run_a, args.run_b):
        if not folder.is_dir():
            print(f"no such folder: {folder}")
            return 1
        runs.append(load_run(folder))
    labels = tuple(args.labels or (args.run_a.name, args.run_b.name))
    r = compare_runs(*runs)
    if not r["papers"]:
        print(f"no paper is finished in both runs ({len(runs[0])} in A, {len(runs[1])} in B)")
        return 1
    out = write_comparison(*runs, labels, args.out or args.run_b / f"vs_{args.run_a.name}.md")
    va, vb = r["a"]["values"], r["b"]["values"]
    print(f"{len(r['papers'])} paper(s) in both: {labels[0]} {pct(va['match_rate'])} vs {labels[1]} {pct(vb['match_rate'])} of "
          f"{va['n']} hand-entered values match ({pct(va['match_rate_likely'])} vs {pct(vb['match_rate_likely'])} of those in the PDF text)")
    print(f"report: {out}")
    return 0


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


def cmd_agent_server(args) -> int:
    import os
    import secrets

    from waitress import serve

    from .web.agent_server import create_agent_app

    token = os.environ.get("AGENT_SERVER_TOKEN")
    if not token:
        print("AGENT_SERVER_TOKEN is not set. The server drives your signed-in Chrome, so every request must carry a secret.\n"
              "Add this line to .env (the web app reads the same file; restart it after):\n"
              f"  AGENT_SERVER_TOKEN={secrets.token_urlsafe(24)}")
        return 2
    app = create_agent_app(args.pdf, token, model=args.model, budget=args.budget, profile=args.chrome_profile,
                           cdp_url=args.cdp_url, wait_human=args.wait_human)
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "MISSING (downloads will fail)"
    print(f"agent server on http://{args.host}:{args.port}  ·  SI saved in {Path(args.pdf) / 'SI'}  ·  OPENROUTER_API_KEY {key}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: anyone on the network who has the token can drive your Chrome")
    serve(app, host=args.host, port=args.port, threads=4)
    return 0


def cmd_serve(args) -> int:
    import os

    from .web import WebConfig, create_app
    from .web.agent_server import AgentServer

    url, token = os.environ.get("AGENT_SERVER_URL"), os.environ.get("AGENT_SERVER_TOKEN")
    agent = AgentServer(url, token) if url and token else None
    cfg = WebConfig(root=Path(args.root), skills_dir=args.skills, runs_dir=args.runs, max_workers=args.workers, agent_server=agent,
                    provider=args.web_provider or os.environ.get("PSC_PROVIDER") or "openrouter")
    app = create_app(cfg)
    key = "set" if any(os.environ.get(n) for n in cfg.key_names) else "MISSING (jobs will be refused)"
    print(f"PSC Agent on http://{args.host}:{args.port}  ·  {cfg.provider} ({cfg.models['default']})  ·  {cfg.key_names[0]} {key}" + (f"  ·  agent server {url}" if agent else ""))
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: the service has no login; anyone who can reach this address can start paid model runs")
    if args.dev:
        app.run(host=args.host, port=args.port, debug=True, use_reloader=False)
    else:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=8)
    return 0


COMMANDS = {"pairs": cmd_pairs, "si": cmd_si, "run": cmd_run, "loop": cmd_loop, "browser": cmd_browser, "agent-server": cmd_agent_server, "columns": cmd_columns, "recheck": cmd_recheck, "extract": cmd_extract, "evaluate": cmd_evaluate, "compare": cmd_compare, "analyze": cmd_analyze,
            "map": cmd_map, "trace": cmd_trace, "learn": cmd_learn, "skills": cmd_skills, "serve": cmd_serve}

if __name__ == "__main__":
    sys.exit(main())
