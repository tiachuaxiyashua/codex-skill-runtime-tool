#!/usr/bin/env python3
"""Start the local Codex Skill Runtime Web UI."""

from __future__ import annotations

import sys
from pathlib import Path


UI_BACKEND = Path(__file__).resolve().parent / "codex-skill-runtime-ui" / "backend"
sys.path.insert(0, str(UI_BACKEND))

from server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
