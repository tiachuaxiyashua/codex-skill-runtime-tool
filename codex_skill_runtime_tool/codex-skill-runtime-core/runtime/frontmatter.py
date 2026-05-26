from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MarkdownDocument:
    path: Path
    metadata: dict[str, Any]
    body: str
    raw: str


def read_markdown_document(path: Path) -> MarkdownDocument:
    raw = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(raw)
    return MarkdownDocument(path=path, metadata=metadata, body=body, raw=raw)


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    normalized = raw.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, raw

    end = normalized.find("\n---\n", 4)
    if end == -1:
        return {}, raw

    frontmatter = normalized[4:end]
    body = normalized[end + len("\n---\n") :]
    return _parse_yaml(frontmatter), body


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]

        parsed = yaml.safe_load(text) or {}
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
    except Exception:
        pass
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    current_block_key: str | None = None
    block_lines: list[str] = []

    def flush_block() -> None:
        nonlocal current_block_key, block_lines
        if current_block_key is not None:
            result[current_block_key] = "\n".join(block_lines).rstrip()
            current_block_key = None
            block_lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if current_block_key is not None:
            if raw_line.startswith((" ", "\t")) or not stripped:
                block_lines.append(raw_line.lstrip())
                continue
            flush_block()

        line = stripped
        if not line or line.startswith("#") or ":" not in line:
            if current_list_key and line.startswith("- "):
                if not isinstance(result.get(current_list_key), list):
                    result[current_list_key] = []
                result[current_list_key].append(_parse_value(line[2:].strip()))
            continue
        if current_list_key and line.startswith("- "):
            if not isinstance(result.get(current_list_key), list):
                result[current_list_key] = []
            result[current_list_key].append(_parse_value(line[2:].strip()))
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value in {"|", ">"}:
            current_list_key = None
            current_block_key = key
            block_lines = []
            continue
        result[key] = [] if value == "" else _parse_value(value)
        current_list_key = key if value == "" else None
    flush_block()
    return result


def _parse_value(value: str) -> Any:
    if value == "":
        return ""

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    if value.isdigit():
        return int(value)

    if "," in value and not value.startswith("["):
        return [part.strip() for part in value.split(",") if part.strip()]

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("\"'") for part in inner.split(",")]

    return value
