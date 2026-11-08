"""Read ``KEY=value`` lines from a ``.env`` file into the environment (no dependency).

Variables already set in the environment win, so a shell ``$env:OPENROUTER_API_KEY`` overrides the file.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env(*paths: str | os.PathLike) -> list[Path]:
    """Load each existing file in order (earlier files win over later ones). Returns the files read."""
    read = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value
        read.append(path)
    return read
