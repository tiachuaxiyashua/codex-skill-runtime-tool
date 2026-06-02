from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_API_MESSAGE_CHARS = 20000


def api_transcript_path(session_or_dir: Any) -> Path:
    session_dir = _session_dir(session_or_dir)
    return session_dir / "api-transcript.jsonl"


def append_api_message(
    session_or_dir: Any,
    *,
    role: str,
    content: str,
    label: str = "",
    source_path: Path | None = None,
    metadata: dict[str, Any] | None = None,
    max_chars: int = MAX_API_MESSAGE_CHARS,
) -> Path:
    path = api_transcript_path(session_or_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    truncated = len(content) > max_chars
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "role": role,
        "label": label,
        "content": content[:max_chars],
        "truncated": truncated,
        "source_path": str(source_path) if source_path is not None else "",
        "metadata": metadata or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_api_messages(session_or_dir: Any, *, limit: int = 40, max_chars: int = 50000) -> list[dict[str, Any]]:
    path = api_transcript_path(session_or_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    selected = rows[-limit:]
    used = 0
    bounded: list[dict[str, Any]] = []
    for item in selected:
        content = str(item.get("content") or "")
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            item = dict(item)
            item["content"] = content[:remaining] + "\n[TRUNCATED API TRANSCRIPT]\n"
            item["truncated"] = True
        used += len(str(item.get("content") or ""))
        bounded.append(item)
    return bounded


def api_transcript_context(session_or_dir: Any, *, limit: int = 20, max_chars: int = 30000) -> str:
    messages = load_api_messages(session_or_dir, limit=limit, max_chars=max_chars)
    if not messages:
        return ""
    lines = [
        "## API Message Transcript",
        "",
        "These are bounded API-like prompt/assistant message records captured by codex-skill-runtime.",
        "",
    ]
    for item in messages:
        lines.extend(
            [
                f"### {item.get('role', '')}: {item.get('label', '')}",
                f"- Timestamp: {item.get('timestamp', '')}",
                f"- Source: `{item.get('source_path', '')}`",
                f"- Truncated: {item.get('truncated', False)}",
                "",
                "```text",
                str(item.get("content") or "")[:max_chars],
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip()


def _session_dir(session_or_dir: Any) -> Path:
    if isinstance(session_or_dir, Path):
        return session_or_dir
    if isinstance(session_or_dir, str):
        return Path(session_or_dir)
    value = getattr(session_or_dir, "dir", None)
    if isinstance(value, Path):
        return value
    return Path(value)
