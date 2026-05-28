from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"done", "failed", "cancelled", "blocked", "unknown"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class JobRecord:
    id: str
    operation: str
    status: str
    command: list[str]
    cwd: str
    pid: int | None = None
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    finished_at: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobRegistry:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.path = self.state_root / "jobs" / "jobs.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        operation: str,
        command: list[str],
        cwd: Path,
        stdout: Path,
        stderr: Path,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = JobRecord(
            id=datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
            operation=operation,
            status="starting",
            command=list(command),
            cwd=str(cwd),
            stdout=str(stdout),
            stderr=str(stderr),
            metadata=metadata or {},
        )
        self.upsert(record)
        return record

    def upsert(self, record: JobRecord) -> None:
        jobs = self._load()
        jobs = [item for item in jobs if item.get("id") != record.id]
        jobs.insert(0, record.to_dict())
        self._write(jobs[:1000])

    def mark_started(self, job_id: str, *, pid: int) -> dict[str, Any] | None:
        return self.update(job_id, status="running", pid=pid)

    def mark_finished(self, job_id: str, *, returncode: int | None) -> dict[str, Any] | None:
        status = "done" if returncode == 0 else "failed"
        return self.update(job_id, status=status, returncode=returncode, finished_at=_now())

    def mark_cancel_requested(self, job_id: str) -> dict[str, Any] | None:
        return self.update(job_id, status="cancel_requested")

    def mark_unknown_if_orphaned(self, job_id: str) -> dict[str, Any] | None:
        record = self.get(job_id)
        if not record or record.get("status") in TERMINAL_STATES:
            return record
        pid = record.get("pid")
        if isinstance(pid, int) and _pid_is_running(pid):
            return record
        return self.update(job_id, status="unknown", finished_at=_now())

    def update(self, job_id: str, **updates: Any) -> dict[str, Any] | None:
        jobs = self._load()
        updated: dict[str, Any] | None = None
        for item in jobs:
            if item.get("id") != job_id:
                continue
            item.update({key: value for key, value in updates.items() if value is not None})
            item["updated_at"] = _now()
            updated = item
            break
        if updated is not None:
            self._write(jobs)
        return updated

    def get(self, job_id: str) -> dict[str, Any] | None:
        for item in self._load():
            if item.get("id") == job_id:
                return item
        return None

    def list(self, *, limit: int = 300) -> list[dict[str, Any]]:
        return self._load()[:limit]

    def cancel(self, job_id: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record is None:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        self.mark_cancel_requested(job_id)
        pid = record.get("pid")
        if not isinstance(pid, int):
            return {"ok": False, "error": "job has no pid", "job_id": job_id}
        try:
            _terminate_pid(pid)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "job_id": job_id, "pid": pid}
        updated = self.update(job_id, status="cancelled", finished_at=_now())
        return {"ok": True, "job": updated}

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return []
        if isinstance(data, dict):
            data = data.get("jobs", [])
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _write(self, jobs: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False)
        return
    os.kill(pid, signal.SIGTERM)

