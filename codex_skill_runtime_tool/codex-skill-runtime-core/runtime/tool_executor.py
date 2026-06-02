from __future__ import annotations

import glob as glob_module
import fnmatch
import json
import re
import shlex
import subprocess
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .bridge import LocalBridge
from .capabilities import discover_capabilities
from .compat import model_invocable
from .hooks import HookDispatcher, PermissionDecision, hook_block_reason, hook_updated_input
from .ide import IDESelection, ide_context, run_lsp_command, write_ide_diagnostics, write_ide_selection
from .large_results import compact_tool_result
from .loaders import SkillRepositoryLoader
from .mcp import MCPBridgeError, MCPServerConfig, call_mcp_tool
from .memory import agent_memory_context, read_project_memory, record_asset, write_agent_memory, write_project_memory
from .prompts import render_markdown_body
from .questions import record_pending_question
from .session import RuntimeSession
from .session_memory import maybe_update_session_memory
from .voice import VoiceRuntime, session_text, voice_context
from .workers import WorkerRegistry


TaskRunner = Callable[[str, str, str], str]


@dataclass
class ToolResult:
    tool: str
    status: str
    summary: str
    data: dict[str, Any]


class ToolExecutionError(RuntimeError):
    pass


class ToolExecutor:
    def __init__(
        self,
        *,
        project_root: Path,
        hooks: HookDispatcher,
        session: RuntimeSession,
        assume_yes: bool,
        task_runner: TaskRunner | None = None,
        allowed_tools: Any = None,
        plugin_root: Path | None = None,
        additional_dirs: list[Path] | None = None,
        invocation_arguments: str = "",
        worker_registry: WorkerRegistry | None = None,
        agent_mcp_servers: list[MCPServerConfig] | None = None,
        agent_name: str = "main-session",
        agent_memory_scope: str | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.hooks = hooks
        self.session = session
        self.assume_yes = assume_yes
        self.task_runner = task_runner
        self.plugin_root = plugin_root.resolve() if plugin_root is not None else None
        self.additional_dirs = additional_dirs or []
        self.invocation_arguments = invocation_arguments
        self.worker_registry = worker_registry
        self.agent_mcp_servers = agent_mcp_servers or []
        self.agent_name = agent_name
        self.agent_memory_scope = agent_memory_scope
        self.preapproved_tools = _parse_allowed_tools(allowed_tools, plugin_root=self.plugin_root)
        self._counter = 0
        self._lock = threading.Lock()

    def execute(self, action: dict[str, Any]) -> ToolResult:
        raw_tool = str(action.get("tool", action.get("type", "")))
        tool = _normalize_tool_name(raw_tool)
        parameters = action.get("parameters")
        if parameters is None:
            parameters = {
                key: value
                for key, value in action.items()
                if key not in {"tool", "type", "reason", "description", "summary"}
            }
        if not isinstance(parameters, dict):
            raise ToolExecutionError("action parameters must be an object")
        if raw_tool.startswith("mcp__") and "tool" not in parameters:
            parameters["tool"] = raw_tool
        parameters = _normalize_action_parameters(tool, parameters)

        handlers = {
            "read_file": self._read_file,
            "glob": self._glob,
            "grep": self._grep,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "multi_edit": self._multi_edit,
            "bash": self._bash,
            "task": self._task,
            "agent": self._task,
            "ask_user_question": self._ask_user_question,
            "todo_write": self._todo_write,
            "skill": self._skill,
            "project_memory_read": self._project_memory_read,
            "project_memory_write": self._project_memory_write,
            "asset_register": self._asset_register,
            "capability_list": self._capability_list,
            "web_fetch": self._web_fetch,
            "web_search": self._web_search,
            "mcp": self._mcp,
            "send_message": self._send_message,
            "task_stop": self._task_stop,
            "memory_read": self._memory_read,
            "memory_write": self._memory_write,
            "bridge": self._bridge,
            "voice": self._voice,
            "ide": self._ide,
        }
        handler = handlers.get(tool)

        tool_node = self.session.start_node(
            "tool",
            tool,
            metadata={"tool": tool, "current_action": str(action.get("reason", "")), "parameters": _compact_parameters(parameters)},
        )
        self.session.event("tool.start", f"{tool}: {action.get('reason', '')}", parameters=parameters)
        if handler is None:
            result = ToolResult(tool=tool, status="ERROR", summary=f"unknown tool: {tool}", data={})
        else:
            parameters = dict(parameters)
            decision = self._permission_decision(tool, parameters)
            if decision.status in {"DENY", "ASK"}:
                status = "ERROR" if decision.status == "DENY" else "BLOCKED"
                result = ToolResult(
                    tool=tool,
                    status=status,
                    summary=decision.reason,
                    data={"permission": decision.__dict__, "preapproved_tools": sorted(self.preapproved_tools)},
                )
            else:
                pre_results = self._fire_pre_tool_hooks(tool, parameters)
                block_reason = hook_block_reason(pre_results, assume_yes=self.assume_yes)
                if block_reason is not None:
                    result = ToolResult(
                        tool=tool,
                        status="BLOCKED",
                        summary=block_reason,
                        data={"hook_results": [asdict(item) for item in pre_results]},
                    )
                else:
                    updated = hook_updated_input(pre_results)
                    if updated:
                        parameters = _apply_hook_updated_input(tool, parameters, updated)
                        decision = self._permission_decision(tool, parameters)
                        if decision.status in {"DENY", "ASK"}:
                            status = "ERROR" if decision.status == "DENY" else "BLOCKED"
                            result = ToolResult(
                                tool=tool,
                                status=status,
                                summary=decision.reason,
                                data={
                                    "permission": decision.__dict__,
                                    "preapproved_tools": sorted(self.preapproved_tools),
                                    "updated_input": updated,
                                },
                            )
                        else:
                            result = self._run_handler_with_post(tool, parameters, handler)
                    else:
                        result = self._run_handler_with_post(tool, parameters, handler)
        tool_id = self._next_tool_id()
        result = compact_tool_result(result, session_dir=self.session.dir, tool_id=tool_id)
        self.session.event("tool.finish", result.summary, result=asdict(result))
        result_path = self.session.write_json(f"tools/{tool_id}-{tool}.json", asdict(result))
        self.session.finish_node(
            tool_node,
            status=_status_for_tool_result(result),
            evidence={"tool_result": str(result_path), "summary": result.summary},
            metadata={"tool_id": tool_id},
        )
        try:
            maybe_update_session_memory(self.session, note=f"{tool} {result.status}: {result.summary}")
        except Exception as exc:
            self.session.event("memory.error", "Failed to update session memory after tool execution", error=str(exc))
        return result

    def _read_file(self, parameters: dict[str, Any]) -> ToolResult:
        path = self._resolve_read_path(str(parameters["path"]))
        max_chars = int(parameters.get("max_chars", 20000))
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n[TRUNCATED]\n"
        self.session.update_read_state(path, text)
        return ToolResult(
            "read_file",
            "OK",
            f"Read {path}",
            {"path": str(path), "content": text, "truncated": truncated},
        )

    def _run_handler_with_post(
        self,
        tool: str,
        parameters: dict[str, Any],
        handler: Callable[[dict[str, Any]], ToolResult],
    ) -> ToolResult:
        try:
            result = handler(parameters)
        except Exception as exc:
            result = ToolResult(tool=tool, status="ERROR", summary=str(exc), data={})
        if result.status == "ERROR":
            failure_results = self._fire_post_tool_failure_hooks(tool, parameters, result)
            block_reason = hook_block_reason(failure_results, assume_yes=self.assume_yes)
            if block_reason is not None:
                return ToolResult(
                    tool=tool,
                    status="BLOCKED",
                    summary=block_reason,
                    data={
                        "tool_result": asdict(result),
                        "hook_results": [asdict(item) for item in failure_results],
                    },
                )
        post_results = self._fire_post_tool_hooks(tool, parameters, result)
        block_reason = hook_block_reason(post_results, assume_yes=self.assume_yes)
        if block_reason is None:
            return result
        return ToolResult(
            tool=tool,
            status="BLOCKED",
            summary=block_reason,
            data={
                "tool_result": asdict(result),
                "hook_results": [asdict(item) for item in post_results],
            },
        )

    def _glob(self, parameters: dict[str, Any]) -> ToolResult:
        pattern = str(parameters["pattern"])
        pattern_path = Path(pattern)
        full_pattern = str(pattern_path if pattern_path.is_absolute() else self.project_root / pattern_path)
        matches = sorted(glob_module.glob(full_pattern, recursive=True))
        rel_matches = [_display_read_path(Path(match), self.project_root) for match in matches[:500]]
        return ToolResult("glob", "OK", f"Found {len(matches)} matches", {"matches": rel_matches, "total": len(matches)})

    def _grep(self, parameters: dict[str, Any]) -> ToolResult:
        pattern = str(parameters["pattern"])
        path_value = str(parameters.get("path", "."))
        root = self._resolve_read_path(path_value)
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        regex = re.compile(pattern)
        matches: list[dict[str, Any]] = []
        for path in files[:2000]:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "path": _display_read_path(path, self.project_root),
                            "line": index,
                            "text": line[:1000],
                        }
                    )
                    if len(matches) >= 500:
                        break
            if len(matches) >= 500:
                break
        return ToolResult("grep", "OK", f"Found {len(matches)} matches", {"matches": matches})

    def _write_file(self, parameters: dict[str, Any]) -> ToolResult:
        path = self._resolve_write_path(str(parameters["path"]))
        content = str(parameters.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        self.session.add_artifact(path, metadata={"tool": "write_file", "bytes": len(content.encode("utf-8"))})
        return ToolResult("write_file", "OK", f"Wrote {path}", {"path": str(path), "bytes": len(content.encode("utf-8"))})

    def _edit_file(self, parameters: dict[str, Any]) -> ToolResult:
        path = self._resolve_write_path(str(parameters["path"]))
        old = str(parameters["old"])
        new = str(parameters["new"])
        text = path.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            raise ToolExecutionError(f"old text not found in {path}")
        updated = text.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8", newline="\n")
        self.session.add_artifact(path, metadata={"tool": "edit_file", "replacements": 1})
        return ToolResult("edit_file", "OK", f"Edited {path}", {"path": str(path), "replacements": 1})

    def _multi_edit(self, parameters: dict[str, Any]) -> ToolResult:
        path = self._resolve_write_path(str(parameters["path"]))
        edits = parameters.get("edits")
        if not isinstance(edits, list):
            raise ToolExecutionError("multi_edit requires an edits list")
        text = path.read_text(encoding="utf-8", errors="replace")
        replacements = 0
        for edit in edits:
            if not isinstance(edit, dict):
                raise ToolExecutionError("each multi_edit item must be an object")
            old = str(edit["old"])
            new = str(edit["new"])
            if old not in text:
                raise ToolExecutionError(f"old text not found in {path}: {old[:80]}")
            text = text.replace(old, new, 1)
            replacements += 1
        path.write_text(text, encoding="utf-8", newline="\n")
        self.session.add_artifact(path, metadata={"tool": "multi_edit", "replacements": replacements})
        return ToolResult("multi_edit", "OK", f"Edited {path}", {"path": str(path), "replacements": replacements})

    def _bash(self, parameters: dict[str, Any]) -> ToolResult:
        command = str(parameters["command"])
        if self.hooks.is_denied_bash(command):
            raise ToolExecutionError(f"bash command denied by .claude/settings.json: {command}")
        completed = subprocess.run(
            command,
            cwd=str(self.project_root),
            shell=True,
            text=True,
            capture_output=True,
            timeout=int(parameters.get("timeout", 120)),
            check=False,
        )
        status = "OK" if completed.returncode == 0 else "ERROR"
        return ToolResult(
            "bash",
            status,
            f"Command exited {completed.returncode}",
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-20000:],
                "stderr": completed.stderr[-20000:],
            },
        )

    def _task(self, parameters: dict[str, Any]) -> ToolResult:
        if self.worker_registry is None and self.task_runner is None:
            raise ToolExecutionError("task runner is not configured")
        agent = str(parameters.get("agent") or parameters.get("subagent_type") or "general-purpose")
        prompt = str(parameters.get("prompt", parameters.get("purpose", "")))
        purpose = str(parameters.get("purpose", "Runtime Task action"))
        name = parameters.get("name")
        if self.worker_registry is not None:
            record = self.worker_registry.spawn(
                agent=agent,
                purpose=purpose,
                prompt=prompt,
                name=str(name) if name else None,
            )
            return ToolResult(
                "task",
                "OK",
                f"Task {agent} completed as {record.id}",
                {
                    "agent": agent,
                    "worker_id": record.id,
                    "name": record.name,
                    "status": record.status,
                    "output": record.latest_output,
                    "send_message_hint": f"Use SendMessage with to='{record.id}' to continue this worker.",
                },
            )
        output = self.task_runner(agent, purpose, prompt) if self.task_runner is not None else ""
        return ToolResult("task", "OK", f"Task {agent} completed", {"agent": agent, "output": output})

    def _send_message(self, parameters: dict[str, Any]) -> ToolResult:
        if self.worker_registry is None:
            raise ToolExecutionError("worker registry is not configured")
        to = str(parameters.get("to") or parameters.get("worker_id") or parameters.get("task_id") or "")
        if not to:
            raise ToolExecutionError("SendMessage requires `to`")
        prompt = str(parameters.get("message") or parameters.get("prompt") or parameters.get("content") or "")
        record = self.worker_registry.send(to=to, prompt=prompt)
        return ToolResult(
            "send_message",
            "OK",
            f"Continued worker {record.id}",
            {"worker_id": record.id, "agent": record.agent, "status": record.status, "output": record.latest_output},
        )

    def _task_stop(self, parameters: dict[str, Any]) -> ToolResult:
        if self.worker_registry is None:
            raise ToolExecutionError("worker registry is not configured")
        to = str(parameters.get("to") or parameters.get("worker_id") or parameters.get("task_id") or "")
        if not to:
            raise ToolExecutionError("TaskStop requires `to` or `task_id`")
        reason = str(parameters.get("reason") or "")
        record = self.worker_registry.stop(to=to, reason=reason)
        return ToolResult("task_stop", "OK", f"Stopped worker {record.id}", {"worker_id": record.id, "status": record.status, "reason": reason})

    def _ask_user_question(self, parameters: dict[str, Any]) -> ToolResult:
        question = str(parameters["question"])
        options = parameters.get("options") or []
        if not isinstance(options, list):
            options = []
        if not self.assume_yes:
            question_node = self.session.start_node(
                "question",
                "user-input",
                status="waiting_user",
                metadata={"question": question, "options": options},
            )
            pending = record_pending_question(
                self.session,
                question=question,
                options=options,
                default=str(parameters.get("default")) if parameters.get("default") is not None else None,
            )
            self.session.update_node(question_node, evidence={"pending_question": str(self.session.path("pending-question.json"))})
            return ToolResult(
                "ask_user_question",
                "BLOCKED",
                "User input required",
                {
                    "question": question,
                    "options": options,
                    "pending_question": pending,
                    "resume_hint": f"answer {self.session.id} <your answer>",
                },
            )
        answer = str(options[0]) if options else str(parameters.get("default", "yes"))
        return ToolResult(
            "ask_user_question",
            "OK",
            f"Assume-yes selected: {answer}",
            {"question": question, "answer": answer, "options": options},
        )

    def _todo_write(self, parameters: dict[str, Any]) -> ToolResult:
        items = parameters.get("items", parameters.get("todos", []))
        if not isinstance(items, list):
            raise ToolExecutionError("todo_write requires items or todos list")
        normalized = []
        for index, item in enumerate(items, start=1):
            if isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append({"id": str(index), "content": str(item), "status": "pending"})
        self.session.write_json("todos/latest.json", {"items": normalized})
        return ToolResult("todo_write", "OK", f"Recorded {len(normalized)} todos", {"items": normalized})

    def _skill(self, parameters: dict[str, Any]) -> ToolResult:
        name = str(parameters.get("name", parameters.get("skill", ""))).strip()
        if not name:
            raise ToolExecutionError("skill action requires name")
        loader = SkillRepositoryLoader(self.project_root, additional_dirs=self.additional_dirs)
        document = loader.load_skill_by_reference(name)
        if not model_invocable(document) and not bool(parameters.get("user_invoked") or parameters.get("allow_disabled")):
            return ToolResult(
                "skill",
                "BLOCKED",
                f"Skill {name} is not model-invocable",
                {"name": name, "path": str(document.path), "metadata": document.metadata},
            )
        skill_arguments = str(
            parameters.get(
                "arguments",
                parameters.get("args", parameters.get("input", parameters.get("prompt", self.invocation_arguments))),
            )
        )
        rendered_body = render_markdown_body(document=document, arguments=skill_arguments, project_root=self.project_root)
        support_files = [
            str(path.relative_to(document.path.parent))
            for path in loader.skill_support_files(document, limit=80)
        ]
        loaded_name = str(document.metadata.get("name") or document.path.parent.name)
        self.session.record_invoked_skill(
            name=loaded_name,
            path=document.path,
            content=rendered_body,
            agent=self.agent_name,
            metadata=document.metadata,
        )
        if str(document.metadata.get("context", "")).strip() == "fork":
            if self.task_runner is None:
                raise ToolExecutionError(f"forked skill {name} requires a task runner")
            agent = str(document.metadata.get("agent") or "general-purpose")
            output = self.task_runner(
                agent,
                f"Forked skill {document.metadata.get('name') or document.path.parent.name}",
                (
                    "Execute this Claude skill in an isolated forked context and return the final result.\n\n"
                    f"Source: {document.path}\n\n"
                    f"Original invocation arguments: {skill_arguments}\n\n"
                    f"Frontmatter: {json.dumps(document.metadata, ensure_ascii=False)}\n\n"
                    f"Skill body:\n{rendered_body}\n\n"
                    f"Supporting files:\n{support_files}"
                ),
            )
            return ToolResult(
                "skill",
                "OK",
                f"Forked skill {document.metadata.get('name') or document.path.parent.name} completed",
                {
                    "name": document.metadata.get("name") or document.path.parent.name,
                    "path": str(document.path),
                    "metadata": document.metadata,
                    "context": "fork",
                    "arguments": skill_arguments,
                    "output": output,
                    "support_files": support_files,
                },
            )
        return ToolResult(
            "skill",
            "OK",
            f"Loaded skill {document.metadata.get('name') or document.path.parent.name}",
            {
                "name": document.metadata.get("name") or document.path.parent.name,
                "path": str(document.path),
                "metadata": document.metadata,
                "body": document.body,
                "rendered_body": rendered_body,
                "arguments": skill_arguments,
                "support_files": support_files,
                "instruction": (
                    "The requested skill has now been loaded into this runtime turn. "
                    "Follow its rendered_body directly. If it needs another skill, request another `skill` action."
                ),
            },
        )

    def _project_memory_read(self, parameters: dict[str, Any]) -> ToolResult:
        section = str(parameters.get("section") or "all")
        content = read_project_memory(self.project_root, section=section)
        return ToolResult(
            "project_memory_read",
            "OK",
            f"Read runtime project memory section {section}",
            {"section": section, "content": content},
        )

    def _project_memory_write(self, parameters: dict[str, Any]) -> ToolResult:
        section = str(parameters.get("section") or parameters.get("name") or "notes")
        content = str(parameters.get("content") or parameters.get("text") or "")
        append = bool(parameters.get("append", True))
        path = write_project_memory(self.project_root, section=section, content=content, append=append)
        return ToolResult(
            "project_memory_write",
            "OK",
            f"Wrote runtime project memory section {section}",
            {"section": section, "path": str(path), "append": append},
        )

    def _asset_register(self, parameters: dict[str, Any]) -> ToolResult:
        asset = parameters.get("asset", parameters)
        if not isinstance(asset, dict):
            raise ToolExecutionError("asset_register requires an object")
        path = record_asset(self.project_root, asset)
        asset_path = asset.get("path") or asset.get("file") or asset.get("asset_path")
        if asset_path:
            self.session.add_artifact(str(asset_path), metadata={"tool": "asset_register", "manifest": str(path), "asset": asset})
        return ToolResult(
            "asset_register",
            "OK",
            "Registered asset in runtime project memory",
            {"path": str(path), "asset": asset},
        )

    def _capability_list(self, parameters: dict[str, Any]) -> ToolResult:
        capabilities = [item.to_dict() for item in discover_capabilities(self.project_root, additional_dirs=self.additional_dirs)]
        namespace = str(parameters.get("namespace") or "").strip()
        if namespace:
            capabilities = [item for item in capabilities if str(item.get("namespace") or "") == namespace]
        return ToolResult(
            "capability_list",
            "OK",
            f"Listed {len(capabilities)} runtime capabilities",
            {"capabilities": capabilities},
        )

    def _web_fetch(self, parameters: dict[str, Any]) -> ToolResult:
        url = str(parameters["url"])
        with urllib.request.urlopen(url, timeout=int(parameters.get("timeout", 20))) as response:
            content_type = response.headers.get("content-type", "")
            data = response.read(int(parameters.get("max_bytes", 200000)))
        text = data.decode("utf-8", errors="replace")
        return ToolResult(
            "web_fetch",
            "OK",
            f"Fetched {url}",
            {"url": url, "content_type": content_type, "content": text[:100000]},
        )

    def _web_search(self, parameters: dict[str, Any]) -> ToolResult:
        query = str(parameters["query"])
        encoded = urllib.parse.urlencode({"q": query})
        url = f"https://duckduckgo.com/html/?{encoded}"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "codex-skill-runtime/1.0"}),
            timeout=int(parameters.get("timeout", 20)),
        ) as response:
            data = response.read(int(parameters.get("max_bytes", 200000)))
        text = data.decode("utf-8", errors="replace")
        return ToolResult("web_search", "OK", f"Searched web for {query}", {"query": query, "html": text[:100000]})

    def _mcp(self, parameters: dict[str, Any]) -> ToolResult:
        tool = str(parameters.get("tool", ""))
        arguments = parameters.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ToolExecutionError("mcp arguments must be an object")
        try:
            result = call_mcp_tool(
                project_root=self.project_root,
                tool=tool,
                arguments=arguments,
                timeout=int(parameters.get("timeout", 45)),
                extra_servers=self.agent_mcp_servers,
                additional_dirs=self.additional_dirs,
            )
        except MCPBridgeError as exc:
            return ToolResult("mcp", "BLOCKED", str(exc), {"tool": tool, "arguments": arguments})
        failure = _mcp_failure(result)
        if failure is not None:
            status, message = failure
            return ToolResult(
                "mcp",
                status,
                message,
                result,
            )
        return ToolResult("mcp", "OK", f"Called MCP tool {tool}", result)

    def _memory_read(self, parameters: dict[str, Any]) -> ToolResult:
        agent = str(parameters.get("agent") or self.agent_name)
        scope = str(parameters.get("scope") or self.agent_memory_scope or "local")
        text = agent_memory_context(self.project_root, agent_name=agent, scope=scope)
        return ToolResult("memory_read", "OK", f"Read {scope} memory for {agent}", {"agent": agent, "scope": scope, "content": text})

    def _memory_write(self, parameters: dict[str, Any]) -> ToolResult:
        agent = str(parameters.get("agent") or self.agent_name)
        scope = str(parameters.get("scope") or self.agent_memory_scope or "local")
        content = str(parameters.get("content") or parameters.get("text") or "")
        append = bool(parameters.get("append", True))
        path = write_agent_memory(self.project_root, agent_name=agent, scope=scope, content=content, append=append)
        return ToolResult("memory_write", "OK", f"Wrote {scope} memory for {agent}", {"agent": agent, "scope": scope, "path": str(path)})

    def _bridge(self, parameters: dict[str, Any]) -> ToolResult:
        operation = _operation(parameters)
        bridge = LocalBridge(self.project_root)
        if operation in {"register", "register_environment"}:
            metadata = parameters.get("metadata")
            environment = bridge.register_environment(
                bridge_id=str(parameters.get("bridge_id") or "") or None,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            self.session.event(
                "bridge.register",
                f"Registered bridge environment {environment.environment_id}",
                environment_id=environment.environment_id,
                bridge_id=environment.bridge_id,
            )
            return ToolResult(
                "bridge",
                "OK",
                f"Registered bridge environment {environment.environment_id}",
                {
                    "environment_id": environment.environment_id,
                    "bridge_id": environment.bridge_id,
                    "root": str(environment.root),
                },
            )
        if operation in {"enqueue", "enqueue_work"}:
            environment_id = _required(parameters, "environment_id")
            data = parameters.get("data")
            work_id = bridge.enqueue_work(
                environment_id,
                kind=str(parameters.get("kind") or parameters.get("type") or "message"),
                data=data if isinstance(data, dict) else {"value": data},
            )
            self.session.event("bridge.enqueue", f"Queued bridge work {work_id}", environment_id=environment_id, work_id=work_id)
            return ToolResult("bridge", "OK", f"Queued bridge work {work_id}", {"environment_id": environment_id, "work_id": work_id})
        if operation in {"poll", "poll_work"}:
            environment_id = _required(parameters, "environment_id")
            work = bridge.poll_work(environment_id)
            summary = "No queued bridge work" if work is None else f"Delivered bridge work {work.get('id')}"
            return ToolResult("bridge", "OK", summary, {"environment_id": environment_id, "work": work})
        if operation in {"ack", "ack_work"}:
            environment_id = _required(parameters, "environment_id")
            work_id = str(parameters.get("work_id") or parameters.get("id") or "")
            if not work_id:
                raise ToolExecutionError("bridge ack requires `work_id`")
            state = str(parameters.get("state") or "acknowledged")
            bridge.ack_work(environment_id, work_id, state=state)
            self.session.event("bridge.ack", f"Bridge work {work_id} marked {state}", environment_id=environment_id, work_id=work_id, state=state)
            return ToolResult("bridge", "OK", f"Bridge work {work_id} marked {state}", {"environment_id": environment_id, "work_id": work_id, "state": state})
        if operation == "heartbeat":
            environment_id = _required(parameters, "environment_id")
            work_id = str(parameters.get("work_id") or parameters.get("id") or "")
            if not work_id:
                raise ToolExecutionError("bridge heartbeat requires `work_id`")
            bridge.heartbeat(environment_id, work_id)
            self.session.event("bridge.heartbeat", f"Bridge heartbeat for {work_id}", environment_id=environment_id, work_id=work_id)
            return ToolResult("bridge", "OK", f"Heartbeat recorded for {work_id}", {"environment_id": environment_id, "work_id": work_id})
        if operation in {"session_event", "write_session_event"}:
            session_id = str(parameters.get("session_id") or self.session.id)
            event = parameters.get("event")
            if not isinstance(event, dict):
                event = {
                    "type": str(parameters.get("type") or "bridge.event"),
                    "message": str(parameters.get("message") or ""),
                    "data": parameters.get("data") if isinstance(parameters.get("data"), dict) else {},
                }
            path = bridge.write_session_event(session_id, event)
            self.session.event("bridge.session_event", f"Bridge session event written for {session_id}", bridge_session_id=session_id, path=str(path))
            return ToolResult("bridge", "OK", f"Wrote bridge session event for {session_id}", {"session_id": session_id, "path": str(path)})
        if operation in {"archive", "archive_session"}:
            session_id = str(parameters.get("session_id") or self.session.id)
            bridge.archive_session(session_id)
            self.session.event("bridge.archive", f"Archived bridge session {session_id}", bridge_session_id=session_id)
            return ToolResult("bridge", "OK", f"Archived bridge session {session_id}", {"session_id": session_id})
        if operation in {"reconnect", "reconnect_session"}:
            environment_id = _required(parameters, "environment_id")
            session_id = str(parameters.get("session_id") or self.session.id)
            pointer = bridge.reconnect_session(environment_id, session_id)
            self.session.event("bridge.reconnect", f"Bridge reconnect pointer updated for {session_id}", environment_id=environment_id, bridge_session_id=session_id)
            return ToolResult("bridge", "OK", f"Bridge reconnect pointer updated for {session_id}", {"environment_id": environment_id, "session_id": session_id, "path": str(pointer)})
        raise ToolExecutionError(f"unknown bridge operation: {operation}")

    def _voice(self, parameters: dict[str, Any]) -> ToolResult:
        operation = _operation(parameters)
        voice = VoiceRuntime(self.project_root)
        if operation == "start":
            session = voice.start()
            self.session.event("voice.start", f"Started voice session {session.session_id}", voice_session_id=session.session_id)
            return ToolResult("voice", "OK", f"Started voice session {session.session_id}", _voice_session_data(session))
        if operation in {"append", "append_transcript"}:
            session_id = _required(parameters, "session_id")
            text = str(parameters.get("text") or parameters.get("content") or "")
            session = voice.append_transcript(session_id, text)
            self.session.event("voice.append", f"Appended voice transcript for {session_id}", voice_session_id=session_id, chars=len(text))
            return ToolResult("voice", "OK", f"Appended transcript for {session_id}", _voice_session_data(session))
        if operation == "finalize":
            session_id = _required(parameters, "session_id")
            session = voice.finalize(session_id)
            text = session_text(session)
            self.session.event("voice.finalize", f"Finalized voice session {session_id}", voice_session_id=session_id, chars=len(text))
            return ToolResult("voice", "OK", f"Finalized voice session {session_id}", {**_voice_session_data(session), "transcript": text})
        if operation == "load":
            session_id = _required(parameters, "session_id")
            session = voice.load(session_id)
            return ToolResult("voice", "OK", f"Loaded voice session {session_id}", {**_voice_session_data(session), "transcript": session_text(session)})
        if operation in {"latest", "context"}:
            context = voice_context(self.project_root)
            return ToolResult("voice", "OK", "Loaded latest voice transcript context", {"context": context})
        raise ToolExecutionError(f"unknown voice operation: {operation}")

    def _ide(self, parameters: dict[str, Any]) -> ToolResult:
        operation = _operation(parameters)
        if operation == "selection":
            file_path = str(parameters.get("file_path") or parameters.get("path") or "")
            if not file_path:
                raise ToolExecutionError("ide selection requires `file_path` or `path`")
            selection = IDESelection(
                file_path=file_path,
                text=str(parameters.get("text") or ""),
                start_line=_optional_int(parameters.get("start_line")),
                end_line=_optional_int(parameters.get("end_line")),
            )
            path = write_ide_selection(self.project_root, selection)
            self.session.event("ide.selection", f"IDE selection updated for {file_path}", path=str(path), file_path=file_path)
            return ToolResult("ide", "OK", f"IDE selection updated for {file_path}", {"path": str(path), "selection": asdict(selection)})
        if operation == "diagnostics":
            diagnostics = parameters.get("diagnostics")
            if not isinstance(diagnostics, list):
                diagnostics = []
            path = write_ide_diagnostics(self.project_root, [item for item in diagnostics if isinstance(item, dict)])
            self.session.event("ide.diagnostics", "IDE diagnostics updated", path=str(path), count=len(diagnostics))
            return ToolResult("ide", "OK", f"IDE diagnostics updated ({len(diagnostics)})", {"path": str(path), "diagnostics": diagnostics})
        if operation in {"lsp_command", "lsp"}:
            raw_command = parameters.get("command")
            if isinstance(raw_command, list):
                command = [str(item) for item in raw_command]
            else:
                command = shlex.split(str(raw_command or ""))
            result = run_lsp_command(command, project_root=self.project_root, timeout=int(parameters.get("timeout", 30)))
            self.session.event("ide.lsp_command", "IDE/LSP command executed", command=command, status=result.get("status"))
            return ToolResult("ide", str(result.get("status") or "OK"), "IDE/LSP command executed", {"command": command, "result": result})
        if operation in {"load", "context"}:
            context = ide_context(self.project_root)
            return ToolResult("ide", "OK", "Loaded IDE context", {"context": context})
        raise ToolExecutionError(f"unknown ide operation: {operation}")

    def _resolve_read_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve()
        if not path.exists():
            raise ToolExecutionError(f"path does not exist: {path}")
        return path

    def _resolve_write_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.project_root / path
        path = path.resolve()
        try:
            rel = path.relative_to(self.project_root)
        except ValueError as exc:
            raise ToolExecutionError(f"write path outside project root: {path}") from exc
        if rel.parts and rel.parts[0] == ".claude":
            raise ToolExecutionError(f"writes to .claude are blocked: {path}")
        return path

    def _permission_decision(self, tool: str, parameters: dict[str, Any]):
        if self.preapproved_tools and not _allowed_by_frontmatter(tool, parameters, self.preapproved_tools):
            claude_tool = _CLAUDE_TOOL_NAMES.get(tool, tool)
            if self.assume_yes:
                return PermissionDecision("ALLOW", f"assume-yes approved tool outside allowed-tools: {claude_tool}", None)
            return PermissionDecision("ASK", f"tool not preapproved by allowed-tools: {claude_tool}", None)
        return self.hooks.permission_decision(
            _CLAUDE_TOOL_NAMES.get(tool, tool),
            _claude_permission_parameters(tool, parameters),
            assume_yes=self.assume_yes,
        )

    def _fire_pre_tool_hooks(self, tool: str, parameters: dict[str, Any]):
        tool_name = _CLAUDE_TOOL_NAMES.get(tool, tool)
        return self.hooks.fire(
            "PreToolUse",
            matcher_value=tool_name,
            payload={
                "tool": tool_name,
                "tool_name": tool_name,
                "tool_input": _claude_tool_input(tool, parameters),
            },
            session=self.session,
        )

    def _fire_post_tool_hooks(self, tool: str, parameters: dict[str, Any], result: ToolResult):
        tool_name = _CLAUDE_TOOL_NAMES.get(tool, tool)
        return self.hooks.fire(
            "PostToolUse",
            matcher_value=tool_name,
            payload={
                "tool": tool_name,
                "tool_name": tool_name,
                "tool_input": _claude_tool_input(tool, parameters),
                "tool_result": result.summary,
                "tool_response": asdict(result),
            },
            session=self.session,
        )

    def _fire_post_tool_failure_hooks(self, tool: str, parameters: dict[str, Any], result: ToolResult):
        tool_name = _CLAUDE_TOOL_NAMES.get(tool, tool)
        return self.hooks.fire(
            "PostToolUseFailure",
            matcher_value=tool_name,
            payload={
                "tool": tool_name,
                "tool_name": tool_name,
                "tool_input": _claude_tool_input(tool, parameters),
                "error": result.summary,
                "tool_response": asdict(result),
            },
            session=self.session,
        )

    def _next_tool_id(self) -> str:
        self.session.path("tools")
        with self._lock:
            self._counter += 1
            return f"{self._counter:03d}"

_TOOL_ALIASES = {
    "read": "read_file",
    "readfile": "read_file",
    "read_file": "read_file",
    "Read": "read_file",
    "glob": "glob",
    "Glob": "glob",
    "grep": "grep",
    "Grep": "grep",
    "write": "write_file",
    "writefile": "write_file",
    "write_file": "write_file",
    "Write": "write_file",
    "edit": "edit_file",
    "editfile": "edit_file",
    "edit_file": "edit_file",
    "Edit": "edit_file",
    "multiedit": "multi_edit",
    "multi_edit": "multi_edit",
    "MultiEdit": "multi_edit",
    "bash": "bash",
    "Bash": "bash",
    "task": "task",
    "Task": "task",
    "agent": "agent",
    "Agent": "agent",
    "askuserquestion": "ask_user_question",
    "ask_user_question": "ask_user_question",
    "AskUserQuestion": "ask_user_question",
    "todowrite": "todo_write",
    "todo_write": "todo_write",
    "TodoWrite": "todo_write",
    "skill": "skill",
    "Skill": "skill",
    "project_memory_read": "project_memory_read",
    "ProjectMemoryRead": "project_memory_read",
    "project_memory_write": "project_memory_write",
    "ProjectMemoryWrite": "project_memory_write",
    "asset_register": "asset_register",
    "AssetRegister": "asset_register",
    "capability_list": "capability_list",
    "CapabilityList": "capability_list",
    "capabilities": "capability_list",
    "webfetch": "web_fetch",
    "web_fetch": "web_fetch",
    "WebFetch": "web_fetch",
    "websearch": "web_search",
    "web_search": "web_search",
    "WebSearch": "web_search",
    "mcp": "mcp",
    "MCP": "mcp",
    "sendmessage": "send_message",
    "send_message": "send_message",
    "SendMessage": "send_message",
    "taskstop": "task_stop",
    "task_stop": "task_stop",
    "TaskStop": "task_stop",
    "memory_read": "memory_read",
    "MemoryRead": "memory_read",
    "memory_write": "memory_write",
    "MemoryWrite": "memory_write",
    "bridge": "bridge",
    "Bridge": "bridge",
    "voice": "voice",
    "Voice": "voice",
    "ide": "ide",
    "IDE": "ide",
}

_CLAUDE_TOOL_NAMES = {
    "read_file": "Read",
    "glob": "Glob",
    "grep": "Grep",
    "write_file": "Write",
    "edit_file": "Edit",
    "multi_edit": "MultiEdit",
    "bash": "Bash",
    "task": "Task",
    "agent": "Agent",
    "ask_user_question": "AskUserQuestion",
    "todo_write": "TodoWrite",
    "skill": "Skill",
    "project_memory_read": "Read",
    "project_memory_write": "Write",
    "asset_register": "Write",
    "capability_list": "Read",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
    "mcp": "mcp",
    "send_message": "SendMessage",
    "task_stop": "TaskStop",
    "memory_read": "Read",
    "memory_write": "Write",
    "bridge": "Bridge",
    "voice": "Voice",
    "ide": "IDE",
}


def _normalize_tool_name(value: str) -> str:
    compact = value.strip()
    if compact.startswith("mcp__"):
        return "mcp"
    return _TOOL_ALIASES.get(compact, _TOOL_ALIASES.get(compact.replace("-", "_"), compact))


def _parse_allowed_tools(value: Any, *, plugin_root: Path | None = None) -> set[str]:
    if value is None or value == "":
        return set()
    raw_tokens: list[str] = []
    if isinstance(value, list):
        for item in value:
            raw_tokens.extend(_split_allowed_tool_string(_expand_plugin_root(str(item), plugin_root)))
    else:
        raw_tokens.extend(_split_allowed_tool_string(_expand_plugin_root(str(value), plugin_root)))
    return {token.strip() for token in raw_tokens if token.strip()}


def _split_allowed_tool_string(value: str) -> list[str]:
    if "," in value:
        return [part.strip() for part in value.split(",")]
    return [part.strip() for part in value.split()]


def _allowed_by_frontmatter(tool: str, parameters: dict[str, Any], allowed: set[str]) -> bool:
    claude_tool = _CLAUDE_TOOL_NAMES.get(tool, tool)
    candidates = {
        tool,
        claude_tool,
        claude_tool.lower(),
        tool.replace("_", "-"),
    }
    mcp_tool = str(parameters.get("tool") or "")
    if tool == "mcp" and mcp_tool:
        candidates.add(mcp_tool)
    command = str(parameters.get("command") or "")
    for raw in allowed:
        token = raw.strip()
        if not token:
            continue
        if token in candidates:
            return True
        if token.endswith("*") and any(fnmatch.fnmatch(candidate, token) for candidate in candidates):
            return True
        if token.startswith("Bash(") and token.endswith(")") and tool == "bash":
            pattern = token.removeprefix("Bash(").removesuffix(")")
            if fnmatch.fnmatch(command, pattern):
                return True
        if token.startswith("mcp__") and tool == "mcp" and mcp_tool and fnmatch.fnmatch(mcp_tool, token):
            return True
    return False


def _expand_plugin_root(value: str, plugin_root: Path | None) -> str:
    if plugin_root is None:
        return value
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))


def _claude_permission_parameters(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if tool == "read_file":
        return {"path": str(parameters.get("path", ""))}
    if tool == "write_file":
        return {"path": str(parameters.get("path", ""))}
    if tool in {"edit_file", "multi_edit"}:
        return {"path": str(parameters.get("path", ""))}
    if tool == "bash":
        return {"command": str(parameters.get("command", ""))}
    if tool == "web_fetch":
        return {"url": str(parameters.get("url", ""))}
    if tool == "mcp":
        return {"tool": str(parameters.get("tool", ""))}
    if tool in {"bridge", "voice", "ide"}:
        return {"operation": str(parameters.get("operation") or parameters.get("action") or "")}
    return parameters


def _claude_tool_input(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if tool == "read_file":
        return {"file_path": str(parameters.get("path", ""))}
    if tool == "write_file":
        return {"file_path": str(parameters.get("path", "")), "content": str(parameters.get("content", ""))}
    if tool == "edit_file":
        return {
            "file_path": str(parameters.get("path", "")),
            "old_string": str(parameters.get("old", "")),
            "new_string": str(parameters.get("new", "")),
        }
    if tool == "multi_edit":
        return {"file_path": str(parameters.get("path", "")), "edits": parameters.get("edits", [])}
    if tool == "bash":
        return {"command": str(parameters.get("command", ""))}
    if tool == "web_fetch":
        return {"url": str(parameters.get("url", ""))}
    if tool == "web_search":
        return {"query": str(parameters.get("query", ""))}
    if tool == "mcp":
        return {"tool": str(parameters.get("tool", "")), "arguments": parameters.get("arguments", {})}
    return dict(parameters)


def _apply_hook_updated_input(tool: str, parameters: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    mapped = dict(parameters)
    key_map = {
        "file_path": "path",
        "old_string": "old",
        "new_string": "new",
    }
    for key, value in updated.items():
        if key == "tool_input" and isinstance(value, dict):
            mapped = _apply_hook_updated_input(tool, mapped, value)
            continue
        mapped[key_map.get(key, key)] = value
    return mapped


def _operation(parameters: dict[str, Any]) -> str:
    return str(parameters.get("operation") or parameters.get("action") or "load").strip().lower().replace("-", "_")


def _required(parameters: dict[str, Any], key: str) -> str:
    value = str(parameters.get(key) or "").strip()
    if not value:
        raise ToolExecutionError(f"missing required parameter `{key}`")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _voice_session_data(session: Any) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "state": session.state,
        "chunks": session.chunks,
        "started_at": session.started_at,
        "finalized_at": session.finalized_at,
    }


def _normalize_action_parameters(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parameters)
    nested = normalized.get("arguments")
    if not isinstance(nested, dict):
        return normalized
    if tool == "mcp":
        if "tool" not in normalized and nested.get("tool"):
            normalized["tool"] = nested["tool"]
            inner = nested.get("arguments")
            if isinstance(inner, dict):
                normalized["arguments"] = inner
            else:
                normalized["arguments"] = {
                    key: value for key, value in nested.items() if key not in {"tool", "timeout"}
                }
            if "timeout" in nested and "timeout" not in normalized:
                normalized["timeout"] = nested["timeout"]
        return normalized
    outer_keys = {key for key in normalized if key not in {"arguments", "timeout"}}
    if not outer_keys:
        return {**nested, **{key: value for key, value in normalized.items() if key != "arguments"}}
    return normalized


def _display_read_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root))
    except ValueError:
        return str(resolved)


def _status_for_tool_result(result: ToolResult) -> str:
    if result.status == "OK":
        return "done"
    if result.status == "BLOCKED":
        return "blocked"
    return "failed"


def _compact_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in parameters.items():
        if isinstance(value, str):
            compacted[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            compacted[key] = value
        elif isinstance(value, list):
            compacted[key] = value[:20]
        elif isinstance(value, dict):
            compacted[key] = {str(k): str(v)[:200] for k, v in list(value.items())[:20]}
        else:
            compacted[key] = str(value)[:500]
    return compacted


def _mcp_failure(result: dict[str, Any]) -> tuple[str, str] | None:
    response = result.get("result")
    if not isinstance(response, dict):
        return None
    if isinstance(response.get("error"), dict):
        error = response["error"]
        return ("ERROR", str(error.get("message") or "MCP JSON-RPC request failed"))
    payload = response.get("result", response)
    if not isinstance(payload, dict):
        return None
    if payload.get("isError") is True:
        return ("ERROR", "MCP tool returned an error result")
    business_status = str(payload.get("status") or "").lower()
    structured = payload.get("structuredContent")
    if not business_status and isinstance(structured, dict):
        business_status = str(structured.get("status") or "").lower()
    if business_status in {"unsupported", "blocked"}:
        message = _mcp_failure_message(payload, structured, business_status)
        return ("BLOCKED", message)
    if business_status in {"error", "failed", "fail"}:
        message = _mcp_failure_message(payload, structured, business_status)
        return ("ERROR", message)
    return None


def _mcp_failure_message(payload: dict[str, Any], structured: Any, default: str) -> str:
    if isinstance(structured, dict) and structured.get("message"):
        return str(structured["message"])
    if payload.get("message"):
        return str(payload["message"])
    return default
