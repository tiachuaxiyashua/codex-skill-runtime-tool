from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MINIMUM_TOKENS_TO_INIT = 10000
DEFAULT_MINIMUM_TOKENS_BETWEEN_UPDATES = 5000
DEFAULT_TOOL_CALLS_BETWEEN_UPDATES = 3


@dataclass(frozen=True)
class SessionMemoryConfig:
    minimum_tokens_to_init: int = DEFAULT_MINIMUM_TOKENS_TO_INIT
    minimum_tokens_between_updates: int = DEFAULT_MINIMUM_TOKENS_BETWEEN_UPDATES
    tool_calls_between_updates: int = DEFAULT_TOOL_CALLS_BETWEEN_UPDATES


@dataclass(frozen=True)
class SessionMemoryStats:
    event_count: int
    tool_count: int
    estimated_tokens: int


def session_memory_config() -> SessionMemoryConfig:
    return SessionMemoryConfig(
        minimum_tokens_to_init=_env_int("SKILL_RUNTIME_SESSION_MEMORY_INIT_TOKENS", DEFAULT_MINIMUM_TOKENS_TO_INIT),
        minimum_tokens_between_updates=_env_int(
            "SKILL_RUNTIME_SESSION_MEMORY_UPDATE_TOKENS",
            DEFAULT_MINIMUM_TOKENS_BETWEEN_UPDATES,
        ),
        tool_calls_between_updates=_env_int(
            "SKILL_RUNTIME_SESSION_MEMORY_TOOL_CALLS",
            DEFAULT_TOOL_CALLS_BETWEEN_UPDATES,
        ),
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def session_memory_path(session_or_dir: Any) -> Path:
    return _session_dir(session_or_dir) / "session-memory" / "summary.md"


def session_memory_state_path(session_or_dir: Any) -> Path:
    return _session_dir(session_or_dir) / "session-memory" / "state.json"


def update_session_memory(
    session: Any,
    *,
    command: str = "",
    arguments: str = "",
    note: str = "",
    status: str = "",
) -> Path:
    session_dir = _session_dir(session)
    session_dir.joinpath("session-memory").mkdir(parents=True, exist_ok=True)
    events = _read_jsonl(session_dir / "events.jsonl")
    transcript = _read_jsonl(session_dir / "transcript.jsonl")
    tools = _read_tools(session_dir / "tools")
    read_state = _load_json(session_dir / "read-state.json")
    artifacts = _load_json(session_dir / "artifacts.json")
    task_tree = _load_json(session_dir / "task-tree.json")
    pending_question = _load_json(session_dir / "pending-question.json")
    invoked_skills = _load_json(session_dir / "invoked-skills.json")
    workers = _load_json(session_dir / "workers.json")

    raw_text = json.dumps(
        {
            "events": events[-80:],
            "transcript": transcript[-80:],
            "tools": tools[-40:],
            "read_state": read_state,
            "artifacts": artifacts,
            "task_tree": task_tree,
            "pending_question": pending_question,
            "invoked_skills": invoked_skills,
            "workers": workers,
        },
        ensure_ascii=False,
    )
    stats = SessionMemoryStats(
        event_count=len(events),
        tool_count=len(tools),
        estimated_tokens=estimate_tokens(raw_text),
    )
    summary = _render_summary(
        session=session,
        command=command,
        arguments=arguments,
        note=note,
        status=status,
        events=events,
        tools=tools,
        read_state=read_state,
        artifacts=artifacts,
        task_tree=task_tree,
        pending_question=pending_question,
        invoked_skills=invoked_skills,
        workers=workers,
        stats=stats,
    )
    path = session_memory_path(session)
    path.write_text(summary, encoding="utf-8")
    session_memory_state_path(session).write_text(
        json.dumps(
            {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "event_count": stats.event_count,
                "tool_count": stats.tool_count,
                "estimated_tokens": stats.estimated_tokens,
                "command": command,
                "status": status,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def maybe_update_session_memory(
    session: Any,
    *,
    command: str = "",
    arguments: str = "",
    note: str = "",
    status: str = "",
    force: bool = False,
) -> Path | None:
    config = session_memory_config()
    current = collect_session_memory_stats(session)
    state = _load_json(session_memory_state_path(session))
    exists = session_memory_path(session).exists()
    if not isinstance(state, dict):
        state = {}
    previous_tokens = _safe_int(state.get("estimated_tokens"))
    previous_tools = _safe_int(state.get("tool_count"))
    initialized = exists or current.estimated_tokens >= config.minimum_tokens_to_init
    should_update = (
        force
        or (not exists and initialized)
        or (
            exists
            and (
                current.estimated_tokens - previous_tokens >= config.minimum_tokens_between_updates
                or current.tool_count - previous_tools >= config.tool_calls_between_updates
            )
        )
    )
    if not should_update:
        return None
    return update_session_memory(
        session,
        command=command,
        arguments=arguments,
        note=note,
        status=status,
    )


def collect_session_memory_stats(session_or_dir: Any) -> SessionMemoryStats:
    session_dir = _session_dir(session_or_dir)
    events = _read_jsonl(session_dir / "events.jsonl")
    tools = _read_tools(session_dir / "tools")
    read_state = _load_json(session_dir / "read-state.json")
    task_tree = _load_json(session_dir / "task-tree.json")
    text = json.dumps(
        {
            "events": events[-80:],
            "tools": tools[-40:],
            "read_state": read_state,
            "task_tree": task_tree,
        },
        ensure_ascii=False,
    )
    return SessionMemoryStats(
        event_count=len(events),
        tool_count=len(tools),
        estimated_tokens=estimate_tokens(text),
    )


def session_memory_context(session_or_dir: Any, *, max_chars: int = 12000) -> str:
    path = session_memory_path(session_or_dir)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED SESSION MEMORY]\n"
    return (
        "## Runtime Session Memory\n\n"
        "This is the runtime-maintained rolling summary for the current or resumed session. "
        "It preserves facts across compacted observations; verify live files before editing.\n\n"
        f"Source: `{path}`\n\n"
        f"{text}"
    )


def _render_summary(
    *,
    session: Any,
    command: str,
    arguments: str,
    note: str,
    status: str,
    events: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    read_state: Any,
    artifacts: Any,
    task_tree: Any,
    pending_question: Any,
    invoked_skills: Any,
    workers: Any,
    stats: SessionMemoryStats,
) -> str:
    session_id = str(getattr(session, "id", _session_dir(session).name))
    label = str(getattr(session, "label", ""))
    root = str(getattr(session, "root", ""))
    lines = [
        "# Session Memory",
        "",
        f"- Session: `{session_id}`",
        f"- Label: {label}",
        f"- Root: `{root}`" if root else "- Root:",
        f"- Command: {command}",
        f"- Arguments: {arguments[:1000]}",
        f"- Status: {status}",
        f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Estimated context tokens: {stats.estimated_tokens}",
        f"- Events: {stats.event_count}",
        f"- Tool calls: {stats.tool_count}",
        "",
    ]
    if note.strip():
        lines.extend(["## Latest Note", "", note.strip()[:4000], ""])

    lines.extend(["## Recent Timeline", ""])
    for event in events[-30:]:
        lines.append(_event_line(event))
    lines.append("")

    if tools:
        lines.extend(["## Recent Tool Results", ""])
        for tool in tools[-20:]:
            lines.append(
                f"- `{tool.get('tool', '')}` status={tool.get('status', '')}: {str(tool.get('summary', ''))[:300]}"
            )
        lines.append("")

    if isinstance(read_state, dict) and read_state:
        lines.extend(["## Files Read", ""])
        for item in list(read_state.values())[-30:]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{item.get('path', '')}` truncated={item.get('truncated', False)}")
        lines.append("")

    artifact_items = artifacts.get("artifacts") if isinstance(artifacts, dict) else []
    if isinstance(artifact_items, list) and artifact_items:
        lines.extend(["## Artifacts", ""])
        for item in artifact_items[-30:]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('path', '')}` type={item.get('type', '')}")
        lines.append("")

    skill_items = invoked_skills.get("skills") if isinstance(invoked_skills, dict) else []
    if isinstance(skill_items, list) and skill_items:
        lines.extend(["## Invoked Skills", ""])
        for item in skill_items[:20]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('name', '')}` agent={item.get('agent', '')} source=`{item.get('path', '')}`")
        lines.append("")

    worker_items = workers.get("workers") if isinstance(workers, dict) else []
    if isinstance(worker_items, list) and worker_items:
        lines.extend(["## Workers", ""])
        for item in worker_items:
            if isinstance(item, dict):
                lines.append(
                    f"- `{item.get('id', '')}` name={item.get('name', '')} agent={item.get('agent', '')} status={item.get('status', '')}"
                )
        lines.append("")

    if isinstance(pending_question, dict) and pending_question:
        lines.extend(
            [
                "## Pending User Question",
                "",
                f"- Question: {pending_question.get('question', '')}",
                f"- Status: {pending_question.get('status', '')}",
                "",
            ]
        )

    if isinstance(task_tree, dict) and task_tree.get("nodes"):
        lines.extend(["## Active Task Tree Snapshot", ""])
        nodes = task_tree.get("nodes")
        if isinstance(nodes, list):
            for node in nodes[-40:]:
                if isinstance(node, dict):
                    lines.append(
                        f"- `{node.get('id', '')}` type={node.get('type', '')} name={node.get('display_name') or node.get('name', '')} status={node.get('status', '')}"
                    )
        lines.append("")

    lines.extend(
        [
            "## Continuation Rules",
            "",
            "- Treat this summary as continuity context, not proof that files are unchanged.",
            "- Resume from pending questions, active workers, unfinished nodes, and latest tool evidence first.",
            "- Re-read files before editing when the exact current content matters.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _event_line(event: dict[str, Any]) -> str:
    timestamp = str(event.get("timestamp") or "")
    type_ = str(event.get("type") or "")
    message = str(event.get("message") or "").replace("\n", " ")[:300]
    data = event.get("data")
    suffix = ""
    if isinstance(data, dict):
        if data.get("arguments"):
            suffix += f" args={str(data.get('arguments'))[:120]}"
        result = data.get("result")
        if isinstance(result, dict):
            suffix += f" tool={result.get('tool', '')} status={result.get('status', '')}"
    return f"- {timestamp} `{type_}` {message}{suffix}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    return rows


def _read_tools(tools_dir: Path) -> list[dict[str, Any]]:
    if not tools_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(tools_dir.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _session_dir(session_or_dir: Any) -> Path:
    if isinstance(session_or_dir, Path):
        return session_or_dir
    if isinstance(session_or_dir, str):
        return Path(session_or_dir)
    value = getattr(session_or_dir, "dir", None)
    if isinstance(value, Path):
        return value
    return Path(value)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
