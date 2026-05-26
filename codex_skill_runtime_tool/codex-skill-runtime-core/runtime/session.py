from __future__ import annotations

import json
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


class RuntimeSession:
    def __init__(self, root: Path, label: str) -> None:
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
        self.id = f"{now}-{clean_label}".strip("-")
        self.label = label
        self.root = root.resolve()
        self.dir = runtime_state_path(self.root, "sessions", self.id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.transcript_path = self.dir / "transcript.jsonl"

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
