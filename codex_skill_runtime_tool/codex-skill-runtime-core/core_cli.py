#!/usr/bin/env python3
"""Codex skill runtime entry point."""

from __future__ import annotations

import sys

from runtime.universal_cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
