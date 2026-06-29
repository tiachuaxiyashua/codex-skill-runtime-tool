from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .session import RuntimeSession
from .state_paths import runtime_state_path
from .transcript import append_transcript_event, find_session_dir


def record_pending_question(
    session: RuntimeSession,
    *,
    question: str,
    options: list[Any] | None = None,
    default: str | None = None,
    pause_policy: str | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": session.id,
        "project_root": str(session.root),
        "question": question,
        "options": options or [],
        "default": default,
        "pause_policy": pause_policy or "",
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
    answered_at = datetime.now().isoformat(timespec="seconds")
    pending = load_pending_question(project_root, session_or_path) or {
        "session_id": session_dir.name,
        "question": "",
        "options": [],
    }
    answered = {
        **pending,
        "answer": answer,
        "status": "answered",
        "answered_at": answered_at,
    }
    direct = session_dir / "pending-question.json"
    if direct.exists():
        direct.write_text(json.dumps(answered, ensure_ascii=False, indent=2), encoding="utf-8")
    (session_dir / "pending-question-answer.json").write_text(
        json.dumps(answered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    indexed = runtime_state_path(project_root, "pending-questions", f"{session_dir.name}.json")
    indexed.parent.mkdir(parents=True, exist_ok=True)
    indexed.write_text(json.dumps(answered, ensure_ascii=False, indent=2), encoding="utf-8")
    _mark_session_question_answered(session_dir, answer=answer, answered_at=answered_at)
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


def _mark_session_question_answered(session_dir: Path, *, answer: str, answered_at: str) -> None:
    answer_path = session_dir / "pending-question-answer.json"
    tree_path = session_dir / "task-tree.json"
    tree = _load_json(tree_path)
    active_ids: set[str] = set()
    if isinstance(tree, dict):
        nodes = tree.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "question" and node.get("status") == "waiting_user":
                    node["status"] = "done"
                    node["finished_at"] = node.get("finished_at") or answered_at
                    node.setdefault("metadata", {})["answer"] = answer
                    node.setdefault("evidence", {})["pending_answer"] = str(answer_path)
                if node.get("status") in {"running", "waiting_user", "queued"}:
                    active_ids.add(str(node.get("id") or ""))
            tree["updated_at"] = answered_at
            tree_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")

    state_path = session_dir / "session-state.json"
    state = _load_json(state_path)
    if isinstance(state, dict):
        state["status"] = "answered"
        state["waiting_question"] = None
        state["active_node_ids"] = [
            node_id for node_id in state.get("active_node_ids", []) if str(node_id) in active_ids
        ]
        state["updated_at"] = answered_at
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = session_dir / "summary.json"
    summary = _load_json(summary_path)
    if isinstance(summary, dict):
        summary["status"] = "ANSWERED"
        summary["updated_at"] = answered_at
        summary["answer"] = answer
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    event = {"type": "question.answered", "message": "User answered pending question", "data": {"answer": answer}, "timestamp": answered_at}
    with (session_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    append_transcript_event(
        session_dir / "transcript.jsonl",
        session_id=session_dir.name,
        type_="question.answered",
        message="User answered pending question",
        data={"answer": answer},
    )
