from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLD = 60000
DEFAULT_PREVIEW = 6000


def compact_tool_result(
    result: Any,
    *,
    session_dir: Path,
    tool_id: str,
    threshold: int = DEFAULT_THRESHOLD,
    preview_chars: int = DEFAULT_PREVIEW,
) -> Any:
    replacements: list[dict[str, Any]] = []
    compacted = _compact_value(
        result.data,
        session_dir=session_dir,
        tool_id=tool_id,
        path=("data",),
        threshold=threshold,
        preview_chars=preview_chars,
        replacements=replacements,
    )
    if not replacements:
        return result
    write_replacement_manifest(session_dir, replacements)
    return replace(
        result,
        data={
            **compacted,
            "_large_result_replacements": replacements,
        }
        if isinstance(compacted, dict)
        else {"value": compacted, "_large_result_replacements": replacements},
    )


def _compact_value(
    value: Any,
    *,
    session_dir: Path,
    tool_id: str,
    path: tuple[str, ...],
    threshold: int,
    preview_chars: int,
    replacements: list[dict[str, Any]],
) -> Any:
    if isinstance(value, str):
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= threshold:
            return value
        rel_path = Path("large-tool-results") / f"{tool_id}-{'-'.join(_safe(part) for part in path)}.txt"
        output = session_dir / rel_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(value, encoding="utf-8")
        preview = _preview(value, preview_chars)
        record = {
            "tool_id": tool_id,
            "json_path": ".".join(path),
            "path": str(output),
            "bytes": len(encoded),
            "preview_chars": len(preview),
            "replacement_text": (
                f"[LARGE_TOOL_RESULT bytes={len(encoded)} full_path={output}]\n"
                f"{preview}\n"
                "[END_PREVIEW]"
            ),
        }
        replacements.append(record)
        return record["replacement_text"]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(
                item,
                session_dir=session_dir,
                tool_id=tool_id,
                path=(*path, str(key)),
                threshold=threshold,
                preview_chars=preview_chars,
                replacements=replacements,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _compact_value(
                item,
                session_dir=session_dir,
                tool_id=tool_id,
                path=(*path, str(index)),
                threshold=threshold,
                preview_chars=preview_chars,
                replacements=replacements,
            )
            for index, item in enumerate(value)
        ]
    return value


def _preview(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    return text[:cut] + "\n[TRUNCATED_PREVIEW]"


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)[:80] or "value"


def write_replacement_manifest(session_dir: Path, replacements: list[dict[str, Any]]) -> None:
    if not replacements:
        return
    path = session_dir / "large-tool-results" / "manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in replacements:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
