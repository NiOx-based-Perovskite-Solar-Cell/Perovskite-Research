"""REST API of the web service. JSON in and out (except file downloads); errors are {"error": "..."}."""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from ..core.skills import DEFAULT_DIR, SkillBook
from .tasks import TASKS

bp = Blueprint("api", __name__, url_prefix="/api")
ROLES = ("recheck", "mapper", "extract", "judge", "decision", "planner", "analyst", "reporter", "learner", "auditor", "si_finder", "si_reader")
_MODEL = re.compile(r"^[\w.\-/:]{1,200}$")
_SKILL = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
READ_ONLY_SKILLS = {"data-map"}  # rebuilt from the data on every analyze run


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@bp.errorhandler(ApiError)
def _api_error(e: ApiError):
    return jsonify(error=str(e)), e.status


def _cfg():
    return current_app.config["PSC"]


def _jobs():
    return current_app.config["JOBS"]


def _rows(path: Path, limit: int = 20000) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [row for _, row in zip(range(limit), csv.DictReader(fh))]


# ------------------------------------------------------------------ overview


@bp.get("/status")
def status():
    cfg = _cfg()
    return jsonify(key_set=any(os.environ.get(n) for n in cfg.key_names), key_name=cfg.key_names[0], provider=cfg.provider,
                   require_key=cfg.require_key, models=cfg.models,
                   default_budget=cfg.default_budget, roles=ROLES,
                   paths={k: str(getattr(cfg, k)) for k in ("root", "raw_dir", "pdf_dir", "data_dir", "skills_dir",
                                                            "runs_dir")})


def _pairing():
    """pair_folders reads every PDF; reuse it until a file in either folder changes."""
    cfg = _cfg()
    stamp = tuple((p.name, p.stat().st_mtime_ns) for d in (cfg.raw_dir, cfg.pdf_dir) if d.is_dir()
                  for p in sorted(d.iterdir()))
    cache = current_app.config.setdefault("PAIRING_CACHE", {})
    if cache.get("stamp") != stamp:
        from ..sources.pairing import pair_folders

        cache.update(stamp=stamp, pairing=pair_folders(cfg.raw_dir, cfg.pdf_dir))
    return cache["pairing"]


@bp.get("/pairs")
def pairs():
    pr = _pairing()
    return jsonify(
        pairs=[{"key": p.key, "pdf": p.pdf.name, "workbooks": len(p.workbooks), "si": len(p.si), "si_files": [f.name for f in p.si], "devices": [w.stem for w in p.workbooks],
                "typo_pdf_doi": p.pdf_doi, "duplicates": [d.name for d in p.duplicate_pdfs]} for p in pr.pairs],
        workbooks_without_pdf=[{"key": k, "workbooks": len(v)} for k, v in sorted(pr.workbooks_without_pdf.items())],
        unmatched_pdfs=[p.name for p in pr.unmatched_pdfs])


@bp.get("/pdfs")
def pdfs():
    cfg = _cfg()
    pr = _pairing()
    paired = {p.pdf.name for p in pr.pairs} | {d.name for p in pr.pairs for d in p.duplicate_pdfs}
    items = [{"id": f"pdf:{p.name}", "name": p.name, "has_workbooks": p.name in paired}
             for p in sorted(cfg.pdf_dir.glob("*.pdf"))]
    if cfg.uploads_dir.is_dir():
        items += [{"id": f"upload:{p.name}", "name": p.name, "has_workbooks": False, "uploaded": True}
                  for p in sorted(cfg.uploads_dir.glob("*.pdf"))]
    return jsonify(pdfs=items)


def _resolve_pdf(file_id: str) -> Path:
    cfg = _cfg()
    kind, _, name = file_id.partition(":")
    base = {"pdf": cfg.pdf_dir, "upload": cfg.uploads_dir}.get(kind)
    if base is None or not name:
        raise ApiError("pdf must be 'pdf:<file name>' or 'upload:<file name>'")
    path = (base / name).resolve()
    if path.parent != base.resolve() or path.suffix.lower() != ".pdf" or not path.is_file():
        raise ApiError(f"no such PDF: {name}", 404)
    return path


@bp.post("/uploads")
def uploads():
    cfg = _cfg()
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".pdf"):
            raise ApiError(f"only PDF files can be uploaded: {f.filename}")
        if f.stream.read(5) != b"%PDF-":
            raise ApiError(f"not a PDF: {f.filename}")
        f.stream.seek(0)
        target, n = cfg.uploads_dir / name, 1
        while target.exists():
            target, n = cfg.uploads_dir / f"{Path(name).stem}-{n}.pdf", n + 1
        f.save(target)
        saved.append({"id": f"upload:{target.name}", "name": target.name})
    if not saved:
        raise ApiError("no files in the 'files' field")
    return jsonify(files=saved), 201


# ------------------------------------------------------------------ supporting information


_SI_KEY = re.compile(r"[0-9a-z]{6,80}")
_SI_NAME = re.compile(r"[0-9a-z]{6,80}_SI\d{0,2}\.pdf")


def _si_dir() -> Path:
    from ..sources.pairing import SI_DIR

    return _cfg().pdf_dir / SI_DIR


@bp.get("/si/plan")
def si_plan():
    """Step 2 of an extraction: what to do about this paper's SI before it is read. Local checks only, no model."""
    from ..sources.pairing import local_si
    from ..sources.paper import load_paper
    from ..sources.si import intake_key, si_stage

    path = _resolve_pdf(request.args.get("pdf", ""))
    paper = load_paper(path)
    key = intake_key(paper, path)
    have = local_si(_cfg().pdf_dir, key)
    r = si_stage(paper, key, _si_dir(), have, None)
    status = "agent_needed" if r.status == "manual" else r.status  # have | not_referenced | pattern | agent_needed
    return jsonify(key=key, doi=paper.doi, status=status, links=r.links, notes=r.notes, save_as=f"{key}_SI.pdf",
                   files=[{"name": f.name, "size": f.stat().st_size} for f in have])


@bp.get("/agent-server")
def agent_server_status():
    """Is there an agent server (a Chrome-driving agent outside Docker) and does it answer? Never exposes the token."""
    from .agent_server import AgentServerError

    srv = _cfg().agent_server
    if srv is None:
        return jsonify(configured=False)
    try:
        return jsonify(configured=True, reachable=True, url=srv.url, chrome=bool(srv.health().get("chrome")))
    except AgentServerError as e:
        return jsonify(configured=True, reachable=False, url=srv.url, error=str(e))


@bp.get("/si")
def list_si():
    d = _si_dir()
    return jsonify(files=[{"name": p.name, "size": p.stat().st_size} for p in sorted(d.glob("*.pdf"))] if d.is_dir() else [])


@bp.post("/si")
def upload_si():
    """Save the supporting information of one paper as ``<pdf folder>/SI/<doi key>_SI.pdf`` (``_SI2`` ... for more
    files). The key is the DOI as lowercase letters and digits (a DOI itself is accepted); pairing finds the file by it,
    so the next recheck / extract / evaluate reads it."""
    import fitz

    from ..sources.pairing import doi_key

    key = doi_key(request.form.get("key", ""))
    if not _SI_KEY.fullmatch(key):
        raise ApiError("give the paper's DOI key (its DOI as letters and digits)")
    files = request.files.getlist("files")
    if not files:
        raise ApiError("no files in the 'files' field")
    d = _si_dir()
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        head = f.stream.read(5)
        if head != b"%PDF-":
            raise ApiError(f"not a PDF: {f.filename}")
        f.stream.seek(0)
        data = f.stream.read()
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                pages = len(doc)
        except Exception:
            raise ApiError(f"the PDF cannot be read: {f.filename}") from None
        n = 1
        target = d / f"{key}_SI.pdf"
        while target.exists():
            n += 1
            if n > 99:
                raise ApiError("too many SI files for this paper")
            target = d / f"{key}_SI{n}.pdf"
        target.write_bytes(data)
        saved.append({"name": target.name, "pages": pages, "size": len(data)})
    paired = any(p.key == key for p in _pairing().pairs)
    return jsonify(key=key, files=saved, paired=paired), 201


@bp.delete("/si/<name>")
def delete_si(name):
    if not _SI_NAME.fullmatch(name):
        raise ApiError("not an SI file name", 404)
    path = _si_dir() / name
    if not path.is_file():
        raise ApiError(f"no such SI file: {name}", 404)
    path.unlink()
    return jsonify(deleted=name)


# ------------------------------------------------------------------ jobs


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@bp.post("/jobs")
def create_job():
    cfg = _cfg()
    body = request.get_json(silent=True) or {}
    kind, params = body.get("kind"), dict(body.get("params") or {})
    if kind not in TASKS:
        raise ApiError(f"kind must be one of: {', '.join(TASKS)}")
    for k, v in params.items():
        if k.endswith("model") and v and not _MODEL.match(str(v)):
            raise ApiError(f"bad model id: {v}")
    needs_model = kind != "si_download" and (kind != "learn" or params.get("model"))  # si_download: the agent server has the key
    if needs_model and cfg.require_key and not any(os.environ.get(n) for n in cfg.key_names):
        raise ApiError(f"{cfg.key_names[0]} is not set on the server: set it, then restart the server", 409)
    if cfg.provider == "gemini" and kind == "analyze":
        raise ApiError("analyze needs tool calling, which the Gemini provider does not have: start the server with --provider openrouter", 409)
    if params.get("budget") not in (None, ""):
        try:
            budget = float(params["budget"])
        except (TypeError, ValueError):
            budget = -1
        if not 0 < budget <= 1000:
            raise ApiError("budget must be between 0 and 1000 USD")
        params["budget"] = budget
    if kind == "recheck":
        dois = params.get("dois") or []
        if not isinstance(dois, list) or not all(isinstance(d, str) for d in dois):
            raise ApiError("dois must be a list of DOI keys")
        params["workers"] = _clamp(params.get("workers"), 1, 8, 1)
    elif kind == "evaluate":
        dois = params.get("dois") or []
        if not isinstance(dois, list) or not all(isinstance(d, str) for d in dois):
            raise ApiError("dois must be a list of DOI keys")
        params.update(workers=_clamp(params.get("workers"), 1, 4, 1), audit=bool(params.get("audit")), force=bool(params.get("force")))
        if params.get("limit") not in (None, ""):
            params["limit"] = _clamp(params["limit"], 1, 500, 500)
    elif kind in ("si", "si_download"):
        if kind == "si_download" and cfg.agent_server is None:
            raise ApiError("no agent server is configured (AGENT_SERVER_URL and AGENT_SERVER_TOKEN): see the README, 'Agent server'", 409)
        params["pdf_path"] = str(_resolve_pdf(str(params.get("pdf", ""))))
    elif kind == "extract":
        params["pdf_path"] = str(_resolve_pdf(str(params.get("pdf", ""))))
    elif kind == "analyze":
        goal = str(params.get("goal") or "").strip()
        if not goal or len(goal) > 2000:
            raise ApiError("goal is required (at most 2000 characters)")
        params.update(goal=goal, questions=_clamp(params.get("questions"), 1, 10, 4),
                      rounds=_clamp(params.get("rounds"), 1, 3, 2))
    elif kind == "learn":
        jobs = [_jobs().get(i) for i in params.get("jobs") or []]
        traces = [Path(j.out_dir) / "trace.jsonl" for j in jobs if j and (Path(j.out_dir) / "trace.jsonl").is_file()]
        if not traces:
            raise ApiError("choose jobs that have a trace")
        params["traces"] = [str(t) for t in traces]
    job = _jobs().submit(kind, params, lambda j: TASKS[kind](cfg, j))
    return jsonify(job.to_dict()), 202


def _job(job_id: str):
    job = _jobs().get(job_id)
    if job is None:
        raise ApiError(f"no job {job_id}", 404)
    return job


@bp.get("/jobs")
def list_jobs():
    return jsonify(jobs=[j.to_dict(log_tail=0) for j in _jobs().list()])


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    return jsonify(_job(job_id).to_dict(log_tail=300))


@bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id):
    job = _job(job_id)
    if job.status in ("queued", "running"):
        _jobs().cancel(job)
    return jsonify(job.to_dict(log_tail=20))


def _job_file(job, rel: str) -> Path:
    base = Path(job.out_dir).resolve()
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file() or ".cache" in path.relative_to(base).parts:
        raise ApiError(f"no file {rel}", 404)
    return path


@bp.get("/jobs/<job_id>/files")
def job_files(job_id):
    base = Path(_job(job_id).out_dir)
    files = [{"path": p.relative_to(base).as_posix(), "bytes": p.stat().st_size} for p in sorted(base.rglob("*"))
             if p.is_file() and ".cache" not in p.relative_to(base).parts and not p.name.endswith(".tmp")]
    return jsonify(files=files)


@bp.get("/jobs/<job_id>/files/<path:rel>")
def job_file(job_id, rel):
    return send_file(_job_file(_job(job_id), rel), as_attachment=request.args.get("download") == "1")


@bp.get("/jobs/<job_id>/findings")
def job_findings(job_id):
    return jsonify(rows=_rows(Path(_job(job_id).out_dir) / "findings.csv"))


@bp.get("/jobs/<job_id>/review")
def job_review(job_id):
    return jsonify(rows=_rows(Path(_job(job_id).out_dir) / "review.csv"))


@bp.get("/jobs/<job_id>/analysis")
def job_analysis(job_id):
    out = Path(_job(job_id).out_dir)
    result = json.loads((out / "result.json").read_text(encoding="utf-8")) if (out / "result.json").is_file() else None
    insights = (out / "INSIGHTS.md").read_text(encoding="utf-8") if (out / "INSIGHTS.md").is_file() else ""
    return jsonify(result=result, insights=insights)


@bp.get("/jobs/<job_id>/trace")
def job_trace(job_id):
    from ..core.learn import digest, load, render

    trace = Path(_job(job_id).out_dir) / "trace.jsonl"
    if not trace.is_file():
        raise ApiError("this job has no trace", 404)
    d = digest(load(trace))
    return jsonify(digest=d, markdown=render(d))


# ------------------------------------------------------------------ skills


def _check_skill_name(name: str) -> None:
    if not _SKILL.match(name):
        raise ApiError("skill names are lowercase letters, digits and dashes")


@bp.get("/skills")
def list_skills():
    cfg = _cfg()
    book = SkillBook(cfg.skills_dir)
    project = cfg.skills_dir.resolve()
    out = []
    for s in sorted(book.skills.values(), key=lambda s: (s.order, s.name)):
        source = "project" if project in s.path.resolve().parents else "default"
        out.append({"name": s.name, "description": s.description, "roles": list(s.roles), "order": s.order,
                    "source": source, "read_only": source == "default" or s.name in READ_ONLY_SKILLS,
                    "overrides_default": source == "project" and (DEFAULT_DIR / s.name / "SKILL.md").is_file()})
    return jsonify(skills=out, roles=ROLES)


@bp.get("/skills/<name>")
def get_skill(name):
    _check_skill_name(name)
    skill = SkillBook(_cfg().skills_dir).skills.get(name)
    if skill is None:
        raise ApiError(f"no skill {name}", 404)
    return jsonify(name=name, text=skill.path.read_text(encoding="utf-8"))


@bp.put("/skills/<name>")
def put_skill(name):
    _check_skill_name(name)
    if name in READ_ONLY_SKILLS:
        raise ApiError(f"{name} is generated from the data; rebuild it instead of editing it")
    text = str((request.get_json(silent=True) or {}).get("text") or "")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m or not re.search(r"^roles:\s*\[.+\]\s*$", m.group(1), re.M):
        raise ApiError("a skill starts with front matter (--- ... ---) that has a roles: [...] line")
    named = re.search(r"^name:\s*(\S+)\s*$", m.group(1), re.M)
    if named and named.group(1) != name:
        raise ApiError(f"front matter name '{named.group(1)}' must match '{name}'")
    if len(text) > 50_000:
        raise ApiError("a skill is at most 50 000 characters")
    target = _cfg().skills_dir / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return jsonify(name=name, saved=str(target))


@bp.delete("/skills/<name>")
def delete_skill(name):
    _check_skill_name(name)
    target = _cfg().skills_dir / name / "SKILL.md"
    if not target.is_file():
        raise ApiError("only project skills can be deleted (default skills come with the package)", 400)
    target.unlink()
    if not any(target.parent.iterdir()):
        target.parent.rmdir()
    return jsonify(name=name, deleted=True)


@bp.get("/prompt")
def prompt():
    role = request.args.get("role", "")
    if role not in ROLES:
        raise ApiError(f"role must be one of: {', '.join(ROLES)}")
    book = SkillBook(_cfg().skills_dir)
    text = book.prompt(role)
    return jsonify(role=role, skills=[s.name for s in book.for_role(role)], prompt=text, chars=len(text))


# ------------------------------------------------------------------ data


@bp.get("/data/tables")
def data_tables():
    cfg = _cfg()
    try:
        from ..analyze.tools import DataTools
    except ImportError:
        raise ApiError("analysis needs pandas: pip install 'psc-agent[analysis]'", 501) from None
    if not cfg.data_dir.is_dir():
        raise ApiError(f"no data folder {cfg.data_dir}", 404)
    return jsonify(DataTools(cfg.data_dir, catalog=cfg.catalog).list_tables())


@bp.get("/columns")
def columns():
    """Every column of the processed tables: type, coverage, meaning and how much the analysis needs it.
    ``?format=csv`` or ``md`` downloads the same as a file."""
    from flask import Response, request

    cfg = _cfg()
    try:
        from ..analyze.columns import profile, summary, to_csv, to_markdown
    except ImportError:
        raise ApiError("columns need pandas: pip install 'psc-agent[analysis]'", 501) from None
    if not cfg.data_dir.is_dir():
        raise ApiError(f"no data folder {cfg.data_dir}", 404)
    cols = profile(cfg.data_dir, catalog=cfg.catalog)
    fmt = request.args.get("format", "json")
    if fmt == "csv":
        return Response(to_csv(cols).encode("utf-8-sig"), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="columns.csv"'})
    if fmt == "md":
        return Response(to_markdown(cols).encode("utf-8"), mimetype="text/markdown",
                        headers={"Content-Disposition": 'attachment; filename="columns.md"'})
    return jsonify(summary=summary(cols), columns=[c.to_dict() for c in cols])


@bp.post("/data/map")
def data_map():
    cfg = _cfg()
    from ..analyze.datamap import refresh

    refresh(cfg.data_dir, cfg.skills_dir, catalog=cfg.catalog, force=True)
    text = (cfg.skills_dir / "data-map" / "SKILL.md").read_text(encoding="utf-8")
    return jsonify(skill="data-map", chars=len(text), text=text)
