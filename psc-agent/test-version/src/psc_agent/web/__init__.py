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

DEFAULT_MODELS = {"default": "anthropic/claude-sonnet-5.5", "decision": "anthropic/claude-haiku-5.5",
                  "judge": "anthropic/claude-haiku-5.5", "si_finder": "anthropic/claude-sonnet-5.5",
                  "si_reader": "anthropic/claude-haiku-5.5"}


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
    eval_dirs: list | None = None  # folders holding `evaluate` runs (the Dashboard lists their sub-folders)
    models: dict = field(default_factory=lambda: dict(DEFAULT_MODELS))
    max_workers: int = 2
    default_budget: float = 5.0  # USD per job when the request gives none
    require_key: bool = True
    runtime_factory: Callable | None = None  # (models, hooks, config) -> Runtime; tests inject a fake model
    agent_server: object | None = None  # agent_server.AgentServer: downloads SI with the person's Chrome, outside Docker

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        home = self.root / "agent for research" if (self.root / "agent for research").is_dir() else self.root
        self.raw_dir = Path(self.raw_dir or self.root / "Raw Data")
        self.pdf_dir = Path(self.pdf_dir or self.root / "original paper")
        self.data_dir = Path(self.data_dir or self.root / "processed" / "prepared_v2")
        self.skills_dir = Path(self.skills_dir or home / "skills")
        self.runs_dir = Path(self.runs_dir or home / "runs")
        self.catalog = Path(self.catalog) if self.catalog else None
        if self.eval_dirs is None:  # runs made on the command line: out/ and evaluate_out/ in the project and the working folder
            seen: dict[Path, None] = {}
            for base in (self.root, Path.cwd().resolve()):
                for name in ("out", "evaluate_out"):
                    seen.setdefault((base / name).resolve(), None)
            self.eval_dirs = list(seen)

    @property
    def uploads_dir(self) -> Path:
        return self.runs_dir / "uploads"


def create_app(config: WebConfig):
    from flask import Flask

    from .api import bp
    from .dashboard import dash
    from .jobs import JobManager

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # uploads: PDFs and workbooks
    app.config["PSC"] = config
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    app.config["JOBS"] = JobManager(config.runs_dir, max_workers=config.max_workers)
    app.register_blueprint(bp)
    app.register_blueprint(dash)

    @app.get("/")
    def index():
        from flask import render_template

        return render_template("index.html")

    return app
