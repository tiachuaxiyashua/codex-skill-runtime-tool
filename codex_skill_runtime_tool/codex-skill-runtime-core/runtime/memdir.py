from __future__ import annotations

import json
import hashlib
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .state_paths import runtime_state_path


MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30
MAX_RELEVANT_MEMORIES = 5
MAX_MEMORY_LINES = 200
MAX_MEMORY_BYTES = 4096
DEFAULT_CONSOLIDATION_HOURS = 24
DEFAULT_CONSOLIDATION_SESSIONS = 5

MemorySideQuerySelector = Callable[[str, list["MemoryHeader"], str, list[str]], list[str] | None]


@dataclass(frozen=True)
class MemoryHeader:
    filename: str
    path: Path
    mtime: float
    description: str
    type: str
    tags: list[str]


def memory_root(project_root: Path) -> Path:
    return runtime_state_path(project_root, "memory")


def memory_overview_path(project_root: Path) -> Path:
    return memory_root(project_root) / "MEMORY.md"


def scan_memory_files(project_root: Path, *, limit: int = MAX_MEMORY_FILES) -> list[MemoryHeader]:
    root = memory_root(project_root)
    if not root.exists():
        return []
    headers: list[MemoryHeader] = []
    for path in root.rglob("*.md"):
        if path.name == "MEMORY.md":
            continue
        try:
            stat = path.stat()
            text = _read_first_lines(path, FRONTMATTER_MAX_LINES)
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        try:
            filename = str(path.relative_to(root))
        except ValueError:
            filename = str(path)
        headers.append(
            MemoryHeader(
                filename=filename.replace("\\", "/"),
                path=path,
                mtime=stat.st_mtime,
                description=str(frontmatter.get("description") or "").strip(),
                type=str(frontmatter.get("type") or "").strip(),
                tags=_as_list(frontmatter.get("tags")),
            )
        )
    headers.sort(key=lambda item: item.mtime, reverse=True)
    return headers[:limit]


def format_memory_manifest(headers: list[MemoryHeader]) -> str:
    rows = []
    for item in headers:
        tag = f"[{item.type}] " if item.type else ""
        timestamp = datetime.fromtimestamp(item.mtime).isoformat(timespec="seconds")
        description = f": {item.description}" if item.description else ""
        rows.append(f"- {tag}{item.filename} ({timestamp}){description}")
    return "\n".join(rows)


def relevant_memory_context(
    project_root: Path,
    *,
    query: str,
    recent_tools: list[str] | None = None,
    selector: MemorySideQuerySelector | None = None,
    limit: int = MAX_RELEVANT_MEMORIES,
    max_lines: int = MAX_MEMORY_LINES,
    max_bytes: int = MAX_MEMORY_BYTES,
) -> str:
    root = memory_root(project_root)
    overview = memory_overview_path(project_root)
    sections: list[str] = []
    if overview.exists():
        sections.append(
            "### MEMORY.md\n\n"
            f"Source: `{overview}`\n\n"
            f"{_read_capped(overview, max_lines=max_lines, max_bytes=max_bytes)}"
        )
    selected = find_relevant_memories(
        project_root,
        query=query,
        recent_tools=recent_tools or [],
        selector=selector,
        limit=limit,
    )
    for item in selected:
        sections.append(
            f"### {item.filename}\n\n"
            f"Source: `{item.path}`\n"
            f"Updated: {datetime.fromtimestamp(item.mtime).isoformat(timespec='seconds')}\n\n"
            f"{_read_capped(item.path, max_lines=max_lines, max_bytes=max_bytes)}"
        )
    if not sections:
        return ""
    return (
        "## Runtime Durable Memory Directory\n\n"
        "These memories are stored by codex-skill-runtime for cross-session continuity. "
        "They are selected from a bounded memory directory; verify current files and live state before editing.\n\n"
        f"Memory root: `{root}`\n\n"
        + "\n\n".join(sections)
    )


def find_relevant_memories(
    project_root: Path,
    *,
    query: str,
    recent_tools: list[str] | None = None,
    selector: MemorySideQuerySelector | None = None,
    limit: int = MAX_RELEVANT_MEMORIES,
) -> list[MemoryHeader]:
    headers = scan_memory_files(project_root)
    if not headers:
        return []
    selected = _side_query_selected_headers(
        query=query,
        headers=headers,
        selector=selector,
        recent_tools=recent_tools or [],
        limit=limit,
    )
    if selected is not None:
        return selected
    query_terms = set(_terms(query))
    tool_terms = set(_terms(" ".join(recent_tools or [])))
    scored: list[tuple[int, MemoryHeader]] = []
    for item in headers:
        haystack = " ".join([item.filename, item.description, item.type, " ".join(item.tags)])
        terms = set(_terms(haystack))
        score = len(query_terms & terms) * 10
        if item.type:
            score += 1
        if tool_terms and tool_terms & terms and item.type.lower() in {"reference", "api", "usage"}:
            score -= 5
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].mtime), reverse=True)
    return [item for _, item in scored[:limit]]


def _side_query_selected_headers(
    *,
    query: str,
    headers: list[MemoryHeader],
    selector: MemorySideQuerySelector | None,
    recent_tools: list[str],
    limit: int,
) -> list[MemoryHeader] | None:
    if selector is None:
        return None
    manifest = format_memory_manifest(headers)
    try:
        filenames = selector(query, headers[:MAX_MEMORY_FILES], manifest, recent_tools)
    except Exception:
        return None
    if not isinstance(filenames, list):
        return None
    by_name = {item.filename: item for item in headers}
    selected: list[MemoryHeader] = []
    for filename in filenames:
        item = by_name.get(str(filename))
        if item is None or item in selected:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def extract_session_memories(
    project_root: Path,
    session: Any,
    *,
    command: str,
    arguments: str,
    status: str,
    notes: str,
    gates: list[Any] | None = None,
) -> list[Path]:
    root = memory_root(project_root)
    topics = root / "topics"
    topics.mkdir(parents=True, exist_ok=True)
    session_id = str(getattr(session, "id", "unknown-session"))
    slug = _slug(command or "session")
    path = topics / f"{slug}.md"
    gate_rows = []
    for gate in gates or []:
        gate_rows.append(
            {
                "name": getattr(gate, "name", ""),
                "status": getattr(gate, "status", ""),
                "reason": getattr(gate, "reason", ""),
            }
        )
    entry = {
        "session_id": session_id,
        "command": command,
        "arguments": arguments,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "notes": notes[:4000],
        "gates": gate_rows,
    }
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace").rstrip()
        if f"Session: `{session_id}`" in text:
            return [path]
        content = text + "\n\n" + _render_memory_entry(entry) + "\n"
    else:
        content = _render_topic_header(command=command, arguments=arguments, status=status) + "\n\n" + _render_memory_entry(entry) + "\n"
    path.write_text(content, encoding="utf-8")
    _write_memory_overview(project_root)
    return [path]


def run_memory_extraction_job(
    project_root: Path,
    session: Any,
    *,
    command: str,
    arguments: str,
    status: str,
    notes: str,
    gates: list[Any] | None = None,
    background: bool = False,
) -> Path:
    job_path = _create_memory_job(project_root, kind="extract", session_id=str(getattr(session, "id", "")))

    def worker() -> None:
        _update_memory_job(job_path, status="running")
        try:
            outputs = extract_session_memories(
                project_root,
                session,
                command=command,
                arguments=arguments,
                status=status,
                notes=notes,
                gates=gates,
            )
        except Exception as exc:
            _update_memory_job(job_path, status="failed", error=str(exc))
            return
        _update_memory_job(job_path, status="completed", outputs=[str(path) for path in outputs])

    if background:
        _update_memory_job(job_path, status="queued-background")
        thread = threading.Thread(target=worker, name=f"memory-extract-{job_path.stem}", daemon=True)
        thread.start()
    else:
        worker()
    return job_path


def consolidate_memories(
    project_root: Path,
    *,
    force: bool = False,
    min_hours: int = DEFAULT_CONSOLIDATION_HOURS,
    min_sessions: int = DEFAULT_CONSOLIDATION_SESSIONS,
) -> Path | None:
    root = memory_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "consolidation.json"
    state = _load_json(state_path)
    if not isinstance(state, dict):
        state = {}
    sessions = _load_recent_session_summaries(project_root, limit=200)
    last_at = _parse_time(str(state.get("updated_at") or ""))
    last_count = int(state.get("session_count") or 0)
    due_by_time = last_at is None or datetime.now() - last_at >= timedelta(hours=min_hours)
    due_by_sessions = len(sessions) - last_count >= min_sessions
    if not force and not (due_by_time and due_by_sessions):
        return None
    path = _write_memory_overview(project_root, sessions=sessions)
    state_path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "session_count": len(sessions),
                "topic_count": len(scan_memory_files(project_root)),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def run_memory_consolidation_job(
    project_root: Path,
    *,
    force: bool = False,
    background: bool = False,
) -> Path:
    job_path = _create_memory_job(project_root, kind="consolidate", session_id="")

    def worker() -> None:
        _update_memory_job(job_path, status="running")
        try:
            output = consolidate_memories(project_root, force=force)
        except Exception as exc:
            _update_memory_job(job_path, status="failed", error=str(exc))
            return
        _update_memory_job(job_path, status="completed", outputs=[str(output)] if output is not None else [])

    if background:
        _update_memory_job(job_path, status="queued-background")
        thread = threading.Thread(target=worker, name=f"memory-consolidate-{job_path.stem}", daemon=True)
        thread.start()
    else:
        worker()
    return job_path


def _create_memory_job(project_root: Path, *, kind: str, session_id: str) -> Path:
    jobs = memory_root(project_root) / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}-{uuid.uuid4().hex[:8]}"
    path = jobs / f"{job_id}.json"
    payload = {
        "id": job_id,
        "kind": kind,
        "session_id": session_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "outputs": [],
        "error": "",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _update_memory_job(path: Path, *, status: str, outputs: list[str] | None = None, error: str = "") -> None:
    data = _load_json(path)
    if not isinstance(data, dict):
        data = {}
    data.update(
        {
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if outputs is not None:
        data["outputs"] = outputs
    if error:
        data["error"] = error
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_memory_overview(project_root: Path, *, sessions: list[dict[str, Any]] | None = None) -> Path:
    root = memory_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    path = memory_overview_path(project_root)
    headers = scan_memory_files(project_root)
    sessions = sessions if sessions is not None else _load_recent_session_summaries(project_root, limit=20)
    lines = [
        "---",
        "description: Runtime durable memory overview for cross-session continuity.",
        "type: index",
        f"updated_at: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# Runtime Durable Memory",
        "",
        "This directory is maintained by codex-skill-runtime. It is generic runtime memory, not a game-specific or skill-specific store.",
        "",
        "## Topic Memories",
        "",
    ]
    if headers:
        lines.append(format_memory_manifest(headers))
    else:
        lines.append("- No topic memories recorded yet.")
    lines.extend(["", "## Recent Sessions", ""])
    if sessions:
        for item in sessions[:20]:
            lines.append(
                f"- `{item.get('session_id', '')}` command={item.get('command', '')} status={item.get('status', '')} updated={item.get('updated_at', '')}"
            )
    else:
        lines.append("- No session summaries recorded yet.")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def _render_topic_header(*, command: str, arguments: str, status: str) -> str:
    return "\n".join(
        [
            "---",
            f"description: Runtime memory extracted from sessions for command `{command}`.",
            "type: session",
            f"tags: {json.dumps(_terms(command + ' ' + arguments)[:12], ensure_ascii=False)}",
            f"status: {status}",
            f"updated_at: {datetime.now().isoformat(timespec='seconds')}",
            "---",
            "",
            f"# {command or 'session'}",
        ]
    )


def _render_memory_entry(entry: dict[str, Any]) -> str:
    lines = [
        f"## Session: `{entry.get('session_id', '')}`",
        "",
        f"- Command: {entry.get('command', '')}",
        f"- Arguments: {str(entry.get('arguments', ''))[:1000]}",
        f"- Status: {entry.get('status', '')}",
        f"- Updated: {entry.get('updated_at', '')}",
    ]
    gates = entry.get("gates")
    if isinstance(gates, list) and gates:
        lines.append("- Gates:")
        for gate in gates[:20]:
            if isinstance(gate, dict):
                lines.append(f"  - {gate.get('name', '')}: {gate.get('status', '')} - {gate.get('reason', '')}")
    notes = str(entry.get("notes") or "").strip()
    if notes:
        lines.extend(["", "### Notes", "", notes])
    return "\n".join(lines)


def _load_recent_session_summaries(project_root: Path, *, limit: int) -> list[dict[str, Any]]:
    index_path = runtime_state_path(project_root, "sessions-index.json")
    items: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = []
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))
    sessions_dir = runtime_state_path(project_root, "sessions")
    if not items and sessions_dir.exists():
        for path in sessions_dir.glob("*/summary.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            if isinstance(item, dict):
                items.append(item)
    items.sort(key=lambda item: str(item.get("updated_at") or item.get("session_id") or ""), reverse=True)
    return items[:limit]


def _read_first_lines(path: Path, limit: int) -> str:
    lines = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index >= limit:
                break
            lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def _read_capped(path: Path, *, max_lines: int, max_bytes: int) -> str:
    raw = path.read_bytes()[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()[:max_lines]
    result = "\n".join(lines)
    if path.stat().st_size > max_bytes or len(text.splitlines()) > max_lines:
        result += "\n[TRUNCATED RUNTIME MEMORY FILE]\n"
    return result


def _parse_frontmatter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = value
            data[key] = parsed
        else:
            data[key] = value
    return data


def _terms(text: str) -> list[str]:
    tokens = []
    for raw in re.split(r"[^A-Za-z0-9_:-]+", text.lower()):
        token = raw.strip("_:-")
        if len(token) >= 2:
            tokens.append(token)
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            tokens.append(char)
    seen: set[str] = set()
    result = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", value.lower()).strip("-")
    if clean:
        return clean[:80]
    digest = hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"topic-{digest}"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = value
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
