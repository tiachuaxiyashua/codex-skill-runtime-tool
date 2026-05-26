from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path


@dataclass
class VoiceSession:
    session_id: str
    state: str = "idle"
    chunks: list[str] = field(default_factory=list)
    started_at: str | None = None
    finalized_at: str | None = None


class VoiceRuntime:
    """Execution-level voice lifecycle.

    The private Claude voice STT service is not reproduced here. This module
    preserves the runtime effect: voice input becomes timestamped transcript
    text that can be injected into the next prompt or replayed from evidence.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = runtime_state_path(self.project_root, "voice")
        self.root.mkdir(parents=True, exist_ok=True)

    def start(self) -> VoiceSession:
        session = VoiceSession(session_id=f"voice-{uuid.uuid4()}", state="recording", started_at=_now())
        self._write(session)
        return session

    def append_transcript(self, session_id: str, text: str) -> VoiceSession:
        session = self.load(session_id)
        if session.state not in {"recording", "processing"}:
            session.state = "recording"
        if text:
            session.chunks.append(text)
        self._write(session)
        return session

    def finalize(self, session_id: str) -> VoiceSession:
        session = self.load(session_id)
        session.state = "final"
        session.finalized_at = _now()
        self._write(session)
        latest = self.root / "latest-transcript.txt"
        latest.write_text(session_text(session), encoding="utf-8")
        return session

    def load(self, session_id: str) -> VoiceSession:
        path = self.root / f"{session_id}.json"
        if not path.exists():
            return VoiceSession(session_id=session_id, state="idle")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return VoiceSession(session_id=session_id, state="idle")
        return VoiceSession(
            session_id=str(data.get("session_id") or session_id),
            state=str(data.get("state") or "idle"),
            chunks=[str(item) for item in data.get("chunks", []) if item is not None],
            started_at=str(data.get("started_at")) if data.get("started_at") else None,
            finalized_at=str(data.get("finalized_at")) if data.get("finalized_at") else None,
        )

    def _write(self, session: VoiceSession) -> None:
        path = self.root / f"{session.session_id}.json"
        path.write_text(
            json.dumps(
                {
                    "session_id": session.session_id,
                    "state": session.state,
                    "chunks": session.chunks,
                    "started_at": session.started_at,
                    "finalized_at": session.finalized_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def voice_context(project_root: Path, *, max_chars: int = 12000) -> str:
    latest = runtime_state_path(project_root, "voice", "latest-transcript.txt")
    env_text = ""
    if latest.exists():
        env_text = latest.read_text(encoding="utf-8", errors="replace")
    if not env_text:
        env_text = ""
    if not env_text.strip():
        return ""
    return "## Runtime Voice Transcript\n\n" + env_text.strip()[:max_chars]


def session_text(session: VoiceSession) -> str:
    return " ".join(chunk.strip() for chunk in session.chunks if chunk.strip()).strip()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
