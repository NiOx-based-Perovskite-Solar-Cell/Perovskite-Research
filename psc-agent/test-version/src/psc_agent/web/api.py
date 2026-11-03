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
ROLES = ("recheck", "mapper", "extract", "judge", "decision", "planner", "analyst", "reporter", "learner")
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
    return jsonify(key_set=bool(os.environ.get("OPENROUTER_API_KEY")), require_key=cfg.require_key, models=cfg.models,
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
        pairs=[{"key": p.key, "pdf": p.pdf.name, "workbooks": len(p.workbooks), "devices": [w.stem for w in p.workbooks],
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
    needs_model = kind != "learn" or params.get("model")
    if needs_model and cfg.require_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise ApiError("OPENROUTER_API_KEY is not set on the server: set it, then restart the server", 409)
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


@bp.post("/data/map")
def data_map():
    cfg = _cfg()
    from ..analyze.datamap import refresh

    refresh(cfg.data_dir, cfg.skills_dir, catalog=cfg.catalog, force=True)
    text = (cfg.skills_dir / "data-map" / "SKILL.md").read_text(encoding="utf-8")
    return jsonify(skill="data-map", chars=len(text), text=text)
