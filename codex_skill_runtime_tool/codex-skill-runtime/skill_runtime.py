#!/usr/bin/env python3
"""Generic Codex runtime entry point for Claude Code skills."""

from __future__ import annotations

import sys
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "codex-skill-runtime-core"
sys.path.insert(0, str(RUNTIME_DIR))

from runtime.universal_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
