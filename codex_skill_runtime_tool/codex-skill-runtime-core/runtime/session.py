from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path
from .transcript import append_transcript_event


@dataclass
class RuntimeEvent:
    type: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class RuntimeSession:
    def __init__(self, root: Path, label: str, *, metadata: dict[str, Any] | None = None) -> None:
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
        self.id = f"{now}-{clean_label}".strip("-")
        self.label = label
        self.root = root.resolve()
        self.dir = runtime_state_path(self.root, "sessions", self.id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.transcript_path = self.dir / "transcript.jsonl"
        self._state_lock = threading.RLock()
        self._node_counter = 0
        self._nodes: dict[str, dict[str, Any]] = {}
        self._root_node_id: str | None = None
        self._status = "created"
        self.metadata = dict(metadata or {})
        self._write_ui_state()

    def path(self, *parts: str) -> Path:
        target = self.dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(self, relative: str, text: str) -> Path:
        path = self.path(relative)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, relative: str, data: Any) -> Path:
        path = self.path(relative)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def set_status(self, status: str, **extra: Any) -> None:
        with self._state_lock:
            self._status = status
            self._write_ui_state(extra=extra)

    def start_node(
        self,
        type_: str,
        name: str,
        *,
        parent_id: str | None = None,
        display_name: str | None = None,
        status: str = "running",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._state_lock:
            self._node_counter += 1
            node_id = f"node-{self._node_counter:04d}"
            parent = parent_id if parent_id is not None else self._default_parent_for(type_)
            namespace, short_name = _split_namespace(name)
            node = {
                "id": node_id,
                "session_id": self.id,
                "parent_id": parent,
                "type": type_,
                "namespace": namespace,
                "name": short_name,
                "display_name": display_name or name,
                "status": status,
                "started_at": _now(),
                "finished_at": _now() if status in {"done", "passed", "failed", "blocked", "cancelled"} else None,
                "evidence": evidence or {},
                "metadata": metadata or {},
            }
            self._nodes[node_id] = node
            if self._root_node_id is None:
                self._root_node_id = node_id
            if status in {"running", "waiting_user"}:
                self._status = "waiting_user" if status == "waiting_user" else "running"
            self._write_ui_state()
            return node_id

    def update_node(
        self,
        node_id: str,
        *,
        status: str | None = None,
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._state_lock:
            node = self._nodes.get(node_id)
            if not node:
                return
            if status is not None:
                node["status"] = status
                if status in {"done", "passed", "failed", "blocked", "cancelled"}:
                    node["finished_at"] = node.get("finished_at") or _now()
            if evidence:
                node.setdefault("evidence", {}).update(evidence)
            if metadata:
                node.setdefault("metadata", {}).update(metadata)
            self._write_ui_state()

    def finish_node(
        self,
        node_id: str | None,
        *,
        status: str = "done",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if node_id is None:
            return
        self.update_node(node_id, status=status, evidence=evidence, metadata=metadata)

    def add_artifact(
        self,
        path: str | Path,
        *,
        type_: str | None = None,
        created_by_node_id: str | None = None,
        created_by_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        artifact_path = str(path)
        item = {
            "path": artifact_path,
            "type": type_ or _artifact_type(artifact_path),
            "created_by_node_id": created_by_node_id or self._latest_active_node_id(),
            "created_by_agent": created_by_agent or self._latest_active_agent_name(),
            "previewable": _is_previewable(artifact_path),
            "created_at": _now(),
            "metadata": metadata or {},
        }
        with self._state_lock:
            artifacts_path = self.path("artifacts.json")
            try:
                data = json.loads(artifacts_path.read_text(encoding="utf-8")) if artifacts_path.exists() else {}
            except ValueError:
                data = {}
            artifacts = data.get("artifacts") if isinstance(data, dict) else []
            if not isinstance(artifacts, list):
                artifacts = []
            artifacts = [existing for existing in artifacts if isinstance(existing, dict) and existing.get("path") != artifact_path]
            artifacts.append(item)
            artifacts_path.write_text(
                json.dumps({"session_id": self.id, "artifacts": artifacts[-500:]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._write_ui_state()

    def set_metadata(self, **metadata: Any) -> None:
        with self._state_lock:
            self.metadata.update({key: value for key, value in metadata.items() if value is not None})
            self._write_ui_state()

    def touched_paths(self) -> list[str]:
        paths: list[str] = []
        read_state = self.path("read-state.json")
        if read_state.exists():
            try:
                data = json.loads(read_state.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                paths.extend(str(item.get("path") or key) for key, item in data.items() if isinstance(item, dict))
        artifacts_path = self.path("artifacts.json")
        if artifacts_path.exists():
            try:
                artifacts = json.loads(artifacts_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                artifacts = {}
            items = artifacts.get("artifacts") if isinstance(artifacts, dict) else []
            if isinstance(items, list):
                paths.extend(str(item.get("path")) for item in items if isinstance(item, dict) and item.get("path"))
        return _unique_text(paths)

    def record_invoked_skill(
        self,
        *,
        name: str,
        path: Path,
        content: str,
        agent: str | None = None,
        metadata: dict[str, Any] | None = None,
        max_chars: int = 60000,
    ) -> None:
        record = {
            "name": name,
            "path": str(path),
            "agent": agent or "",
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
            "metadata": metadata or {},
            "invoked_at": _now(),
        }
        target = self.path("invoked-skills.json")
        try:
            data = json.loads(target.read_text(encoding="utf-8", errors="replace")) if target.exists() else {}
        except (OSError, ValueError):
            data = {}
        records = data.get("skills") if isinstance(data, dict) else []
        if not isinstance(records, list):
            records = []
        records = [item for item in records if isinstance(item, dict) and not (item.get("name") == name and item.get("agent") == (agent or ""))]
        records.insert(0, record)
        target.write_text(json.dumps({"session_id": self.id, "skills": records[:100]}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.event("skill.invoked", f"Loaded skill {name}", path=str(path), agent=agent or "")

    def invoked_skills_context(self, *, max_chars: int = 40000) -> str:
        target = self.path("invoked-skills.json")
        if not target.exists():
            return ""
        try:
            data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return ""
        records = data.get("skills") if isinstance(data, dict) else []
        if not isinstance(records, list) or not records:
            return ""
        lines = [
            "## Runtime Invoked Skills",
            "",
            "These skills were previously loaded in this session. Preserve their operative instructions across compacted tool observations.",
            "",
        ]
        remaining = max_chars
        for item in records:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "")
            block = (
                f"### {item.get('name', '')}\n\n"
                f"Source: `{item.get('path', '')}`\n"
                f"Agent: `{item.get('agent', '')}`\n\n"
                f"{content}\n"
            )
            if len(block) > remaining:
                block = block[:remaining] + "\n[TRUNCATED INVOKED SKILL]\n"
            lines.append(block)
            remaining -= len(block)
            if remaining <= 0:
                break
        return "\n".join(lines).strip()

    def event(self, type_: str, message: str, **data: Any) -> None:
        event = RuntimeEvent(type=type_, message=message, data=data)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        append_transcript_event(
            self.transcript_path,
            session_id=self.id,
            type_=type_,
            message=message,
            data=data,
        )

    def transcript_event(self, type_: str, message: str, **data: Any) -> None:
        append_transcript_event(
            self.transcript_path,
            session_id=self.id,
            type_=type_,
            message=message,
            data=data,
        )

    def update_read_state(self, path: Path, content: str, *, max_chars: int = 20000) -> None:
        state_path = self.path("read-state.json")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except ValueError:
            state = {}
        if not isinstance(state, dict):
            state = {}
        key = str(path.resolve())
        state[key] = {
            "path": key,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _default_parent_for(self, type_: str) -> str | None:
        if type_ in {"skill", "agent"} and self._root_node_id is None:
            return None
        if type_ == "agent":
            group = self._latest_active_node_id(type_="parallel_group")
            if group:
                return group
            return self._latest_active_node_id(type_="skill")
        if type_ == "tool":
            return self._latest_active_node_id(type_="agent") or self._latest_active_node_id(type_="skill")
        if type_ in {"gate", "question", "artifact", "mcp", "parallel_group"}:
            return self._latest_active_node_id(type_="agent") or self._latest_active_node_id(type_="skill")
        return self._root_node_id

    def _latest_active_node_id(self, *, type_: str | None = None) -> str | None:
        for node in reversed(list(self._nodes.values())):
            if type_ is not None and node.get("type") != type_:
                continue
            if node.get("status") in {"running", "waiting_user", "queued"}:
                return str(node.get("id"))
        return None

    def _latest_active_agent_name(self) -> str:
        node_id = self._latest_active_node_id(type_="agent")
        if not node_id:
            return ""
        node = self._nodes.get(node_id, {})
        return str(node.get("display_name") or node.get("name") or "")

    def _write_ui_state(self, *, extra: dict[str, Any] | None = None) -> None:
        nodes = list(self._nodes.values())
        child_ids: dict[str, list[str]] = {}
        for node in nodes:
            parent_id = node.get("parent_id")
            if parent_id:
                child_ids.setdefault(str(parent_id), []).append(str(node.get("id")))
        tree_nodes = []
        for node in nodes:
            tree_nodes.append({**node, "child_ids": child_ids.get(str(node.get("id")), [])})

        active_nodes = [node for node in nodes if node.get("status") in {"running", "waiting_user", "queued"}]
        current_agents = [
            {
                "id": node.get("id", ""),
                "name": node.get("display_name") or node.get("name") or "",
                "status": node.get("status", ""),
                "current_action": _node_action(node),
                "started_at": node.get("started_at"),
            }
            for node in active_nodes
            if node.get("type") == "agent"
        ]
        current_skill = ""
        for node in reversed(active_nodes):
            if node.get("type") == "skill":
                current_skill = str(node.get("display_name") or node.get("name") or "")
                break
        waiting_question = None
        for node in reversed(active_nodes):
            if node.get("type") == "question":
                waiting_question = node
                break
        state = {
            "session_id": self.id,
            "label": self.label,
            "root": str(self.root),
            "metadata": self.metadata,
            "status": self._effective_session_status(active_nodes),
            "current_skill": current_skill,
            "current_agents": current_agents,
            "active_node_ids": [node.get("id", "") for node in active_nodes],
            "waiting_question": waiting_question,
            "last_event": "",
            "updated_at": _now(),
        }
        if extra:
            state.update(extra)
        self.path("session-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self.path("task-tree.json").write_text(
            json.dumps(
                {
                    "session_id": self.id,
                    "root_node_id": self._root_node_id,
                    "nodes": tree_nodes,
                    "updated_at": state["updated_at"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _effective_session_status(self, active_nodes: list[dict[str, Any]]) -> str:
        if any(node.get("status") == "waiting_user" for node in active_nodes):
            return "waiting_user"
        if active_nodes:
            return "running"
        if self._status in {"done", "failed", "blocked", "cancelled"}:
            return self._status
        terminal = [node for node in self._nodes.values() if node.get("status") in {"failed", "blocked"}]
        if terminal:
            return str(terminal[-1].get("status"))
        return self._status


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _split_namespace(value: str) -> tuple[str, str]:
    if ":" not in value:
        return "", value
    namespace, name = value.split(":", 1)
    return namespace, name


def _unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _node_action(node: dict[str, Any]) -> str:
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        for key in ("current_action", "purpose", "tool", "command"):
            value = metadata.get(key)
            if value:
                return str(value)
    return str(node.get("display_name") or node.get("name") or "")


def _artifact_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return "image"
    if suffix in {".wav", ".mp3", ".ogg", ".flac", ".opus"}:
        return "audio"
    if suffix in {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml"}:
        return "document"
    if suffix in {".tscn", ".gd", ".godot", ".tres", ".res"}:
        return "godot"
    return "file"


def _is_previewable(path: str) -> bool:
    return _artifact_type(path) in {"image", "audio", "document"}
