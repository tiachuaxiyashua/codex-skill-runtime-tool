from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def append_tool_transcript(
    session_or_dir: Any,
    *,
    event: str,
    tool: str,
    tool_id: str,
    payload: dict[str, Any],
) -> Path:
    """Persist tool_use/tool_result records for transcript replay.

    The runtime already records human-readable events. This file keeps a
    stricter message-like stream so resume can reconstruct the exact tool
    boundary a skill saw without depending on a provider-private transcript.
    """

    session_dir = _session_dir(session_or_dir)
    path = session_dir / "tool-transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "tool": tool,
        "tool_id": tool_id,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def tool_transcript_context(session_or_dir: Any, *, limit: int = 80, max_chars: int = 40000) -> str:
    path = _session_dir(session_or_dir) / "tool-transcript.jsonl"
    if not path.exists():
        return ""
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
    if not rows:
        return ""
    lines = [
        "## Structured Tool Transcript",
        "",
        "These tool_use/tool_result records are persisted by codex-skill-runtime for resume.",
        "",
    ]
    used = 0
    for item in rows[-limit:]:
        payload = json.dumps(item.get("payload", {}), ensure_ascii=False, indent=2, default=str)
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(payload) > remaining:
            payload = payload[:remaining] + "\n[TRUNCATED TOOL TRANSCRIPT]\n"
        used += len(payload)
        lines.extend(
            [
                f"### {item.get('event', '')}: {item.get('tool', '')} #{item.get('tool_id', '')}",
                f"- Timestamp: {item.get('timestamp', '')}",
                "",
                "```json",
                payload,
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
