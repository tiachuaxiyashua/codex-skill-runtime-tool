from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .session_memory import session_memory_context
from .token_budget import DEFAULT_AUTOCOMPACT_BUFFER_TOKENS, ContextBudgetResult


MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
DEFAULT_SESSION_MEMORY_COMPACT_MAX_CHARS = 40000
DEFAULT_SESSION_MEMORY_COMPACT_MIN_CHARS = 10000


@dataclass(frozen=True)
class CompactState:
    updated_at: str
    context_window_tokens: int | None
    target_tokens: int | None
    estimated_tokens_before: int
    estimated_tokens_after: int
    reserved_buffer_tokens: int
    autocompact_threshold_tokens: int | None
    above_autocompact_threshold: bool
    omitted_sections: list[str]
    compact_summary_path: str = ""


def record_compact_state(session: Any, budget: ContextBudgetResult) -> CompactState:
    threshold = None
    if budget.context_window_tokens is not None:
        threshold = max(0, budget.context_window_tokens - DEFAULT_AUTOCOMPACT_BUFFER_TOKENS)
    above = threshold is not None and budget.estimated_tokens_before >= threshold
    compact_summary_path = ""
    if above or budget.omitted:
        compact_summary_path = str(write_session_memory_compact(session))
    state = CompactState(
        updated_at=datetime.now().isoformat(timespec="seconds"),
        context_window_tokens=budget.context_window_tokens,
        target_tokens=budget.target_tokens,
        estimated_tokens_before=budget.estimated_tokens_before,
        estimated_tokens_after=budget.estimated_tokens_after,
        reserved_buffer_tokens=DEFAULT_AUTOCOMPACT_BUFFER_TOKENS,
        autocompact_threshold_tokens=threshold,
        above_autocompact_threshold=above,
        omitted_sections=budget.omitted,
        compact_summary_path=compact_summary_path,
    )
    path = _session_dir(session) / "compact-state.json"
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def write_session_memory_compact(
    session: Any,
    *,
    max_chars: int = DEFAULT_SESSION_MEMORY_COMPACT_MAX_CHARS,
    min_chars: int = DEFAULT_SESSION_MEMORY_COMPACT_MIN_CHARS,
) -> Path:
    session_dir = _session_dir(session)
    text = session_memory_context(session, max_chars=max_chars)
    if not text:
        text = "No session memory exists yet."
    if len(text) < min_chars:
        compact = text
    else:
        compact = text[:max_chars] + "\n[SESSION MEMORY COMPACTED]\n"
    path = session_dir / "session-memory" / "compact.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Session Memory Compact",
                "",
                f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
                f"- Max chars: {max_chars}",
                "",
                compact,
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def compact_state_context(session_or_dir: Any, *, max_chars: int = 8000) -> str:
    path = _session_dir(session_or_dir) / "compact-state.json"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED COMPACT STATE]\n"
    return f"## Runtime Compact State\n\nSource: `{path}`\n\n```json\n{text}\n```"


def _session_dir(session_or_dir: Any) -> Path:
    if isinstance(session_or_dir, Path):
        return session_or_dir
    if isinstance(session_or_dir, str):
        return Path(session_or_dir)
    value = getattr(session_or_dir, "dir", None)
    if isinstance(value, Path):
        return value
    return Path(value)
