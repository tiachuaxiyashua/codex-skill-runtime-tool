from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .api_transcript import api_transcript_context
from .state_paths import runtime_state_path
from .memdir import relevant_memory_context
from .session_memory import session_memory_context
from .workers import worker_scratchpad_context


@dataclass(frozen=True)
class TranscriptEvent:
    timestamp: str
    session_id: str
    type: str
    message: str
    data: dict[str, Any]


def append_transcript_event(
    path: Path,
    *,
    session_id: str,
    type_: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "type": type_,
        "message": message,
        "data": data or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_transcript(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def find_session_dir(project_root: Path, session_or_path: str) -> Path:
    raw = session_or_path.strip().strip('"')
    candidate = Path(raw)
    if candidate.exists():
        if candidate.is_dir():
            return candidate.resolve()
        return candidate.resolve().parent
    sessions_dir = runtime_state_path(project_root, "sessions")
    direct = sessions_dir / raw
    if direct.exists() and direct.is_dir():
        return direct.resolve()
    matches = sorted(sessions_dir.glob(f"*{raw}*"), key=lambda path: path.name, reverse=True)
    for match in matches:
        if match.is_dir():
            return match.resolve()
    raise FileNotFoundError(f"Cannot find runtime session `{session_or_path}` under {sessions_dir}")


def transcript_path_for_session(session_dir: Path) -> Path:
    transcript = session_dir / "transcript.jsonl"
    if transcript.exists():
        return transcript
    return session_dir / "events.jsonl"


def replay_context(
    project_root: Path,
    session_or_path: str,
    *,
    max_chars: int = 50000,
) -> str:
    session_dir = find_session_dir(project_root, session_or_path)
    events = load_transcript(transcript_path_for_session(session_dir))
    read_state = _load_json(session_dir / "read-state.json")
    summary = _load_json(session_dir / "summary.json")
    workers = _load_json(session_dir / "workers.json")
    replacements = load_replacement_manifest(session_dir)

    lines = [
        "## Runtime Transcript Replay",
        "",
        f"Source session: `{session_dir.name}`",
        f"Source path: `{session_dir}`",
        "",
        "This replay is reconstructed from runtime JSONL evidence. Use it as prior context; verify live files before editing.",
        "",
    ]
    if isinstance(summary, dict) and summary:
        lines.extend(
            [
                "### Session Summary",
                f"- Command: {summary.get('command', '')}",
                f"- Arguments: {summary.get('arguments', '')}",
                f"- Status: {summary.get('status', '')}",
                f"- Updated: {summary.get('updated_at', '')}",
                "",
            ]
        )
        notes = str(summary.get("notes") or "").strip()
        if notes:
            lines.extend(["```text", notes[:8000], "```", ""])

    session_memory = session_memory_context(session_dir)
    if session_memory:
        lines.extend(["### Rolling Session Memory", "", session_memory, ""])

    worker_rows = workers.get("workers") if isinstance(workers, dict) else []
    if isinstance(worker_rows, list) and worker_rows:
        lines.extend(["### Worker Records", ""])
        for worker in worker_rows:
            if not isinstance(worker, dict):
                continue
            lines.append(
                f"- `{worker.get('id', '')}` name={worker.get('name', '')} agent={worker.get('agent', '')} "
                f"status={worker.get('status', '')} updated={worker.get('updated_at', '')}"
            )
        lines.append("")

    scratchpads = worker_scratchpad_context(session_dir)
    if scratchpads:
        lines.extend(["### Worker Scratchpad Context", "", scratchpads, ""])

    api_messages = api_transcript_context(session_dir)
    if api_messages:
        lines.extend(["### API Message Replay", "", api_messages, ""])

    lines.append("### Event Timeline")
    for event in _important_events(events)[-120:]:
        lines.append(_event_line(event))
    lines.append("")

    if isinstance(read_state, dict) and read_state:
        lines.append("### Restored Read State")
        for item in list(read_state.values())[:40]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{item.get('path', '')}` truncated={item.get('truncated', False)} updated={item.get('updated_at', '')}")
        lines.append("")

    if replacements:
        lines.append("### Content Replacements")
        for item in replacements[-80:]:
            lines.append(
                f"- tool={item.get('tool_id', item.get('tool', ''))} path={item.get('json_path', '')} "
                f"bytes={item.get('bytes', '')} full={item.get('path', '')}"
            )
        lines.append("")

    durable = relevant_memory_context(project_root, query=_replay_query(summary, events))
    if durable:
        lines.extend(["### Relevant Durable Memory", "", durable, ""])

    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED TRANSCRIPT REPLAY]\n"
    return text


def load_replacement_manifest(session_dir: Path) -> list[dict[str, Any]]:
    paths = [
        session_dir / "large-tool-results" / "manifest.jsonl",
        session_dir / "content-replacements" / "manifest.jsonl",
    ]
    items: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                items.append(data)
    return items


def summarize_transcript(events: Iterable[dict[str, Any]], *, max_events: int = 40) -> str:
    lines = ["## Transcript Summary"]
    for event in list(_important_events(events))[-max_events:]:
        lines.append(_event_line(event))
    return "\n".join(lines)


def _important_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = []
    for event in events:
        type_ = str(event.get("type") or "")
        if type_.startswith(("codex.", "tool.", "session.", "hook.", "transcript.", "bridge.", "voice.", "ide.", "microcompact.", "memory.")):
            keep.append(event)
    return keep


def _event_line(event: dict[str, Any]) -> str:
    type_ = str(event.get("type") or "")
    timestamp = str(event.get("timestamp") or "")
    message = str(event.get("message") or "").replace("\n", " ")[:500]
    data = event.get("data")
    suffix = ""
    if isinstance(data, dict):
        if "returncode" in data:
            suffix += f" returncode={data.get('returncode')}"
        result = data.get("result")
        if isinstance(result, dict):
            suffix += f" tool={result.get('tool', '')} status={result.get('status', '')}"
    return f"- {timestamp} `{type_}` {message}{suffix}"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _replay_query(summary: Any, events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if isinstance(summary, dict):
        for key in ("command", "arguments", "notes", "status"):
            if summary.get(key):
                parts.append(str(summary.get(key)))
    for event in events[-30:]:
        parts.append(str(event.get("message") or ""))
        data = event.get("data")
        if isinstance(data, dict):
            parts.append(json.dumps(data, ensure_ascii=False)[:1000])
    return "\n".join(part for part in parts if part)
