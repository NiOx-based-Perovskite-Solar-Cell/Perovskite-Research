"""Background jobs for the web service, and the two hooks that connect a running workflow to the page.

Each job runs in a thread pool with its own output folder ``runs/<job id>/`` (trace.jsonl, results, job.json).
Jobs left running when the server stopped are listed as "interrupted" after a restart.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.hooks import HookBlock

MAX_LOG = 600


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    out_dir: str
    status: str = "queued"  # queued | running | done | failed | cancelled | interrupted
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None
    summary: dict = field(default_factory=dict)
    error: str | None = None
    cost_usd: float = 0.0
    log: list[str] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_log(self, line: str) -> None:
        with self._lock:
            self.log.append(f"{datetime.now():%H:%M:%S} {line}")
            del self.log[:-MAX_LOG]

    def add_cost(self, usd: float) -> None:
        with self._lock:
            self.cost_usd += usd

    def to_dict(self, log_tail: int | None = 200) -> dict:
        with self._lock:
            log = list(self.log[-log_tail:]) if log_tail else []
        return {"id": self.id, "kind": self.kind, "params": self.params, "status": self.status,
                "created": self.created, "started": self.started, "finished": self.finished,
                "summary": self.summary, "error": self.error, "cost_usd": round(self.cost_usd, 4),
                "cancel_requested": self.cancel.is_set(), "log": log}


class JobManager:
    def __init__(self, runs_dir: Path, max_workers: int = 2):
        self.runs_dir = Path(runs_dir)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="psc-job")
        self._load()

    def _load(self) -> None:
        for p in sorted(self.runs_dir.glob("*/job.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            job = Job(**{k: d[k] for k in ("id", "kind", "params", "out_dir", "status", "created", "started",
                                           "finished", "summary", "error", "cost_usd", "log") if k in d})
            if job.status in ("queued", "running"):
                job.status, job.error = "interrupted", "the server stopped while this job was running"
            self.jobs[job.id] = job

    def submit(self, kind: str, params: dict, fn) -> Job:
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{kind}-{secrets.token_hex(2)}"
        out = self.runs_dir / job_id
        out.mkdir(parents=True, exist_ok=True)
        job = Job(job_id, kind, params, str(out))
        with self._lock:
            self.jobs[job_id] = job
        self.save(job)
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn) -> None:
        job.status, job.started = "running", _now()
        self.save(job)
        try:
            job.summary = fn(job) or {}
            job.status = "cancelled" if job.cancel.is_set() else "done"
        except Exception as e:  # the job fails, the server keeps running
            job.status = "cancelled" if job.cancel.is_set() else "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.add_log("".join(traceback.format_exception(e)[-3:]).strip())
        finally:
            job.finished = _now()
            job.add_log(f"{job.status} (${job.cost_usd:.4f})")
            self.save(job)

    def save(self, job: Job) -> None:
        path = Path(job.out_dir) / "job.json"
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(job.to_dict(log_tail=None) | {"out_dir": job.out_dir, "log": job.log},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        job.add_log("cancel requested: stopping before the next model call")


class ProgressHook:
    """Streams what the workflow does into the job log, and adds up the cost."""

    def __init__(self, job: Job):
        self.job = job

    def on_run_start(self, run, **_):
        self.job.add_log(f"start {run.get('kind')}: {run.get('paper') or run.get('goal') or ''}")

    def on_after_model(self, role, completion, **_):
        u = completion.usage or {}
        cost = 0.0 if completion.cached else (u.get("cost") or 0.0)
        self.job.add_cost(cost)
        tools = [tc.get("function", {}).get("name") for tc in completion.tool_calls]
        self.job.add_log(f"{role} · {completion.model}: {u.get('prompt_tokens', 0)}+{u.get('completion_tokens', 0)} "
                         f"tokens, ${cost:.4f}{' (cache)' if completion.cached else ''}"
                         + (f" → {', '.join(tools)}" if tools else ""))

    def on_model_error(self, role, error, **_):
        self.job.add_log(f"{role} error: {str(error)[:300]}")

    def on_after_tool(self, name, ok, result, **_):
        self.job.add_log(f"tool {name}: " + ("ok" if ok else str(result)[:200]))

    def on_after_answer(self, kind, rows, **_):
        bad = sum(getattr(r, "quote_ok", None) is False for r in rows)
        self.job.add_log(f"{kind}: {len(rows)} rows" + (f", {bad} quote(s) not on the cited page" if bad else ""))

    def on_decision(self, kind, verdicts, **_):
        counts: dict[str, int] = {}
        for v in verdicts:
            counts[v.choice] = counts.get(v.choice, 0) + 1
        self.job.add_log(f"decision ({kind}): {counts}")

    def on_run_end(self, run, summary, **_):
        self.job.add_log(f"end {run.get('kind')}: {summary}")


class CancelHook:
    """Refuses the next model or tool call once the user pressed Cancel."""

    def __init__(self, job: Job):
        self.job = job

    def on_before_model(self, **_):
        if self.job.cancel.is_set():
            raise HookBlock("cancelled by the user")

    on_before_tool = on_before_model
