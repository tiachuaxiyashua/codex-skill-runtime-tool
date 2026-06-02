from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capabilities import discover_capabilities
from .loaders import SkillRepositoryLoader
from .mcp import discover_mcp_servers


@dataclass(frozen=True)
class SearchRecord:
    name: str
    kind: str
    description: str
    metadata: dict[str, Any]


RUNTIME_TOOL_RECORDS = [
    SearchRecord("read_file", "runtime-tool", "Read a text file and persist read state.", {}),
    SearchRecord("glob", "runtime-tool", "Find files by glob pattern.", {}),
    SearchRecord("grep", "runtime-tool", "Search text with a regular expression.", {}),
    SearchRecord("write_file", "runtime-tool", "Write a file inside the active workspace.", {}),
    SearchRecord("edit_file", "runtime-tool", "Replace one exact text occurrence in a file.", {}),
    SearchRecord("multi_edit", "runtime-tool", "Apply multiple exact text replacements in one file.", {}),
    SearchRecord("bash", "runtime-tool", "Run a shell command in the active workspace.", {}),
    SearchRecord("powershell", "runtime-tool", "Run a PowerShell command in the active workspace.", {}),
    SearchRecord("terminal_capture", "runtime-tool", "Run a terminal command and persist captured output.", {}),
    SearchRecord("repl", "runtime-tool", "Evaluate a small Python REPL snippet.", {}),
    SearchRecord("task_create", "runtime-tool", "Create a worker/subagent task.", {}),
    SearchRecord("task_get", "runtime-tool", "Read one persisted worker task record.", {}),
    SearchRecord("task_list", "runtime-tool", "List persisted worker task records.", {}),
    SearchRecord("task_output", "runtime-tool", "Read latest or full output from a worker task.", {}),
    SearchRecord("task_update", "runtime-tool", "Update a worker task or send a continuation prompt.", {}),
    SearchRecord("task_stop", "runtime-tool", "Stop a worker task.", {}),
    SearchRecord("skill", "runtime-tool", "Load a model-invocable skill by name for nested invocation.", {}),
    SearchRecord("tool_search", "runtime-tool", "Search runtime tools, skills, capabilities, and MCP servers.", {}),
    SearchRecord("plan_mode", "runtime-tool", "Enter, exit, or verify a persisted plan mode lifecycle.", {}),
    SearchRecord("web_browser", "runtime-tool", "Use a lightweight stateful browser for open/click/find/current.", {}),
    SearchRecord("list_mcp_resources", "runtime-tool", "List resources exposed by configured MCP servers.", {}),
    SearchRecord("read_mcp_resource", "runtime-tool", "Read one resource exposed by a configured MCP server.", {}),
    SearchRecord("mcp_elicitation", "runtime-tool", "Record or answer an MCP elicitation request.", {}),
]


def search_runtime_tools(
    project_root: Path,
    *,
    query: str,
    limit: int = 8,
    additional_dirs: list[Path] | None = None,
) -> list[dict[str, Any]]:
    records = list(RUNTIME_TOOL_RECORDS)
    records.extend(_skill_records(project_root, additional_dirs=additional_dirs))
    records.extend(_capability_records(project_root, additional_dirs=additional_dirs))
    records.extend(_mcp_records(project_root, additional_dirs=additional_dirs))
    scored: list[tuple[float, SearchRecord]] = []
    query_terms = _terms(query)
    for record in records:
        score = _score(query_terms, record)
        if score > 0 or not query_terms:
            scored.append((score, record))
    scored.sort(key=lambda pair: (pair[0], pair[1].kind, pair[1].name), reverse=True)
    return [
        {
            "name": record.name,
            "kind": record.kind,
            "description": record.description,
            "score": round(score, 3),
            "metadata": record.metadata,
        }
        for score, record in scored[: max(1, limit)]
    ]


def _skill_records(project_root: Path, *, additional_dirs: list[Path] | None) -> list[SearchRecord]:
    try:
        loader = SkillRepositoryLoader(project_root, additional_dirs=additional_dirs)
        listings = loader.skill_listings(model_only=True)
    except Exception:
        return []
    return [
        SearchRecord(
            item.name,
            "skill",
            item.description,
            {"path": str(item.path), "source": item.source, "context": item.context, "agent": item.agent},
        )
        for item in listings
    ]


def _capability_records(project_root: Path, *, additional_dirs: list[Path] | None) -> list[SearchRecord]:
    try:
        capabilities = discover_capabilities(project_root, additional_dirs=additional_dirs)
    except Exception:
        return []
    return [
        SearchRecord(
            item.name,
            "capability",
            item.description,
            item.to_dict(),
        )
        for item in capabilities
    ]


def _mcp_records(project_root: Path, *, additional_dirs: list[Path] | None) -> list[SearchRecord]:
    try:
        servers = discover_mcp_servers(project_root, additional_dirs=additional_dirs)
    except Exception:
        return []
    records = []
    for server in servers:
        instructions = str(server.config.get("instructions") or server.config.get("instruction") or "")
        records.append(
            SearchRecord(
                server.name,
                "mcp-server",
                instructions[:500] or f"MCP server {server.name}",
                {"aliases": list(server.aliases), "plugin": server.plugin_name or "", "transport": str(server.config.get("type") or "")},
            )
        )
    return records


def _score(query_terms: list[str], record: SearchRecord) -> float:
    if not query_terms:
        return 1.0
    haystack = f"{record.name} {record.kind} {record.description} {' '.join(str(value) for value in record.metadata.values())}"
    terms = _terms(haystack)
    if not terms:
        return 0.0
    term_set = set(terms)
    score = 0.0
    for term in query_terms:
        if term in term_set:
            score += 3.0
        else:
            score += max((1.0 for candidate in term_set if candidate.startswith(term) or term.startswith(candidate)), default=0.0)
    score += math.log(1 + len(set(query_terms) & term_set))
    if record.name.lower() == " ".join(query_terms):
        score += 5.0
    return score


def _terms(value: str) -> list[str]:
    return [part.lower() for part in re.split(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", value) if part]
