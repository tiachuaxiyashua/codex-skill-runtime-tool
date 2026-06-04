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
    SearchRecord("sleep", "runtime-tool", "Wait for a bounded duration in polling workflows.", {}),
    SearchRecord("task", "runtime-tool", "Run a delegated worker task, optionally in the background.", {}),
    SearchRecord("agent", "runtime-tool", "Run a delegated subagent, optionally in the background.", {}),
    SearchRecord("task_create", "runtime-tool", "Create a task-list item for planning and task tracking.", {}),
    SearchRecord("task_get", "runtime-tool", "Read one task-list item by id.", {}),
    SearchRecord("task_list", "runtime-tool", "List task-list items.", {}),
    SearchRecord("task_output", "runtime-tool", "Read latest or full output from a worker task.", {}),
    SearchRecord("task_update", "runtime-tool", "Update a task-list item status, owner, dependencies, or metadata.", {}),
    SearchRecord("task_stop", "runtime-tool", "Stop a worker task.", {}),
    SearchRecord("send_message", "runtime-tool", "Send a follow-up message to a background worker.", {}),
    SearchRecord("ask_user_question", "runtime-tool", "Pause execution for one focused user question.", {}),
    SearchRecord("todo_write", "runtime-tool", "Persist a todo list for the current session.", {}),
    SearchRecord("skill", "runtime-tool", "Load a model-invocable skill by name for nested invocation.", {}),
    SearchRecord("discover_skills", "runtime-tool", "List visible skills without loading full skill bodies.", {}),
    SearchRecord("tool_search", "runtime-tool", "Search runtime tools, skills, capabilities, and MCP servers.", {}),
    SearchRecord("plan_mode", "runtime-tool", "Enter, exit, or verify a persisted plan mode lifecycle.", {}),
    SearchRecord("config", "runtime-tool", "Inspect runtime settings and plugin enablement state.", {}),
    SearchRecord("project_memory_read", "runtime-tool", "Read runtime-owned project memory sections.", {}),
    SearchRecord("project_memory_write", "runtime-tool", "Write runtime-owned project memory sections.", {}),
    SearchRecord("asset_register", "runtime-tool", "Append an asset record to runtime-owned project memory.", {}),
    SearchRecord("capability_list", "runtime-tool", "List capability manifests from loaded skills and plugins.", {}),
    SearchRecord("notebook_edit", "runtime-tool", "Edit a Jupyter notebook cell through JSON cell operations.", {}),
    SearchRecord("snip", "runtime-tool", "Extract a bounded line range from a text file.", {}),
    SearchRecord("send_user_file", "runtime-tool", "Register a user-provided file in the active session.", {}),
    SearchRecord("review_artifact", "runtime-tool", "Persist review notes for an artifact.", {}),
    SearchRecord("brief", "runtime-tool", "Persist a concise brief record.", {}),
    SearchRecord("remote_trigger", "runtime-tool", "Persist a generic remote trigger request.", {}),
    SearchRecord("structured_output", "runtime-tool", "Persist schema/value structured output.", {}),
    SearchRecord("workflow", "runtime-tool", "Persist and read workflow state.", {}),
    SearchRecord("team_create", "runtime-tool", "Persist a named team record.", {}),
    SearchRecord("team_delete", "runtime-tool", "Delete a named team record.", {}),
    SearchRecord("enter_worktree", "runtime-tool", "Create and record a Git worktree.", {}),
    SearchRecord("exit_worktree", "runtime-tool", "Record or remove a Git worktree.", {}),
    SearchRecord("cron_create", "runtime-tool", "Create a session schedule record and process-local fire queue entry.", {}),
    SearchRecord("cron_list", "runtime-tool", "List session schedule records.", {}),
    SearchRecord("cron_delete", "runtime-tool", "Delete a session schedule record.", {}),
    SearchRecord("monitor", "runtime-tool", "Inspect session, task tree, worker, and job state.", {}),
    SearchRecord("lsp", "runtime-tool", "Run an LSP command in the active workspace.", {}),
    SearchRecord("memory_read", "runtime-tool", "Read agent-scoped memory.", {}),
    SearchRecord("memory_write", "runtime-tool", "Write agent-scoped memory.", {}),
    SearchRecord("bridge", "runtime-tool", "Use the local bridge registry and queue.", {}),
    SearchRecord("voice", "runtime-tool", "Use runtime voice transcript state.", {}),
    SearchRecord("ide", "runtime-tool", "Use IDE selection, diagnostics, or LSP context.", {}),
    SearchRecord("web_fetch", "runtime-tool", "Fetch one URL through the runtime web fetcher.", {}),
    SearchRecord("web_search", "runtime-tool", "Search the web through the configured runtime search path.", {}),
    SearchRecord("web_browser", "runtime-tool", "Use a lightweight stateful browser for open/click/find/current.", {}),
    SearchRecord("mcp_auth", "runtime-tool", "Start or complete OAuth for a configured MCP server.", {}),
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
        records.append(
            SearchRecord(
                f"mcp__{server.name}__authenticate",
                "mcp-auth-tool",
                f"Start OAuth authentication for MCP server {server.name}.",
                {"server": server.name, "aliases": list(server.aliases), "plugin": server.plugin_name or "", "transport": str(server.config.get("type") or "")},
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
