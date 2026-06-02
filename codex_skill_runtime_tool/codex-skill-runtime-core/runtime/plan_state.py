from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"done", "completed", "pass", "passed", "verified", "skipped"}


def enter_plan_mode(session_or_dir: Any, *, plan: str = "", reason: str = "") -> dict[str, Any]:
    state = _load_state(session_or_dir)
    state.update(
        {
            "mode": "plan",
            "plan": plan,
            "reason": reason,
            "entered_at": state.get("entered_at") or _now(),
            "updated_at": _now(),
            "verified_at": "",
            "verification": {},
        }
    )
    _write_state(session_or_dir, state)
    return state


def exit_plan_mode(session_or_dir: Any, *, approved: bool = True, reason: str = "") -> dict[str, Any]:
    state = _load_state(session_or_dir)
    state.update(
        {
            "mode": "execute" if approved else "blocked",
            "approved": approved,
            "exit_reason": reason,
            "exited_at": _now(),
            "updated_at": _now(),
        }
    )
    _write_state(session_or_dir, state)
    return state


def verify_plan_execution(session_or_dir: Any, *, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _load_state(session_or_dir)
    evidence = evidence if isinstance(evidence, dict) else {}
    tasks = evidence.get("tasks")
    complete = False
    if isinstance(tasks, list) and tasks:
        complete = all(str(item.get("status", "")).lower() in TERMINAL_STATUSES for item in tasks if isinstance(item, dict))
    elif evidence:
        complete = bool(evidence.get("complete", evidence.get("verified", False)))
    state.update(
        {
            "verified": complete,
            "verified_at": _now(),
            "verification": evidence,
            "updated_at": _now(),
        }
    )
    _write_state(session_or_dir, state)
    return state


def plan_mode_context(session_or_dir: Any) -> str:
    state = _load_state(session_or_dir)
    if not state:
        return ""
    return (
        "## Runtime Plan Mode State\n\n"
        f"- Mode: {state.get('mode', '')}\n"
        f"- Approved: {state.get('approved', '')}\n"
        f"- Verified: {state.get('verified', '')}\n"
        f"- Updated: {state.get('updated_at', '')}\n\n"
        "### Plan\n\n"
        f"{state.get('plan', '')}\n"
    ).strip()


def _load_state(session_or_dir: Any) -> dict[str, Any]:
    path = _path(session_or_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(session_or_dir: Any, state: dict[str, Any]) -> Path:
    path = _path(session_or_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _path(session_or_dir: Any) -> Path:
    return _session_dir(session_or_dir) / "plan-mode.json"


def _session_dir(session_or_dir: Any) -> Path:
    if isinstance(session_or_dir, Path):
        return session_or_dir
    if isinstance(session_or_dir, str):
        return Path(session_or_dir)
    value = getattr(session_or_dir, "dir", None)
    if isinstance(value, Path):
        return value
    return Path(value)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
