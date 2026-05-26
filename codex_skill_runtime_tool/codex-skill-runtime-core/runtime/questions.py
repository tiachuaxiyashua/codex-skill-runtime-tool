from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .session import RuntimeSession
from .state_paths import runtime_state_path
from .transcript import find_session_dir


def record_pending_question(
    session: RuntimeSession,
    *,
    question: str,
    options: list[Any] | None = None,
    default: str | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": session.id,
        "project_root": str(session.root),
        "question": question,
        "options": options or [],
        "default": default,
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    session.write_json("pending-question.json", payload)
    pending_root = runtime_state_path(session.root, "pending-questions")
    pending_root.mkdir(parents=True, exist_ok=True)
    (pending_root / f"{session.id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    session.event("question.pending", "User input required", question=question, options=options or [], default=default)
    return payload


def load_pending_question(project_root: Path, session_or_path: str) -> dict[str, Any] | None:
    try:
        session_dir = find_session_dir(project_root, session_or_path)
    except FileNotFoundError:
        return None
    answered = session_dir / "pending-question-answer.json"
    if answered.exists():
        data = _load_json(answered)
        if isinstance(data, dict):
            return data
    direct = session_dir / "pending-question.json"
    if direct.exists():
        data = _load_json(direct)
        if isinstance(data, dict):
            return data
    indexed = runtime_state_path(project_root, "pending-questions", f"{session_dir.name}.json")
    data = _load_json(indexed)
    return data if isinstance(data, dict) else None


def answer_pending_question(project_root: Path, session_or_path: str, answer: str) -> dict[str, Any]:
    session_dir = find_session_dir(project_root, session_or_path)
    pending = load_pending_question(project_root, session_or_path) or {
        "session_id": session_dir.name,
        "question": "",
        "options": [],
    }
    answered = {
        **pending,
        "answer": answer,
        "status": "answered",
        "answered_at": datetime.now().isoformat(timespec="seconds"),
    }
    (session_dir / "pending-question-answer.json").write_text(
        json.dumps(answered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    indexed = runtime_state_path(project_root, "pending-questions", f"{session_dir.name}.json")
    indexed.parent.mkdir(parents=True, exist_ok=True)
    indexed.write_text(json.dumps(answered, ensure_ascii=False, indent=2), encoding="utf-8")
    return answered


def pending_question_context(project_root: Path, session_or_path: str) -> str:
    pending = load_pending_question(project_root, session_or_path)
    if not pending:
        return ""
    lines = [
        "## Runtime Pending Question",
        "",
        f"- Source session: `{pending.get('session_id', '')}`",
        f"- Status: {pending.get('status', '')}",
        f"- Question: {pending.get('question', '')}",
    ]
    options = pending.get("options")
    if isinstance(options, list) and options:
        lines.append("- Options:")
        for index, option in enumerate(options, start=1):
            lines.append(f"  {index}. {option}")
    if pending.get("answer") is not None:
        lines.append(f"- User answer: {pending.get('answer')}")
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
