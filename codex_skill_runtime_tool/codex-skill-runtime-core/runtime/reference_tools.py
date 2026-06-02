from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def bounded_sleep(seconds: float, *, max_seconds: float = 60.0) -> dict[str, Any]:
    duration = max(0.0, min(float(seconds), max_seconds))
    started = now()
    time.sleep(duration)
    return {"requested_seconds": seconds, "slept_seconds": duration, "started_at": started, "finished_at": now()}


def edit_notebook(
    path: Path,
    *,
    cell_number: int,
    source: str = "",
    cell_type: str = "code",
    edit_mode: str = "replace",
) -> dict[str, Any]:
    if path.suffix.lower() != ".ipynb":
        raise ValueError("NotebookEdit requires a .ipynb file")
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("Notebook file must contain a JSON object")
    cells = data.setdefault("cells", [])
    if not isinstance(cells, list):
        raise ValueError("Notebook cells must be a list")
    index = int(cell_number)
    mode = edit_mode.strip().lower() or "replace"
    if mode not in {"replace", "insert", "delete"}:
        raise ValueError("NotebookEdit edit_mode must be replace, insert, or delete")
    if mode == "delete":
        if index < 0 or index >= len(cells):
            raise IndexError(f"Notebook cell index out of range: {index}")
        removed = cells.pop(index)
        changed = {"removed_cell_type": removed.get("cell_type") if isinstance(removed, dict) else ""}
    else:
        cell = _notebook_cell(source=source, cell_type=cell_type)
        if mode == "insert":
            if index < 0 or index > len(cells):
                raise IndexError(f"Notebook insert index out of range: {index}")
            cells.insert(index, cell)
        else:
            if index < 0 or index >= len(cells):
                raise IndexError(f"Notebook cell index out of range: {index}")
            cells[index] = cell
        changed = {"cell_type": cell["cell_type"], "source_lines": len(cell["source"])}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "cell_number": index, "edit_mode": mode, "cell_count": len(cells), **changed}


def create_worktree(
    project_root: Path,
    session_dir: Path,
    *,
    name: str = "",
    branch: str = "",
    base: str = "",
) -> dict[str, Any]:
    slug = _slug(name or f"worktree-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    worktree_root = runtime_state_path(project_root, "worktrees")
    target = worktree_root / slug
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "worktree", "add"]
    if branch:
        command.extend(["-b", branch])
    command.append(str(target))
    if base:
        command.append(base)
    completed = subprocess.run(command, cwd=str(project_root), text=True, capture_output=True, check=False)
    record = {
        "name": slug,
        "path": str(target),
        "branch": branch,
        "base": base,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "created_at": now(),
    }
    _append_jsonl(session_dir / "worktrees.jsonl", record)
    if completed.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {completed.stderr[-1000:]}")
    return record


def exit_worktree(project_root: Path, session_dir: Path, *, path: str, remove: bool = False) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    command = ["git", "worktree", "remove", str(target)] if remove else ["git", "worktree", "list", "--porcelain"]
    completed = subprocess.run(command, cwd=str(project_root), text=True, capture_output=True, check=False)
    record = {
        "path": str(target),
        "remove": remove,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "updated_at": now(),
    }
    _append_jsonl(session_dir / "worktrees.jsonl", record)
    if remove and completed.returncode != 0:
        raise RuntimeError(f"git worktree remove failed: {completed.stderr[-1000:]}")
    return record


def create_cron(
    session_dir: Path,
    *,
    name: str,
    schedule: str,
    command: str,
    metadata: dict[str, Any] | None = None,
    recurring: bool = True,
    durable: bool = False,
) -> dict[str, Any]:
    state = _load_state(session_dir / "cron.json", default={"jobs": []})
    jobs = state.setdefault("jobs", [])
    if not isinstance(jobs, list):
        jobs = []
        state["jobs"] = jobs
    effective_metadata = metadata or {}
    job = {
        "id": f"cron-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}",
        "name": name,
        "schedule": schedule,
        "cron": schedule,
        "prompt": command,
        "command": command,
        "recurring": recurring,
        "durable": durable,
        "status": "scheduled",
        "metadata": effective_metadata,
        "next_fire_at": _next_fire_at(schedule, effective_metadata),
        "created_at": now(),
        "updated_at": now(),
    }
    jobs.insert(0, job)
    _write_state(session_dir / "cron.json", state)
    return job


def start_cron_timer(session_dir: Path, job: dict[str, Any], *, max_delay_seconds: float = 3600.0) -> None:
    next_fire_at = str(job.get("next_fire_at") or "")
    if not next_fire_at:
        return
    try:
        fire_at = datetime.fromisoformat(next_fire_at)
    except ValueError:
        return
    delay = (fire_at - datetime.now()).total_seconds()
    if delay < 0:
        delay = 0
    if delay > max_delay_seconds:
        return

    def run() -> None:
        time.sleep(delay)
        _fire_cron_job(session_dir, str(job.get("id") or ""))

    thread = threading.Thread(target=run, name=f"runtime-cron-{job.get('id', '')}", daemon=True)
    thread.start()


def list_cron(session_dir: Path) -> list[dict[str, Any]]:
    state = _load_state(session_dir / "cron.json", default={"jobs": []})
    jobs = state.get("jobs")
    return [item for item in jobs if isinstance(item, dict)] if isinstance(jobs, list) else []


def delete_cron(session_dir: Path, *, job_id: str) -> dict[str, Any]:
    state = _load_state(session_dir / "cron.json", default={"jobs": []})
    jobs = state.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    deleted = None
    remaining = []
    for item in jobs:
        if isinstance(item, dict) and item.get("id") == job_id:
            deleted = dict(item)
            deleted["status"] = "deleted"
            deleted["updated_at"] = now()
            continue
        remaining.append(item)
    state["jobs"] = remaining
    _write_state(session_dir / "cron.json", state)
    if deleted is None:
        raise KeyError(f"cron job not found: {job_id}")
    return deleted


def _fire_cron_job(session_dir: Path, job_id: str) -> None:
    path = session_dir / "cron.json"
    state = _load_state(path, default={"jobs": []})
    jobs = state.get("jobs")
    if not isinstance(jobs, list):
        return
    kept = []
    fired: dict[str, Any] | None = None
    for item in jobs:
        if not isinstance(item, dict):
            continue
        if item.get("id") != job_id:
            kept.append(item)
            continue
        if item.get("status") != "scheduled":
            kept.append(item)
            continue
        fired = dict(item)
        recurring = bool(item.get("recurring", True))
        item["last_fired_at"] = now()
        item["updated_at"] = now()
        if recurring:
            next_fire_at = _next_fire_at(str(item.get("schedule") or item.get("cron") or ""), item.get("metadata") if isinstance(item.get("metadata"), dict) else {}, after=datetime.now())
            item["next_fire_at"] = next_fire_at
            item["status"] = "scheduled" if next_fire_at else "completed"
            kept.append(item)
        else:
            item["status"] = "fired"
            fired = dict(item)
    state["jobs"] = kept
    _write_state(path, state)
    if fired is not None:
        _append_jsonl(
            session_dir / "cron-fires.jsonl",
            {
                "id": fired.get("id"),
                "name": fired.get("name"),
                "prompt": fired.get("prompt") or fired.get("command") or "",
                "schedule": fired.get("schedule") or fired.get("cron") or "",
                "fired_at": now(),
            },
        )


def register_user_file(session_dir: Path, source: Path, *, copy: bool = False, max_chars: int = 12000) -> dict[str, Any]:
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"user file not found: {source}")
    target = source
    if copy:
        target_dir = session_dir / "user-files"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.copy2(source, target)
    try:
        preview = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
        binary = False
    except OSError:
        preview = ""
        binary = True
    record = {"source": str(source), "path": str(target), "copied": copy, "binary": binary, "preview": preview, "registered_at": now()}
    _append_jsonl(session_dir / "user-files.jsonl", {key: value for key, value in record.items() if key != "preview"})
    return record


def snip_file(path: Path, *, start_line: int = 1, end_line: int | None = None, max_lines: int = 200) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = end_line if end_line is not None else min(len(lines), start + max_lines - 1)
    end = min(len(lines), max(start, end), start + max_lines - 1)
    selected = lines[start - 1:end]
    return {"path": str(path), "start_line": start, "end_line": end, "text": "\n".join(selected), "total_lines": len(lines)}


def append_session_record(session_dir: Path, filename: str, record: dict[str, Any]) -> Path:
    record = {"updated_at": now(), **record}
    path = session_dir / filename
    _append_jsonl(path, record)
    return path


def upsert_named_state(session_dir: Path, filename: str, *, key: str, value: dict[str, Any]) -> dict[str, Any]:
    path = session_dir / filename
    state = _load_state(path, default={"items": {}})
    items = state.setdefault("items", {})
    if not isinstance(items, dict):
        items = {}
        state["items"] = items
    items[key] = {"updated_at": now(), **value}
    _write_state(path, state)
    return items[key]


def delete_named_state(session_dir: Path, filename: str, *, key: str) -> dict[str, Any]:
    path = session_dir / filename
    state = _load_state(path, default={"items": {}})
    items = state.get("items")
    if not isinstance(items, dict) or key not in items:
        raise KeyError(f"state item not found: {key}")
    deleted = items.pop(key)
    _write_state(path, state)
    return deleted if isinstance(deleted, dict) else {"value": deleted}


def load_named_state(session_dir: Path, filename: str) -> dict[str, Any]:
    return _load_state(session_dir / filename, default={"items": {}})


def _notebook_cell(*, source: str, cell_type: str) -> dict[str, Any]:
    clean_type = cell_type if cell_type in {"code", "markdown", "raw"} else "code"
    lines = source.splitlines(keepends=True)
    if source and (not lines or not lines[-1].endswith("\n")):
        lines[-1:] = [lines[-1] if lines else source]
    cell = {"cell_type": clean_type, "metadata": {}, "source": lines}
    if clean_type == "code":
        cell.update({"execution_count": None, "outputs": []})
    return cell


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _load_state(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value).strip("-")
    return slug[:64] or "worktree"


def _next_fire_at(schedule: str, metadata: dict[str, Any], *, after: datetime | None = None) -> str:
    base = after or datetime.now()
    delay = metadata.get("delay_seconds", metadata.get("run_after_seconds"))
    if delay is not None:
        try:
            return (base + timedelta(seconds=max(0.0, float(delay)))).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return ""
    value = schedule.strip()
    if not value:
        return ""
    if value.lower() in {"now", "once"}:
        return base.isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        pass
    parts = value.split()
    if len(parts) != 5:
        return ""
    start = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for offset in range(0, 366 * 24 * 60):
        candidate = start + timedelta(minutes=offset)
        if _cron_matches(parts, candidate):
            return candidate.isoformat(timespec="seconds")
    return ""


def _cron_matches(parts: list[str], candidate: datetime) -> bool:
    values = [candidate.minute, candidate.hour, candidate.day, candidate.month, (candidate.weekday() + 1) % 7]
    return all(_field_matches(field, value) for field, value in zip(parts, values))


def _field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            try:
                interval = int(part[2:])
            except ValueError:
                continue
            if interval > 0 and value % interval == 0:
                return True
            continue
        if "-" in part:
            try:
                start, end = [int(piece) for piece in part.split("-", 1)]
            except ValueError:
                continue
            if start <= value <= end:
                return True
            continue
        try:
            if int(part) == value:
                return True
        except ValueError:
            continue
    return False
