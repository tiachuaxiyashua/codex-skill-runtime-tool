from __future__ import annotations

import os
from pathlib import Path


def runtime_state_root(project_root: Path) -> Path:
    configured = os.environ.get("SKILL_RUNTIME_STATE_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / ".skill-runtime" / "state"


def runtime_state_path(project_root: Path, *parts: str) -> Path:
    return runtime_state_root(project_root).joinpath(*parts)
