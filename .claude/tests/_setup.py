"""Put the skill scripts on sys.path for the tests (they are scripts, not an installed package)."""
import sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
for d in ("trace-to-skill", "repo-map", "plan-evidence", "project-cli"):
    p = str(CLAUDE / "skills" / d)
    if p not in sys.path:
        sys.path.insert(0, p)
HOOKS = CLAUDE / "hooks" / "hooks.py"
