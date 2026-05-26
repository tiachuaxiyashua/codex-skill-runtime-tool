from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path


@dataclass(frozen=True)
class BridgeEnvironment:
    environment_id: str
    bridge_id: str
    root: Path


class LocalBridge:
    """Local Bridge lifecycle compatible with Claude Code remote-control shape.

    This does not connect to Claude's private remote-control service. It preserves
    the execution-relevant mechanics: environment registration, work queue,
    session events, heartbeat, archive, reconnect pointers, and resumable state.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = runtime_state_path(self.project_root, "bridge")
        self.root.mkdir(parents=True, exist_ok=True)

    def register_environment(self, *, bridge_id: str | None = None, metadata: dict[str, Any] | None = None) -> BridgeEnvironment:
        bridge_id = bridge_id or str(uuid.uuid4())
        environment_id = f"env-{uuid.uuid4()}"
        env_dir = self.root / "environments" / environment_id
        env_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(
            env_dir / "state.json",
            {
                "environment_id": environment_id,
                "bridge_id": bridge_id,
                "created_at": _now(),
                "metadata": metadata or {},
                "status": "registered",
            },
        )
        return BridgeEnvironment(environment_id=environment_id, bridge_id=bridge_id, root=env_dir)

    def enqueue_work(self, environment_id: str, *, kind: str, data: dict[str, Any]) -> str:
        work_id = f"work-{uuid.uuid4()}"
        self._write_json(
            self._work_path(environment_id, work_id),
            {
                "id": work_id,
                "type": kind,
                "data": data,
                "state": "queued",
                "created_at": _now(),
                "heartbeat_at": None,
            },
        )
        return work_id

    def poll_work(self, environment_id: str) -> dict[str, Any] | None:
        work_dir = self.root / "environments" / environment_id / "work"
        if not work_dir.exists():
            return None
        for path in sorted(work_dir.glob("*.json")):
            data = self._read_json(path)
            if isinstance(data, dict) and data.get("state") == "queued":
                data["state"] = "delivered"
                data["delivered_at"] = _now()
                self._write_json(path, data)
                return data
        return None

    def ack_work(self, environment_id: str, work_id: str, *, state: str = "acknowledged") -> None:
        path = self._work_path(environment_id, work_id)
        data = self._read_json(path)
        if not isinstance(data, dict):
            return
        data["state"] = state
        data["ack_at"] = _now()
        self._write_json(path, data)

    def heartbeat(self, environment_id: str, work_id: str) -> None:
        path = self._work_path(environment_id, work_id)
        data = self._read_json(path)
        if not isinstance(data, dict):
            return
        data["heartbeat_at"] = _now()
        self._write_json(path, data)

    def write_session_event(self, session_id: str, event: dict[str, Any]) -> Path:
        path = self.root / "sessions" / session_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": _now(), **event}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def archive_session(self, session_id: str) -> None:
        path = self.root / "sessions" / session_id / "state.json"
        data = self._read_json(path)
        if not isinstance(data, dict):
            data = {"session_id": session_id}
        data["archived_at"] = _now()
        data["state"] = "archived"
        self._write_json(path, data)

    def reconnect_session(self, environment_id: str, session_id: str) -> Path:
        pointer = self.root / "bridge-pointer.json"
        data = {
            "environment_id": environment_id,
            "session_id": session_id,
            "updated_at": _now(),
        }
        self._write_json(pointer, data)
        return pointer

    def _work_path(self, environment_id: str, work_id: str) -> Path:
        return self.root / "environments" / environment_id / "work" / f"{work_id}.json"

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def bridge_context(project_root: Path, *, max_chars: int = 12000) -> str:
    root = runtime_state_path(project_root, "bridge")
    pointer = root / "bridge-pointer.json"
    if not pointer.exists():
        return ""
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    text = "## Runtime Bridge Context\n\n" + json.dumps(data, ensure_ascii=False, indent=2)
    return text[:max_chars]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
