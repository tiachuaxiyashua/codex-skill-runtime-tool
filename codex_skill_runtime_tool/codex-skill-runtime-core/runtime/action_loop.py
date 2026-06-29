from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codex_cli import CodexCLI, CodexRunResult
from .frontmatter import MarkdownDocument
from .jsonutil import parse_json_response
from .microcompact import compact_observations
from .prompts import skill_prompt
from .session import RuntimeSession
from .session_memory import maybe_update_session_memory, session_memory_context
from .tool_executor import ToolExecutor, ToolResult


@dataclass
class ActionLoopResult:
    status: str
    final: str
    codex_runs: list[CodexRunResult] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


class StrictActionLoop:
    def __init__(
        self,
        *,
        codex: CodexCLI,
        session: RuntimeSession,
        project_root: Path,
        schema_path: Path,
        tool_executor: ToolExecutor,
        max_steps: int = 8,
        model: str | None = None,
        reasoning_effort: str | None = None,
        use_output_schema: bool = True,
        allow_plain_text_final: bool = False,
    ) -> None:
        self.codex = codex
        self.session = session
        self.project_root = project_root
        self.schema_path = schema_path
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.use_output_schema = use_output_schema
        self.allow_plain_text_final = allow_plain_text_final

    def run(
        self,
        *,
        command: str,
        arguments: str,
        skill: MarkdownDocument,
        agent: MarkdownDocument,
        context_bundle: str,
        skill_support: str = "",
        assume_yes: bool,
        qa_mode: str,
        dry_run: bool,
        runtime_profile: str = "",
    ) -> ActionLoopResult:
        observations: list[dict[str, Any]] = []
        codex_runs: list[CodexRunResult] = []
        tool_results: list[ToolResult] = []
        schema_failed = False

        for step in range(1, self.max_steps + 1):
            prompt = self._build_prompt(
                command=command,
                arguments=arguments,
                skill=skill,
                agent=agent,
                context_bundle=context_bundle,
                skill_support=skill_support,
                runtime_profile=runtime_profile,
                assume_yes=assume_yes,
                qa_mode=qa_mode,
                observations=observations,
                step=step,
            )
            output_schema = self.schema_path if self.use_output_schema and not schema_failed else None
            active_prompt = prompt if output_schema is not None else _raw_json_prompt(prompt)
            run = self.codex.exec_prompt(
                session=self.session,
                label=f"strict-step-{step}",
                workdir=self.project_root,
                prompt=active_prompt,
                output_schema=output_schema,
                dry_run=dry_run,
                stall_timeout_seconds=_schema_stall_timeout_seconds() if output_schema is not None else None,
                retry_attempts=_schema_retry_attempts() if output_schema is not None else None,
                retry_backoff_seconds=_schema_retry_backoff_seconds() if output_schema is not None else None,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
            )
            if not dry_run and output_schema is not None and run.returncode != 0:
                codex_runs.append(run)
                schema_failed = True
                self.session.event(
                    "codex.schema_fallback",
                    f"Strict step {step} failed with schema; disabling schema for this session and retrying prompt-only JSON mode.",
                    returncode=run.returncode,
                    raw_returncode=run.raw_returncode,
                    terminal_event=run.terminal_event,
                    failure_reason=run.failure_reason,
                    stderr=str(run.stderr_path),
                )
                run = self.codex.exec_prompt(
                    session=self.session,
                    label=f"strict-step-{step}-json-fallback",
                    workdir=self.project_root,
                    prompt=_raw_json_prompt(prompt),
                    output_schema=None,
                    dry_run=False,
                    model=self.model,
                    reasoning_effort=self.reasoning_effort,
                )
            codex_runs.append(run)
            if dry_run:
                return ActionLoopResult("DRY-RUN", "Strict prompt prepared.", codex_runs, tool_results)
            if run.returncode != 0:
                stderr = run.stderr_path.read_text(encoding="utf-8", errors="replace") if run.stderr_path.exists() else ""
                if "502 Bad Gateway" in stderr or "upstream_error" in stderr:
                    return ActionLoopResult(
                        "BLOCKED",
                        f"Codex upstream failed during strict step {step}: {stderr[-2000:]}",
                        codex_runs,
                        tool_results,
                    )
                return ActionLoopResult(
                    "BLOCKED",
                    f"Codex strict step {step} failed with exit {run.returncode}: {stderr[-2000:]}",
                    codex_runs,
                    tool_results,
                )
            if not run.last_message.strip():
                return ActionLoopResult(
                    "BLOCKED",
                    f"Codex strict step {step} returned no final JSON message.",
                    codex_runs,
                    tool_results,
                )

            try:
                response = parse_json_response(run.last_message)
            except (json.JSONDecodeError, ValueError) as exc:
                if self.allow_plain_text_final:
                    final_text = run.last_message.strip()
                    self.session.event(
                        "codex.plain_text_final",
                        f"Accepted plain-text final response for {command} step {step}.",
                        label=run.label,
                        parse_error=str(exc),
                        last_message=str(run.last_message_path),
                    )
                    return ActionLoopResult("FINAL", final_text, codex_runs, tool_results)
                return ActionLoopResult(
                    "BLOCKED",
                    f"Codex strict step {step} returned invalid JSON: {exc}",
                    codex_runs,
                    tool_results,
                )
            self.session.write_json(f"strict-step-{step}/response.json", response)
            status = str(response.get("status"))
            if status == "final":
                return ActionLoopResult("FINAL", str(response.get("final", "")), codex_runs, tool_results)
            if status == "blocked":
                return ActionLoopResult("BLOCKED", str(response.get("final") or response.get("summary") or ""), codex_runs, tool_results)
            if status != "action_required":
                return ActionLoopResult("BLOCKED", f"Unknown status: {status}", codex_runs, tool_results)

            actions = _normalize_actions(response.get("actions"))
            if not actions:
                return ActionLoopResult("BLOCKED", "action_required response contained no actions", codex_runs, tool_results)

            step_observation: dict[str, Any] = {"step": step, "actions": []}
            for result in self._execute_actions(actions):
                tool_results.append(result)
                step_observation["actions"].append(
                    {
                        "tool": result.tool,
                        "status": result.status,
                        "summary": result.summary,
                        "data": result.data,
                    }
                )
                if result.tool == "ask_user_question" and result.status == "BLOCKED":
                    observations.append(step_observation)
                    return ActionLoopResult(
                        "BLOCKED",
                        _blocked_question_message(result),
                        codex_runs,
                        tool_results,
                    )
            observations.append(step_observation)

        return ActionLoopResult("BLOCKED", f"Maximum strict action steps reached: {self.max_steps}", codex_runs, tool_results)

    def _execute_actions(self, actions: list[Any]) -> list[ToolResult]:
        if len(actions) > 1 and all(isinstance(action, dict) and _action_tool_name(action) in {"task", "agent"} for action in actions):
            group_node = self.session.start_node(
                "parallel_group",
                "parallel agents",
                metadata={"count": len(actions)},
            )
            ordered: list[ToolResult | None] = [None] * len(actions)
            try:
                with ThreadPoolExecutor(max_workers=min(len(actions), 4)) as executor:
                    future_map = {
                        executor.submit(self.tool_executor.execute, action): index
                        for index, action in enumerate(actions)
                    }
                    for future in as_completed(future_map):
                        ordered[future_map[future]] = future.result()
                return [result for result in ordered if result is not None]
            finally:
                status = "done" if all(result is not None and result.status == "OK" for result in ordered) else "failed"
                self.session.finish_node(group_node, status=status)

        results: list[ToolResult] = []
        for action in actions:
            if not isinstance(action, dict):
                results.append(ToolResult("unknown", "ERROR", "Action must be an object", {}))
            else:
                results.append(self.tool_executor.execute(action))
        return results

    def _build_prompt(
        self,
        *,
        command: str,
        arguments: str,
        skill: MarkdownDocument,
        agent: MarkdownDocument,
        context_bundle: str,
        skill_support: str = "",
        assume_yes: bool,
        qa_mode: str,
        observations: list[dict[str, Any]],
        step: int,
        runtime_profile: str = "",
    ) -> str:
        try:
            maybe_update_session_memory(
                self.session,
                command=command,
                arguments=arguments,
                note=f"strict action loop step {step}",
                force=step == 1,
            )
        except Exception as exc:
            self.session.event("memory.error", "Failed to update session memory before strict step", error=str(exc))
        base = skill_prompt(
            command=command,
            arguments=arguments,
            skill=skill,
            agent=agent,
            context_bundle=context_bundle,
            skill_support=skill_support,
            project_root=self.project_root,
            assume_yes=assume_yes,
            qa_mode=qa_mode,
            runtime_profile=runtime_profile,
        )
        invoked_skills = self.session.invoked_skills_context()
        compacted_observations, compacted_records = compact_observations(
            observations,
            session_dir=self.session.dir,
        )
        if compacted_records:
            self.session.event(
                "microcompact.applied",
                f"Compacted {len(compacted_records)} old strict observation result(s)",
                records=compacted_records,
            )

        invoked_section = f"\n\n---\n\n{invoked_skills}" if invoked_skills else ""
        live_session_memory = session_memory_context(self.session)
        session_memory_section = f"\n\n---\n\n{live_session_memory}" if live_session_memory else ""

        return f"""{base}{invoked_section}{session_memory_section}

{_strict_action_instructions()}

Current strict step: {step}

## Prior Runtime Observations

```json
{json.dumps(compacted_observations, ensure_ascii=False, indent=2)}
```
"""


def _blocked_question_message(result: ToolResult) -> str:
    question = result.data.get("question", "")
    options = result.data.get("options", [])
    hint = result.data.get("resume_hint", "")
    lines = ["User input required before the workflow can continue.", ""]
    if question:
        lines.append(f"Question: {question}")
    if isinstance(options, list) and options:
        lines.append("Options:")
        for index, option in enumerate(options, start=1):
            lines.append(f"{index}. {option}")
    if hint:
        lines.extend(["", f"Resume hint: {hint}"])
    return "\n".join(lines).strip()


def _action_tool_name(action: dict[str, Any]) -> str:
    return str(action.get("tool", action.get("type", ""))).strip().lower().replace("-", "_")


def _strict_action_instructions() -> str:
    if _lean_context_enabled():
        return """## Strict Runtime Action Mode

Return JSON matching the provided schema. Do not call tools as functions. Do not write prose before the JSON.
You request runtime-owned work by putting tool names inside the JSON `actions` array.

Core tools:
- read_file: parameters `path`, optional `max_chars`
- glob: parameters `pattern`
- grep: parameters `pattern`, optional `path`
- write_file: parameters `path`, `content`
- edit_file: parameters `path`, `old`, `new`
- bash: parameters `command`, optional `timeout`
- task or agent: parameters `agent` or `subagent_type`, `purpose`, `prompt`
- ask_user_question: parameters `question`, optional `options`, optional `default`

Return `status: action_required` with actions when you need runtime work.
Return `status: final` only when the workflow has enough evidence to stop.
Return `status: blocked` when required user input or missing prerequisites prevent progress.

Example write action:
```json
{
  "status": "action_required",
  "summary": "Create the requested file.",
  "actions": [
    {
      "tool": "write_file",
      "parameters": {
        "path": "index.html",
        "content": "<!doctype html>..."
      }
    }
  ],
  "final": ""
}
```"""
    return """## Strict Runtime Action Mode

You must return JSON matching the provided schema. Do not write files directly.
Request runtime-owned actions instead.

Available tools:

- read_file: parameters `path`, optional `max_chars`
- glob: parameters `pattern`
- grep: parameters `pattern`, optional `path`
- write_file: parameters `path`, `content`
- edit_file: parameters `path`, `old`, `new`
- multi_edit: parameters `path`, `edits` where edits is a list of objects with `old` and `new`
- bash: parameters `command`, optional `timeout`
- powershell: parameters `command`, optional `timeout`
- terminal_capture: parameters `command`, optional `shell`, optional `timeout`; persists stdout/stderr evidence
- repl: parameters `language` (`python`), `code`
- sleep: parameters `seconds`; bounded wait for polling
- task or agent: parameters `agent` or `subagent_type`, `purpose`, `prompt`, optional `name`, optional `run_in_background`/`background` or `wait=false`
- task_create/task_get/task_list/task_update/task_output/task_stop: task-list and worker lifecycle tools
- ask_user_question: parameters `question`, optional `options`, optional `default`
- todo_write: parameters `items` list, or `todos` list
- skill/discover_skills/tool_search/capability_list/config: skill and capability discovery/configuration
- plan_mode/workflow/review_artifact/brief/structured_output/monitor: planning, reporting, and state tools
- project_memory_read/project_memory_write/memory_read/memory_write/asset_register: runtime memory and asset tools
- bridge/voice/ide/web_fetch/web_search/web_browser/mcp/mcp_auth/list_mcp_resources/read_mcp_resource/mcp_elicitation: external capability tools

Return `status: action_required` with actions when you need runtime work.
Return `status: final` only when the workflow has enough evidence to stop.
Return `status: blocked` when required user input or missing prerequisites prevent progress."""


def _schema_retry_attempts() -> int:
    return max(1, _env_int("SKILL_RUNTIME_CODEX_SCHEMA_RETRY_ATTEMPTS", default=1))


def _schema_stall_timeout_seconds() -> int | None:
    value = _env_int("SKILL_RUNTIME_CODEX_SCHEMA_STALL_TIMEOUT_SECONDS", default=45)
    return value if value > 0 else None


def _schema_retry_backoff_seconds() -> float:
    value = os.environ.get("SKILL_RUNTIME_CODEX_SCHEMA_RETRY_BACKOFF_SECONDS", "").strip()
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 0.0


def _env_int(name: str, *, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _lean_context_enabled() -> bool:
    value = os.environ.get("SKILL_RUNTIME_CONTEXT_MODE") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_MODE") or ""
    return value.strip().lower() in {"lean", "lite", "minimal", "local"}


def _normalize_actions(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    if "tool" in value or "type" in value:
        return [value]

    actions: list[dict[str, Any]] = []
    for tool, parameters in value.items():
        if not isinstance(tool, str):
            continue
        if isinstance(parameters, dict):
            actions.append({"tool": tool, "parameters": parameters})
        else:
            actions.append({"tool": tool, "parameters": {"value": parameters}})
    return actions


def _raw_json_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\nIMPORTANT: Return only a raw JSON object with keys status, summary, actions, and final. "
        "Do not wrap it in Markdown. Do not include any prose outside the JSON object."
    )
