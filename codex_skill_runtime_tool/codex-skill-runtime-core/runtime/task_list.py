from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


VALID_STATUSES = {"pending", "in_progress", "completed"}


def create_task(
    session_dir: Path,
    *,
    subject: str,
    description: str,
    active_form: str = "",
    owner: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _load(session_dir)
    task_id = str(int(state.get("high_water_mark") or 0) + 1)
    state["high_water_mark"] = int(task_id)
    task = {
        "id": task_id,
        "subject": subject,
        "description": description,
        "activeForm": active_form,
        "owner": owner,
        "status": "pending",
        "blocks": [],
        "blockedBy": [],
        "metadata": metadata or {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    state.setdefault("tasks", {})[task_id] = task
    _save(session_dir, state)
    return task


def get_task(session_dir: Path, task_id: str) -> dict[str, Any] | None:
    task = _load(session_dir).get("tasks", {}).get(str(task_id))
    return dict(task) if isinstance(task, dict) else None


def list_tasks(session_dir: Path) -> list[dict[str, Any]]:
    tasks = _load(session_dir).get("tasks", {})
    if not isinstance(tasks, dict):
        return []
    rows = [dict(item) for item in tasks.values() if isinstance(item, dict)]
    rows.sort(key=lambda item: int(str(item.get("id") or "0")) if str(item.get("id") or "0").isdigit() else 0)
    return [task for task in rows if not _is_internal(task)]


def update_task(
    session_dir: Path,
    *,
    task_id: str,
    subject: str | None = None,
    description: str | None = None,
    active_form: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    add_blocks: list[str] | None = None,
    add_blocked_by: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _load(session_dir)
    tasks = state.setdefault("tasks", {})
    if not isinstance(tasks, dict) or task_id not in tasks or not isinstance(tasks[task_id], dict):
        return {"success": False, "taskId": task_id, "updatedFields": [], "error": "Task not found"}
    task = tasks[task_id]
    updated: list[str] = []
    old_status = str(task.get("status") or "pending")
    if status == "deleted":
        tasks.pop(task_id, None)
        for other in tasks.values():
            if not isinstance(other, dict):
                continue
            other["blocks"] = [item for item in _string_list(other.get("blocks")) if item != task_id]
            other["blockedBy"] = [item for item in _string_list(other.get("blockedBy")) if item != task_id]
        _save(session_dir, state)
        return {
            "success": True,
            "taskId": task_id,
            "updatedFields": ["deleted"],
            "statusChange": {"from": old_status, "to": "deleted"},
        }
    if subject is not None and subject != str(task.get("subject") or ""):
        task["subject"] = subject
        updated.append("subject")
    if description is not None and description != str(task.get("description") or ""):
        task["description"] = description
        updated.append("description")
    if active_form is not None and active_form != str(task.get("activeForm") or ""):
        task["activeForm"] = active_form
        updated.append("activeForm")
    if owner is not None and owner != str(task.get("owner") or ""):
        task["owner"] = owner
        updated.append("owner")
    if status is not None:
        if status not in VALID_STATUSES:
            return {"success": False, "taskId": task_id, "updatedFields": [], "error": f"Invalid status: {status}"}
        if status != old_status:
            task["status"] = status
            updated.append("status")
    if metadata is not None:
        merged = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        merged = dict(merged)
        for key, value in metadata.items():
            if value is None:
                merged.pop(str(key), None)
            else:
                merged[str(key)] = value
        task["metadata"] = merged
        updated.append("metadata")
    if add_blocks:
        task_blocks = _string_list(task.get("blocks"))
        for block_id in add_blocks:
            if block_id not in task_blocks:
                task_blocks.append(block_id)
        task["blocks"] = task_blocks
        for block_id in add_blocks:
            _add_unique_reverse(tasks, block_id, "blockedBy", task_id)
        updated.append("blocks")
    if add_blocked_by:
        blocked_by = _string_list(task.get("blockedBy"))
        for blocker_id in add_blocked_by:
            if blocker_id not in blocked_by:
                blocked_by.append(blocker_id)
        task["blockedBy"] = blocked_by
        for blocker_id in add_blocked_by:
            _add_unique_reverse(tasks, blocker_id, "blocks", task_id)
        updated.append("blockedBy")
    task["updated_at"] = _now()
    _save(session_dir, state)
    result: dict[str, Any] = {"success": True, "taskId": task_id, "updatedFields": updated}
    if status is not None and status != old_status:
        result["statusChange"] = {"from": old_status, "to": status}
    return result


def _load(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "tasks.json"
    if not path.exists():
        return {"high_water_mark": 0, "tasks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {"high_water_mark": 0, "tasks": {}}
    if not isinstance(data, dict):
        return {"high_water_mark": 0, "tasks": {}}
    data.setdefault("tasks", {})
    data.setdefault("high_water_mark", 0)
    return data


def _save(session_dir: Path, state: dict[str, Any]) -> None:
    path = session_dir / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _add_unique_reverse(tasks: dict[str, Any], task_id: str, field: str, value: str) -> None:
    target = tasks.get(str(task_id))
    if not isinstance(target, dict):
        return
    values = _string_list(target.get(field))
    if value not in values:
        values.append(value)
    target[field] = values
    target["updated_at"] = _now()


def _is_internal(task: dict[str, Any]) -> bool:
    metadata = task.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("_internal"))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
