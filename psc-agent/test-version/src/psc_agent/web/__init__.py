"""psc-agent as a web service: Flask backend (REST API + background jobs) and an HTML/Tailwind/JS frontend.

    python -m psc_agent serve --root .            # http://127.0.0.1:5000
    from psc_agent.web import WebConfig, create_app
    app = create_app(WebConfig(root=Path(".")))    # mount in your own server

Long workflows (recheck, extract, analyze, learn) run as background jobs. Their hooks feed the page:
ProgressHook streams the log and cost, CancelHook stops a job before its next model call, BudgetHook caps it.
The OpenRouter key stays in the server's environment and is never sent to the browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..models import SUGGESTED, role_models

PROVIDER_MODELS = {p: role_models(p) for p in ("openrouter", "gemini")}  # models.py is the only place that names models


@dataclass
class WebConfig:
    """Paths default to the project layout: Raw Data/, original paper/, processed/prepared_v2/ under ``root``;
    skills/ and runs/ under ``root/agent for research`` when that folder exists, else under ``root``."""

    root: Path
    raw_dir: Path | None = None
    pdf_dir: Path | None = None
    data_dir: Path | None = None
    catalog: Path | None = None
    skills_dir: Path | None = None
    runs_dir: Path | None = None
    provider: str = "openrouter"  # openrouter | gemini: who answers the model calls (gemini has no tool calling: no analyze / web-search agent)
    models: dict | None = None  # role -> model id; default: PROVIDER_MODELS[provider]
    max_workers: int = 2
    default_budget: float = 5.0  # USD per job when the request gives none
    require_key: bool = True
    runtime_factory: Callable | None = None  # (models, hooks, config) -> Runtime; tests inject a fake model
    agent_server: object | None = None  # agent_server.AgentServer: downloads SI with the person's Chrome, outside Docker

    def __post_init__(self):
        if self.provider not in PROVIDER_MODELS:
            raise ValueError(f"provider must be one of: {', '.join(PROVIDER_MODELS)}")
        self.models = dict(self.models or PROVIDER_MODELS[self.provider])
        self.root = Path(self.root).resolve()
        home = self.root / "agent for research" if (self.root / "agent for research").is_dir() else self.root
        self.raw_dir = Path(self.raw_dir or self.root / "Raw Data")
        self.pdf_dir = Path(self.pdf_dir or self.root / "original paper")
        self.data_dir = Path(self.data_dir or self.root / "processed" / "prepared_v2")
        self.skills_dir = Path(self.skills_dir or home / "skills")
        self.runs_dir = Path(self.runs_dir or home / "runs")
        self.catalog = Path(self.catalog) if self.catalog else None

    @property
    def key_names(self) -> tuple[str, ...]:
        """Environment variables that may hold the key of the provider."""
        from ..core.gemini import KEY_NAMES

        return KEY_NAMES if self.provider == "gemini" else ("OPENROUTER_API_KEY",)

    @property
    def uploads_dir(self) -> Path:
        return self.runs_dir / "uploads"


def create_app(config: WebConfig):
    from flask import Flask

    from .api import bp
    from .jobs import JobManager

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # uploads: PDFs and workbooks
    app.config["PSC"] = config
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    app.config["JOBS"] = JobManager(config.runs_dir, max_workers=config.max_workers)
    app.register_blueprint(bp)

    @app.get("/review/<job_id>")
    def review_page(job_id):  # its own page (opened from a row's evidence link), not a tab
        from flask import render_template

        return render_template("review.html", job_id=job_id)

    @app.get("/")
    def index():
        from flask import render_template

        return render_template("index.html", suggested_models=SUGGESTED)

    return app
