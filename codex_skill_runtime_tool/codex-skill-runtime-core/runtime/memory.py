from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .session import RuntimeSession
from .state_paths import runtime_state_path


def runtime_memory_context(
    project_root: Path,
    *,
    exclude_session: str | None = None,
    limit: int = 5,
    max_chars: int = 10000,
) -> str:
    summaries = _load_recent_summaries(project_root, exclude_session=exclude_session, limit=limit)
    if not summaries:
        return ""

    lines = [
        "## Runtime Memory / Compacted Session Context",
        "",
        "These are deterministic summaries from previous codex-skill-runtime sessions. "
        "Use them only as continuity hints; verify current files before changing code.",
        "",
    ]
    for item in summaries:
        lines.extend(
            [
                f"### {item.get('session_id', 'unknown')}",
                f"- Label: {item.get('label', '')}",
                f"- Command: {item.get('command', '')}",
                f"- Status: {item.get('status', '')}",
                f"- Updated: {item.get('updated_at', '')}",
                f"- Events: {item.get('event_count', 0)}",
            ]
        )
        tools = item.get("recent_tools", [])
        if isinstance(tools, list) and tools:
            lines.append("- Recent tools:")
            for tool in tools[:8]:
                if not isinstance(tool, dict):
                    continue
                lines.append(
                    f"  - {tool.get('tool', '')}: {tool.get('status', '')} - {tool.get('summary', '')}"
                )
        notes = str(item.get("notes") or "").strip()
        if notes:
            lines.extend(["- Notes:", _indent(notes, "  ")])
        lines.append("")

    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED RUNTIME MEMORY]\n"
    return text


def project_memory_context(project_root: Path, *, max_chars: int = 20000) -> str:
    root = _project_memory_root(project_root)
    sections: list[str] = []
    style_path = root / "style-guide.md"
    notes_path = root / "project-notes.md"
    assets_path = root / "asset-manifest.jsonl"

    if style_path.exists():
        sections.append(_memory_file_section("Global Style Guide", style_path, max_chars=max_chars // 3))
    if assets_path.exists():
        lines = assets_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        sections.append(
            "### Asset Manifest\n\n"
            f"Source: `{assets_path}`\n\n"
            "```jsonl\n"
            + "\n".join(lines)[: max_chars // 3]
            + "\n```"
        )
    if notes_path.exists():
        sections.append(_memory_file_section("Project Notes", notes_path, max_chars=max_chars // 3))
    if not sections:
        return ""
    return (
        "## Runtime Project Memory\n\n"
        "This memory is owned by codex-skill-runtime, not by any loaded skill package. "
        "Use it for cross-skill continuity such as global art/audio style, asset inventory, and project decisions. "
        "Verify live files before relying on it.\n\n"
        + "\n\n".join(sections)
    )


def read_project_memory(project_root: Path, *, section: str = "all") -> str:
    clean = _project_memory_section(section)
    root = _project_memory_root(project_root)
    if clean == "all":
        return project_memory_context(project_root) or "No runtime project memory exists yet."
    path = _project_memory_file(root, clean)
    if not path.exists():
        return f"No runtime project memory exists for section `{clean}` at `{path}`."
    return path.read_text(encoding="utf-8", errors="replace")


def write_project_memory(project_root: Path, *, section: str, content: str, append: bool = True) -> Path:
    root = _project_memory_root(project_root)
    path = _project_memory_file(root, _project_memory_section(section))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content.strip()
    if append and path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace").rstrip()
        text = f"{existing}\n\n{text}\n" if existing else text + "\n"
    else:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def record_asset(project_root: Path, asset: dict[str, Any]) -> Path:
    root = _project_memory_root(project_root)
    path = root / "asset-manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        **asset,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def record_session_summary(
    session: RuntimeSession,
    *,
    command: str,
    arguments: str = "",
    status: str,
    notes: str = "",
    gates: list[Any] | None = None,
) -> Path:
    events = _read_events(session.events_path)
    recent_tools = _read_recent_tools(session.dir / "tools")
    gate_rows = []
    for gate in gates or []:
        gate_rows.append(
            {
                "name": getattr(gate, "name", ""),
                "status": getattr(gate, "status", ""),
                "reason": getattr(gate, "reason", ""),
            }
        )

    summary = {
        "session_id": session.id,
        "label": getattr(session, "label", ""),
        "project_root": str(session.root),
        "command": command,
        "arguments": arguments,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "event_count": len(events),
        "recent_events": events[-12:],
        "recent_tools": recent_tools[-20:],
        "gates": gate_rows,
        "notes": notes,
    }
    path = session.write_json("summary.json", summary)
    _update_index(session.root, summary)
    return path


def agent_memory_context(project_root: Path, *, agent_name: str, scope: str | None) -> str:
    if not scope:
        return ""
    path = _agent_memory_path(project_root, agent_name=agent_name, scope=scope)
    if not path.exists():
        return (
            "## Agent Memory\n\n"
            f"Agent `{agent_name}` requested `{scope}` memory. No memory file exists yet at `{path}`."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > 20000:
        text = text[:20000] + "\n[TRUNCATED AGENT MEMORY]\n"
    return f"## Agent Memory: {agent_name} ({scope})\n\nSource: `{path}`\n\n{text}"


def write_agent_memory(project_root: Path, *, agent_name: str, scope: str, content: str, append: bool = True) -> Path:
    path = _agent_memory_path(project_root, agent_name=agent_name, scope=scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    if append and path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace").rstrip()
        text = f"{existing}\n\n{content.strip()}\n" if existing else content.strip() + "\n"
    else:
        text = content.strip() + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _agent_memory_path(project_root: Path, *, agent_name: str, scope: str) -> Path:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in agent_name).strip("-") or "agent"
    if scope == "user":
        configured = Path.home() / ".claude" / "agent-memory"
        return configured / f"{clean}.md"
    if scope == "project":
        return project_root / ".claude" / "agent-memory" / f"{clean}.md"
    return runtime_state_path(project_root, "agent-memory", f"{clean}.md")


def _load_recent_summaries(project_root: Path, *, exclude_session: str | None, limit: int) -> list[dict[str, Any]]:
    index_path = _index_path(project_root)
    summaries: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                summaries.extend(item for item in data if isinstance(item, dict))
        except (OSError, ValueError):
            summaries = []

    if not summaries:
        sessions_dir = runtime_state_path(project_root, "sessions")
        if sessions_dir.exists():
            for summary_path in sessions_dir.glob("*/summary.json"):
                try:
                    data = json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    summaries.append(data)

    filtered = [item for item in summaries if item.get("session_id") != exclude_session]
    filtered.sort(key=lambda item: str(item.get("updated_at") or item.get("session_id") or ""), reverse=True)
    return filtered[:limit]


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                events.append(data)
    except OSError:
        return []
    return events


def _read_recent_tools(tools_dir: Path) -> list[dict[str, Any]]:
    if not tools_dir.exists():
        return []
    tools: list[dict[str, Any]] = []
    for path in sorted(tools_dir.glob("*.json"))[-30:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        tools.append(
            {
                "tool": data.get("tool", ""),
                "status": data.get("status", ""),
                "summary": data.get("summary", ""),
            }
        )
    return tools


def _update_index(project_root: Path, summary: dict[str, Any]) -> None:
    path = _index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                items = [item for item in data if isinstance(item, dict)]
        except (OSError, ValueError):
            items = []
    items = [item for item in items if item.get("session_id") != summary.get("session_id")]
    items.insert(0, summary)
    path.write_text(json.dumps(items[:200], ensure_ascii=False, indent=2), encoding="utf-8")


def _index_path(project_root: Path) -> Path:
    return runtime_state_path(project_root, "sessions-index.json")


def _project_memory_root(project_root: Path) -> Path:
    return runtime_state_path(project_root, "project-memory")


def _project_memory_section(section: str) -> str:
    normalized = section.strip().lower().replace("_", "-")
    aliases = {
        "style": "style-guide",
        "style-guide": "style-guide",
        "art-style": "style-guide",
        "audio-style": "style-guide",
        "assets": "assets",
        "asset-manifest": "assets",
        "notes": "notes",
        "project-notes": "notes",
        "all": "all",
    }
    return aliases.get(normalized, normalized or "notes")


def _project_memory_file(root: Path, section: str) -> Path:
    if section in {"style-guide", "style"}:
        return root / "style-guide.md"
    if section in {"assets", "asset-manifest"}:
        return root / "asset-manifest.jsonl"
    if section in {"notes", "project-notes"}:
        return root / "project-notes.md"
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in section).strip("-") or "notes"
    return root / f"{clean}.md"


def _memory_file_section(title: str, path: Path, *, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED PROJECT MEMORY]\n"
    return f"### {title}\n\nSource: `{path}`\n\n{text}"


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.splitlines())
