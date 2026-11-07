"""Skills: Markdown instructions that make up each model role's system prompt.

A skill is ``<dir>/<name>/SKILL.md`` with a small front matter::

    ---
    name: read-figures
    description: How to read values off plots
    roles: [recheck, extract]
    order: 40
    ---
    <instructions for the model>

The package ships default skills (``psc_agent/skills/``). A project adds skills, or replaces one by
using the same name, in its own directories (later directories win). The system prompt of a role is
every skill whose ``roles`` lists that role (or ``*``), sorted by ``order``. Edit the Markdown to change
how a model works; no code change needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "skills"  # psc_agent/skills/<name>/SKILL.md
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    roles: tuple[str, ...]
    order: int
    body: str
    path: Path


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if m := _FRONT.match(text):
        for line in m.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        body = m.group(2)
    roles = tuple(r.strip().strip("'\"") for r in meta.get("roles", "*").strip("[]").split(",") if r.strip())
    try:
        order = int(meta.get("order", 50))
    except ValueError:
        order = 50
    return Skill(meta.get("name") or path.parent.name, meta.get("description", ""), roles or ("*",), order,
                 body.strip(), path)


class SkillBook:
    """All skills from the package defaults plus ``dirs``; the last directory is where generated
    skills (data map, learned rules) are written."""

    def __init__(self, *dirs, include_defaults: bool = True):
        self.dirs = ([DEFAULT_DIR] if include_defaults else []) + [Path(d) for d in dirs if d]
        self.skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        skills = {}
        for d in self.dirs:
            for p in sorted(d.glob("*/SKILL.md")) if d.is_dir() else []:
                s = parse_skill(p)
                skills[s.name] = s
        self.skills = skills

    @property
    def project_dir(self) -> Path | None:
        """Where generated skills go: the last non-default directory."""
        own = [d for d in self.dirs if d != DEFAULT_DIR]
        return own[-1] if own else None

    def for_role(self, role: str) -> list[Skill]:
        return sorted((s for s in self.skills.values() if role in s.roles or "*" in s.roles),
                      key=lambda s: (s.order, s.name))

    def prompt(self, role: str) -> str:
        """The system prompt for ``role``. Its first line names the role (fake servers in tests rely on it)."""
        parts = [f"You are the {role} model of a perovskite solar cell research agent. Follow these skills.", ""]
        for s in self.for_role(role):
            parts += [f"## Skill: {s.name}", s.body, ""]
        return "\n".join(parts).strip()
