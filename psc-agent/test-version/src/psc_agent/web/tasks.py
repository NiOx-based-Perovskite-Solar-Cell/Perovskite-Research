"""Job functions: request parameters → a psc_agent workflow run in the job's folder."""
from __future__ import annotations

from pathlib import Path

from ..core.hooks import BudgetHook, QuoteCheckHook, TraceHook
from ..core.runtime import Runtime
from .jobs import CancelHook, Job, ProgressHook


def _runtime(cfg, job: Job, models: dict, extra_hooks=()) -> Runtime:
    out = Path(job.out_dir)
    budget = job.params.get("budget")
    hooks = [TraceHook(out / "trace.jsonl"), QuoteCheckHook(), ProgressHook(job), CancelHook(job), *extra_hooks,
             BudgetHook(float(budget if budget not in (None, "") else cfg.default_budget))]
    models = {k: v for k, v in models.items() if v}
    if cfg.runtime_factory:
        return cfg.runtime_factory(models, hooks, cfg)
    if cfg.provider == "gemini":  # whole PDFs, no page images
        from ..core.gemini import GeminiClient

        return Runtime(models, client=GeminiClient(cache_dir=cfg.runs_dir / ".cache"), skills_dirs=[cfg.skills_dir], hooks=hooks,
                       native_pdf=True)
    return Runtime(models, cache_dir=cfg.runs_dir / ".cache", skills_dirs=[cfg.skills_dir], hooks=hooks)


def _models(cfg, params: dict, *roles: str) -> dict:
    out = {"default": params.get("model") or cfg.models["default"]}
    for role in roles:
        out[role] = params.get(f"{role}_model") or cfg.models.get(role)
    return out


def run_recheck(cfg, job: Job) -> dict:
    from ..recheck import Rechecker, recheck_folders

    p = job.params
    rt = _runtime(cfg, job, _models(cfg, p, "judge"))
    rechecker = Rechecker(runtime=rt, judge=not p.get("no_judge"))
    reports = recheck_folders(rechecker, cfg.raw_dir, cfg.pdf_dir, job.out_dir, only=p.get("dois") or None,
                              workers=int(p.get("workers") or 1), force=True, should_stop=job.cancel.is_set)
    counts: dict[str, int] = {}
    for r in reports:
        for status, n in r.counts().items():
            counts[status] = counts.get(status, 0) + n
    return {"papers": len(reports), "counts": counts, "errors": [f"{r.paper}: {e}" for r in reports for e in r.errors]}


def run_si(cfg, job: Job) -> dict:
    """Step 2 of an extraction with a model: it reads the paper's text for its reference to the SI (and the address, when the
    paper prints one). Only when that leaves no link and the publisher's page does not follow from the DOI does the SI agent
    search the web and answer with the addresses it saw (it downloads nothing)."""
    from ..sources.pairing import SI_DIR, local_si
    from ..sources.paper import load_paper
    from ..sources.si import SiFinder, SiReader, intake_key, si_stage

    p = job.params
    rt = _runtime(cfg, job, _models(cfg, p, "si_reader", "si_finder"))
    paper = load_paper(Path(p["pdf_path"]))
    key = intake_key(paper, p["pdf_path"])
    finder = None if cfg.provider == "gemini" else SiFinder(rt, download=False)  # no tool calling on Gemini: no web-search agent
    res = si_stage(paper, key, cfg.pdf_dir / SI_DIR, local_si(cfg.pdf_dir, key), finder, SiReader(rt))
    return {"key": key, "status": res.status, "links": res.links, "notes": res.notes}


def run_si_download(cfg, job: Job) -> dict:
    """Step 2 with the person's own Chrome: the agent server (outside Docker, where Chrome is) finds and downloads the SI
    into ``SI/`` — the folder this app reads. No model runs here; the server pays for its own."""
    from ..sources.paper import load_paper
    from ..sources.si import intake_key

    p = job.params
    path = Path(p["pdf_path"])
    key = intake_key(load_paper(path), path)
    job.add_log("asking the agent server to download the SI with your Chrome: a window opens on your computer; sign in or pass a check there if asked")
    res = cfg.agent_server.download_si(path, key, p.get("si_finder_model"))
    job.add_log(f"{res['status']}: {len(res['files'])} file(s) saved. {res['notes'][:300]}")
    return res


def run_extract(cfg, job: Job) -> dict:
    """The real extraction, after the SI step: the paper is read once together with the SI files saved for it."""
    from ..extract import Extractor
    from ..sources.pairing import local_si
    from ..sources.paper import load_paper
    from ..sources.si import intake_key

    p = job.params
    path = Path(p["pdf_path"])
    paper = load_paper(path)
    si = local_si(cfg.pdf_dir, intake_key(paper, path))
    if si:
        paper = load_paper(path, si=si)
    job.add_log(f"supporting information: {len(si)} file(s) read with the paper" if si else "supporting information: none loaded")
    rt = _runtime(cfg, job, _models(cfg, p, "mapper", "decision"))
    res = Extractor(rt, decide_rows=not p.get("no_decide")).extract(paper, job.out_dir)
    return {"devices": len(res.devices), "rows": len(res.rows), "counts": res.counts(), "si_files": len(si),
            "files": [Path(f).name for f in res.files], "errors": res.errors}


def run_analyze(cfg, job: Job) -> dict:
    from ..analyze import Analyst, DataMapHook, DataTools

    p = job.params
    rt = _runtime(cfg, job, _models(cfg, p, "analyst", "decision"),
                  extra_hooks=[DataMapHook(cfg.data_dir, cfg.skills_dir, cfg.catalog)])
    result = Analyst(rt, DataTools(cfg.data_dir, catalog=cfg.catalog), max_questions=int(p.get("questions") or 4),
                     rounds=int(p.get("rounds") or 2)).run(p["goal"], job.out_dir)
    return {"acs": len(result.acs), "counts": result.counts(), "errors": result.errors}


def run_learn(cfg, job: Job) -> dict:
    from ..core.learn import learn, load

    traces = [Path(t) for t in job.params["traces"]]
    rt = _runtime(cfg, job, {"default": job.params["model"]}) if job.params.get("model") else None
    path = learn(load(traces), cfg.skills_dir, runtime=rt)
    return {"skill": str(path), "traces": len(traces)}


def run_evaluate(cfg, job: Job) -> dict:
    from ..evaluate import Auditor, evaluate_pairs, write_report
    from ..extract import Extractor
    from ..sources.pairing import doi_key, pair_folders

    p = job.params
    rt = _runtime(cfg, job, _models(cfg, p, "mapper", "auditor"))
    extractor = Extractor(rt, decide_rows=False)
    auditor = Auditor(rt) if p.get("audit") else None  # the auditor re-reads every cell that does not match: costs about as much again
    pairs = pair_folders(cfg.raw_dir, cfg.pdf_dir).pairs
    if p.get("dois"):
        wanted = [doi_key(o) for o in p["dois"]]
        pairs = [pair for pair in pairs if any(w in pair.key for w in wanted)]
    if p.get("limit"):
        pairs = pairs[: int(p["limit"])]
    rt.hooks.emit("batch", papers=len(pairs))  # the progress bar counts papers
    evals = evaluate_pairs(extractor, pairs, job.out_dir, workers=int(p.get("workers") or 1),
                           force=bool(p.get("force")), auditor=auditor)
    write_report(evals, job.out_dir, {"provider": cfg.provider, "model": p.get("model") or cfg.models["default"]})
    return {"papers": len(evals), "errors": [e for ev in evals for e in ev.errors]}


TASKS = {"recheck": run_recheck, "si": run_si, "si_download": run_si_download, "extract": run_extract, "analyze": run_analyze, "learn": run_learn, "evaluate": run_evaluate}
