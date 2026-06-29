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
    lines = _yaml_lines(text)
    if not lines:
        return {}
    parsed, _ = _parse_yaml_block(lines, 0, lines[0][0])
    return parsed if isinstance(parsed, dict) else {}


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        result.append((indent, raw_line.strip()))
    return result


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    if lines[index][1].startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_dict(lines, index, indent)


def _parse_yaml_dict(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            index += 1
            continue
        if content.startswith("- ") or ":" not in content:
            break

        key, raw_value = content.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value in {"|", ">"}:
            block, index = _parse_literal_block(lines, index + 1, indent)
            result[key] = block
            continue
        if value:
            result[key] = _parse_value(value)
            index += 1
            continue
        if index + 1 < len(lines) and lines[index + 1][0] > line_indent:
            child, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
            result[key] = child
        else:
            result[key] = {}
            index += 1
    return result, index


def _parse_yaml_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            index += 1
            continue
        if not content.startswith("- "):
            break

        item = content[2:].strip()
        next_index = index + 1
        child: Any = None
        if next_index < len(lines) and lines[next_index][0] > line_indent:
            child, next_index = _parse_yaml_block(lines, next_index, lines[next_index][0])

        if not item:
            result.append(child if child is not None else "")
        elif ":" in item and not item.startswith(("http://", "https://")):
            key, raw_value = item.split(":", 1)
            value = raw_value.strip()
            record: dict[str, Any] = {key.strip(): _parse_value(value) if value else {}}
            if isinstance(child, dict):
                record.update(child)
            result.append(record)
        else:
            result.append(_parse_value(item))
        index = next_index
    return result, index


def _parse_literal_block(lines: list[tuple[int, str]], index: int, parent_indent: int) -> tuple[str, int]:
    block_lines: list[str] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent <= parent_indent:
            break
        block_lines.append(content)
        index += 1
    return "\n".join(block_lines).rstrip(), index


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
