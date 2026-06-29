from __future__ import annotations

import json
import fnmatch
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .action_loop import StrictActionLoop
from .bridge import bridge_context
from .capabilities import capability_context, discover_capabilities
from .compat import agent_memory_scope, agent_skill_references, invocation_profile
from .compact_state import compact_state_context, record_compact_state
from .codex_cli import CodexCLI, CodexRunResult
from .frontmatter import MarkdownDocument
from .gates import GateResult, evaluate_qa_report
from .hooks import HookDispatcher, hook_block_reason
from .ide import ide_context
from .jsonutil import parse_json_response
from .loaders import SkillRepositoryLoader
from .memdir import MemoryHeader, run_memory_consolidation_job, run_memory_extraction_job, relevant_memory_context
from .mcp import mcp_instructions_context, servers_from_agent_mcp_specs
from .memory import agent_memory_context, project_memory_context, record_session_summary, runtime_memory_context
from .plan_state import plan_mode_context
from .prompts import agent_task_prompt, qa_prompt, skill_prompt
from .qa import resolve_qa_agent
from .questions import answer_pending_question, pending_question_context, record_pending_question
from .session import RuntimeSession
from .session_memory import maybe_update_session_memory, session_memory_context
from .state_machines import build_workflow_plan
from .system_prompt import (
    SystemPromptOptions,
    build_compat_system_prompt,
    resolve_append_system_prompt_value,
    resolve_system_prompt_value,
)
from .tasks import parse_task_requests
from .token_budget import ContextSection, apply_context_budget, budget_context_for_prompt, context_window_tokens
from .tool_executor import ToolExecutor
from .transcript import find_session_dir, replay_context
from .voice import voice_context
from .workers import WorkerRegistry


@dataclass
class RuntimeResult:
    session: RuntimeSession
    primary: CodexRunResult | None
    tasks: list[CodexRunResult]
    gates: list[GateResult]
    exit_code: int




QUESTION_REQUEST_PATTERNS: tuple[str, ...] = (
    "请回答",
    "请回复",
    "请选择",
    "请选择一个",
    "请确认",
    "需要你",
    "需要您",
    "告诉我",
    "请告诉我",
    "先确认",
    "先回答",
    "先补充",
    "请补充",
    "请提供",
    "请说明",
    "请输入",
    "等待你",
    "等待您",
)

QUESTION_WORD_PATTERNS: tuple[str, ...] = (
    "是否",
    "能否",
    "可否",
    "还是",
    "哪个",
    "哪一个",
    "哪些",
    "什么",
    "如何",
)


def _assistant_question_from_text(text: str) -> dict[str, object] | None:
    value = str(text or "").strip()
    if not value:
        return None
    explicit = _explicit_user_decision_block(value)
    if explicit:
        return explicit
    return None


def _explicit_user_decision_block(text: str) -> dict[str, object] | None:
    value = str(text or "").strip()
    match = re.search(r"(?is)USER_DECISION_REQUIRED\s*:?\s*(?P<body>.+)", value)
    if not match:
        return None
    body = match.group("body").strip()
    if not body:
        return None
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", value) if part.strip()]
    body_paragraphs = [part.strip() for part in re.split(r"\n{2,}", body) if part.strip()]
    candidates = body_paragraphs or paragraphs or [body]
    for block in reversed(candidates):
        if _block_looks_like_question(block):
            question, options = _normalize_question_block(block)
            if question:
                return {"question": question, "options": options}
    sentences = [part.strip() for part in re.split(r"(?<=[？?。！!])\s*", body) if part.strip()]
    for block in reversed(sentences):
        if _block_looks_like_question(block):
            question, options = _normalize_question_block(block)
            if question:
                return {"question": question, "options": options}
    question, options = _normalize_question_block(body)
    return {"question": question, "options": options} if question else None


def _block_looks_like_question(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if re.search(r"[？?]\s*$", value):
        return True
    if re.search(r"[？?]", value):
        return True
    lowered = value.lower()
    if any(hint in value for hint in QUESTION_REQUEST_PATTERNS):
        return True
    if any(hint in lowered for hint in ("please answer", "please choose", "please select", "please provide", "please confirm", "tell me", "choose one", "pick one", "select one")):
        return True
    addressed_to_user = bool(re.search(r"(你|您|your|you)\b?", value, flags=re.IGNORECASE))
    return addressed_to_user and (any(hint in value for hint in QUESTION_WORD_PATTERNS) or any(hint in lowered for hint in ("which", "what", "how", "whether")))


def _normalize_question_block(text: str) -> tuple[str, list[str]]:
    value = str(text or "").strip()
    if not value:
        return "", []
    lines = [line.rstrip() for line in value.splitlines()]
    option_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^(?:[-*•]|\d+[.)]|[A-Z][.)])\s+", stripped):
            option_lines.append(re.sub(r"^(?:[-*•]|\d+[.)]|[A-Z][.)])\s+", "", stripped).strip())
    if not option_lines:
        quoted = []
        for match in re.finditer(r"[`'“‘]([^`'”’]{1,48})[`'”’]", value):
            item = match.group(1).strip()
            if item and item not in quoted:
                quoted.append(item)
        option_lines = quoted[:6]
    question = value
    if len(lines) > 1:
        first = lines[0].strip()
        if _block_looks_like_question(first):
            question = first
        else:
            question = "\n".join(line for line in lines if line.strip())
    if len(question) > 1600:
        question = question[:1600].rstrip() + "..."
    return question, option_lines[:6]


def _session_answerable_question_text(text: str) -> str:
    question = _assistant_question_from_text(text)
    if not question:
        return ""
    return str(question.get("question") or "").strip()


class CodexSkillRuntime:
    def __init__(
        self,
        *,
        project_root: Path,
        codex: CodexCLI,
        dry_run: bool = False,
        assume_yes: bool = False,
        qa_mode: str = "auto",
        additional_dirs: list[Path] | None = None,
        output_style: str | None = None,
        system_prompt: str | None = None,
        append_system_prompt: str | None = None,
        strict_schema: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.codex = codex
        self.dry_run = dry_run
        self.assume_yes = assume_yes
        self.qa_mode = qa_mode
        self.additional_dirs = additional_dirs or []
        self.output_style = output_style
        self.strict_schema = strict_schema
        self.custom_system_prompt = resolve_system_prompt_value(system_prompt, project_root=self.project_root)
        self.append_system_prompt = resolve_append_system_prompt_value(append_system_prompt, project_root=self.project_root)
        self.loader = SkillRepositoryLoader(self.project_root, additional_dirs=self.additional_dirs)
        self._side_query_counter = 0
        self.hooks = HookDispatcher(
            self.loader.settings_candidates(),
            self.project_root,
            prompt_runner=self._run_prompt_hook,
        )

    def _record_waiting_question(
        self,
        session: RuntimeSession,
        *,
        node_id: str,
        source_label: str,
        message: str,
        parent_message: str,
        evidence: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        parsed = _assistant_question_from_text(message)
        if not parsed:
            return False
        question = str(parsed.get("question") or "").strip()
        if not question:
            return False
        options = parsed.get("options") if isinstance(parsed.get("options"), list) else []
        session.finish_node(node_id, status="done", evidence=evidence, metadata=metadata)
        question_node = session.start_node(
            "question",
            "assistant-question",
            parent_id=node_id,
            status="waiting_user",
            metadata={"question": question, "options": options, "source": source_label},
        )
        pending = record_pending_question(session, question=question, options=options)
        session.update_node(question_node, evidence={"pending_question": str(session.path("pending-question.json"))})
        session.set_status("waiting_user")
        return True

    def inspect(self) -> dict[str, object]:
        self.loader.assert_valid()
        return {
            "project_root": str(self.project_root),
            "target_workspace": str(self.project_root),
            "skill_repositories": [str(path.resolve()) for path in self.additional_dirs],
            "skills": self.loader.list_skills(),
            "skill_listings": [item.__dict__ | {"path": str(item.path)} for item in self.loader.skill_listings()],
            "agents": self.loader.list_agents(),
            "plugins": self.loader.plugin_statuses(),
            "capabilities": [item.to_dict() for item in discover_capabilities(self.project_root, additional_dirs=self.additional_dirs)],
            "settings": str(self.loader.primary_settings_path()),
            "context_files": [str(path) for path in self.loader.optional_context_files()],
        }

    def chat_turn(self, message: str) -> RuntimeResult:
        session = self._new_session("chat")
        created_at = datetime.now().isoformat(timespec="seconds")
        session.set_metadata(user_prompt=message, arguments=message, invocation="chat", chat=True, created_at=created_at)
        session.event("session.start", "Starting chat turn", user_prompt=message)
        session.set_status("running")
        chat_node = session.start_node(
            "chat",
            "chat-turn",
            display_name="Chat turn",
            metadata={"user_prompt": message[:1000]},
        )
        context = self._context_bundle(session=session, hook_results=[])
        registry = self.loader.skill_registry_context(max_chars=12000)
        prompt = _chat_turn_prompt(message=message, context=context, skill_registry=registry)
        run = self.codex.exec_prompt(
            session=session,
            label="chat-turn",
            workdir=self.project_root,
            prompt=prompt,
            dry_run=self.dry_run,
        )
        status = "passed" if run.returncode == 0 else "failed"
        waiting = False
        final = run.last_message if run.returncode == 0 else f"Codex chat turn failed with exit {run.returncode}: {run.failure_reason}"
        if run.returncode == 0:
            waiting = self._record_waiting_question(
                session,
                node_id=chat_node,
                source_label="chat",
                message=final,
                parent_message=message,
                evidence={"last_message": str(run.last_message_path), "stdout": str(run.stdout_path), "stderr": str(run.stderr_path)},
                metadata={"returncode": run.returncode},
            )
        if not waiting:
            session.finish_node(
                chat_node,
                status=status,
                evidence={"last_message": str(run.last_message_path), "stdout": str(run.stdout_path), "stderr": str(run.stderr_path)},
                metadata={"returncode": run.returncode},
            )
            session.set_status("done" if run.returncode == 0 else "failed")
            summary_status = "PASS" if run.returncode == 0 else "FAIL"
        else:
            summary_status = "WAITING_USER"
        summary = {
            "session_id": session.id,
            "command": "chat",
            "arguments": message,
            "status": summary_status,
            "notes": final[:4000],
            "created_at": created_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "gates": [{"name": "CHAT", "status": summary_status, "reason": "Real Codex chat turn."}],
        }
        session.write_json("summary.json", summary)
        return RuntimeResult(session=session, primary=run, tasks=[], gates=[], exit_code=0 if waiting else run.returncode)

    def run_chat_loop(self, message: str, max_steps: int = 8, *, resume_from: str | None = None) -> RuntimeResult:
        session = self._new_session("chat")
        created_at = datetime.now().isoformat(timespec="seconds")
        session.set_metadata(
            user_prompt=message,
            arguments=message,
            invocation="chat",
            chat=True,
            strict_tools=True,
            conversational_loop=True,
            resumed_from=resume_from or "",
            created_at=created_at,
        )
        session.event("session.start", "Starting conversational chat loop", user_prompt=message, strict_tools=True)
        session.set_status("running")
        chat_node = session.start_node(
            "chat",
            "chat-turn",
            display_name="Chat turn",
            metadata={"user_prompt": message[:1000], "strict_tools": True},
        )
        self.hooks.fire(
            "UserPromptSubmit",
            matcher_value=message,
            payload={"user_prompt": message, "chat": True, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        session_hooks = self.hooks.fire(
            "SessionStart",
            matcher_value=message,
            payload={"command": "chat", "arguments": message, "chat": True, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )

        tasks: list[CodexRunResult] = []
        task_lock = threading.Lock()

        def task_runner(task_agent: str, purpose: str, prompt: str, index: int = 1) -> str:
            result = self._run_agent_task(
                session=session,
                parent_command="chat",
                agent_name=task_agent,
                purpose=purpose,
                inputs=prompt,
                parent_result="Conversational runtime Task action",
                index=index,
                hooks=self.hooks,
                strict_tools=True,
                max_steps=max(2, max_steps // 2),
            )
            with task_lock:
                tasks.append(result)
            return result.last_message

        worker_registry = WorkerRegistry(task_runner, session_dir=session.dir)
        chat_skill = MarkdownDocument(
            path=self.project_root / "CHAT.md",
            metadata={
                "name": "chat",
                "description": "Natural-language runtime conversation entry point",
            },
            body=_chat_loop_skill_body(message=message),
            raw="",
        )
        chat_agent = self._synthetic_agent(
            "main-session",
            (
                "You are the main conversational runtime agent. Decide whether to answer directly, "
                "invoke a visible skill, call tools, delegate to agents, or ask the user through AskUserQuestion. "
                "Do not invent completed work; produce evidence through runtime tools."
            ),
            path=chat_skill.path,
        )
        profile = invocation_profile(
            skill=chat_skill,
            agent=chat_agent,
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            explicit_output_style=self.output_style,
        )
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=self.hooks,
            session=session,
            assume_yes=self.assume_yes,
            task_runner=task_runner,
            worker_registry=worker_registry,
            allowed_tools=chat_skill.metadata.get("allowed-tools"),
            additional_dirs=self.additional_dirs,
            invocation_arguments=message,
            agent_mcp_servers=self._agent_mcp_servers(chat_agent),
            agent_name="main-session",
            agent_memory_scope=agent_memory_scope(chat_agent),
        )
        loop = StrictActionLoop(
            codex=self.codex,
            session=session,
            project_root=self.project_root,
            schema_path=_runtime_schema_path(),
            tool_executor=executor,
            max_steps=max_steps,
            model=profile.model,
            reasoning_effort=profile.effort,
            use_output_schema=self.strict_schema,
            allow_plain_text_final=True,
        )
        agent_node = session.start_node(
            "agent",
            "main-session",
            parent_id=chat_node,
            metadata={"purpose": "Conversational chat loop", "strict_tools": True},
        )
        loop_result = loop.run(
            command="chat",
            arguments=message,
            skill=chat_skill,
            agent=chat_agent,
            context_bundle=self._context_bundle(session=session, hook_results=session_hooks)
            + _resume_context_block(self.project_root, resume_from),
            skill_support="No static chat support files. Use `skill` to load matching skills.",
            runtime_profile=self._runtime_profile(profile, skill=chat_skill, agent=chat_agent),
            assume_yes=self.assume_yes,
            qa_mode=self.qa_mode,
            dry_run=self.dry_run,
        )
        session.finish_node(agent_node, status="done" if loop_result.status in {"FINAL", "DRY-RUN"} else "blocked")
        session.write_json(
            "strict-result.json",
            {
                "status": loop_result.status,
                "final": loop_result.final,
                "tool_results": [
                    {"tool": result.tool, "status": result.status, "summary": result.summary, "data": result.data}
                    for result in loop_result.tool_results
                ],
            },
        )

        gates: list[GateResult] = []
        if loop_result.status == "BLOCKED":
            gates.append(GateResult("CHAT-LOOP", "BLOCKED", loop_result.final))
        elif loop_result.status == "DRY-RUN":
            gates.append(GateResult("CHAT-LOOP", "DRY-RUN", "Conversational action prompt was prepared but not executed."))
        else:
            gates.append(GateResult("CHAT-LOOP", "PASS", "Conversational action loop reached final status."))
        session.start_node(
            "gate",
            "CHAT-LOOP",
            parent_id=chat_node,
            status=_node_status_for_gate(gates[-1]),
            metadata={"reason": gates[-1].reason},
        )

        stop_results = self.hooks.fire(
            "Stop",
            payload={"command": "chat", "session": session.id, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        stop_block = hook_block_reason(stop_results, assume_yes=self.assume_yes)
        if stop_block:
            gates.append(GateResult("STOP-HOOK", "BLOCKED", stop_block))
        self.hooks.fire(
            "SessionEnd",
            payload={"command": "chat", "session": session.id, "strict_tools": True, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        session.event("session.stop", "Finished conversational chat loop")

        primary = loop_result.codex_runs[-1] if loop_result.codex_runs else None
        exit_code = primary.returncode if primary is not None else 0
        if loop_result.status == "BLOCKED":
            exit_code = max(exit_code, 2)
        for task in tasks:
            exit_code = max(exit_code, task.returncode)
        for gate in gates:
            if gate.status in {"FAIL", "BLOCKED"}:
                exit_code = max(exit_code, 2)
        self._record_memory(
            session,
            command="chat",
            arguments=message,
            status="PASS" if exit_code == 0 else ("WAITING_USER" if _pending_question_exists(session) else "FAIL"),
            notes=loop_result.final[:4000],
            gates=gates,
        )
        session.finish_node(chat_node, status="done" if exit_code == 0 else ("blocked" if _pending_question_exists(session) else "failed"))
        if _pending_question_exists(session):
            session.set_status("waiting_user")
            exit_code = 0
        else:
            session.set_status("done" if exit_code == 0 else "failed")
        summary_status = "WAITING_USER" if _pending_question_exists(session) else ("PASS" if exit_code == 0 else "FAIL")
        session.write_json(
            "summary.json",
            {
                "session_id": session.id,
                "command": "chat",
                "arguments": message,
                "status": summary_status,
                "notes": loop_result.final[:4000],
                "created_at": created_at,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "gates": [{"name": gate.name, "status": gate.status, "reason": gate.reason} for gate in gates],
            },
        )
        return RuntimeResult(session=session, primary=primary, tasks=tasks, gates=gates, exit_code=exit_code)

    def _run_prompt_hook(
        self,
        prompt: str,
        payload: dict[str, object],
        session: RuntimeSession | None,
        plugin_root: Path | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        if session is None:
            return subprocess.CompletedProcess(["codex-prompt-hook"], 0, '{"continue": true}', "")
        hook_prompt = (
            "# Claude Code Prompt Hook Compatibility Runner\n\n"
            "You are evaluating a Claude Code prompt-based hook inside the Codex compatibility runtime.\n"
            "Return only one JSON object. Valid keys include `continue`, `systemMessage`, `decision`, "
            "`permissionDecision`, and `hookSpecificOutput` with `permissionDecision` or `updatedInput`.\n\n"
            "## Hook Prompt\n\n"
            f"{prompt}\n\n"
            "## Hook Input JSON\n\n"
            "```json\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "```\n"
        )
        result = self.codex.exec_prompt(
            session=session,
            label=f"prompt-hook-{payload.get('hook_event_name', 'event')}",
            workdir=plugin_root or self.project_root,
            prompt=hook_prompt,
            dry_run=self.dry_run,
            timeout_seconds=timeout,
        )
        stderr = result.stderr_path.read_text(encoding="utf-8", errors="replace") if result.stderr_path.exists() else ""
        return subprocess.CompletedProcess(["codex-prompt-hook"], result.returncode, result.last_message, stderr)

    def run_skill(self, command: str, arguments: str) -> RuntimeResult:
        clean_command = command[1:] if command.startswith("/") else command
        session = self._new_session(clean_command)
        skill = self.loader.load_skill(clean_command)
        agent_name, agent = self._load_routed_agent(clean_command, skill)
        hooks = self._hooks_for(skill, agent)
        profile = invocation_profile(
            skill=skill,
            agent=agent,
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            explicit_output_style=self.output_style,
        )
        session.event("session.start", f"Starting /{clean_command}", arguments=arguments)
        session.set_status("running")
        skill_node = session.start_node(
            "skill",
            clean_command,
            display_name=f"/{clean_command}",
            metadata={"command": clean_command, "arguments": arguments, "agent": agent_name},
        )
        session.write_json("workflow-plan.json", build_workflow_plan(clean_command, arguments, self.qa_mode).to_dict())
        hooks.fire(
            "UserPromptSubmit",
            matcher_value=f"/{clean_command}",
            payload={"user_prompt": f"/{clean_command} {arguments}".strip()},
            session=session,
            dry_run=self.dry_run,
        )
        session_hooks = hooks.fire(
            "SessionStart",
            matcher_value=f"{clean_command} {arguments}",
            payload={"command": clean_command, "arguments": arguments},
            session=session,
            dry_run=self.dry_run,
        )

        context = self._context_bundle(session=session, hook_results=session_hooks)

        prompt = skill_prompt(
            command=clean_command,
            arguments=arguments,
            skill=skill,
            agent=agent,
            context_bundle=context,
            skill_support=self.loader.describe_skill_support(skill),
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            qa_mode=self.qa_mode,
            runtime_profile=self._runtime_profile(profile, skill=skill, agent=agent),
        )
        primary_node = session.start_node(
            "agent",
            agent_name,
            parent_id=skill_node,
            metadata={"purpose": f"Execute /{clean_command}", "skill": clean_command},
        )
        primary = self.codex.exec_prompt(
            session=session,
            label=f"skill-{clean_command}",
            workdir=self.project_root,
            prompt=prompt,
            dry_run=self.dry_run,
            model=profile.model,
            reasoning_effort=profile.effort,
        )
        session.finish_node(
            primary_node,
            status="done" if primary.returncode == 0 else "failed",
            evidence={
                "prompt": str(primary.prompt_path),
                "stdout": str(primary.stdout_path),
                "stderr": str(primary.stderr_path),
                "last_message": str(primary.last_message_path),
            },
        )

        tasks: list[CodexRunResult] = []
        gates: list[GateResult] = []
        parent_result = primary.last_message

        for index, task in enumerate(parse_task_requests(parent_result), start=1):
            tasks.append(
                self._run_agent_task(
                    session=session,
                    parent_command=clean_command,
                    agent_name=task.agent,
                    purpose=task.purpose,
                    inputs=task.inputs,
                    parent_result=parent_result,
                    index=index,
                    hooks=hooks,
                )
            )

        if self._should_run_required_qa(clean_command, arguments):
            qa_result = self._run_required_qa(
                session=session,
                target_path=self._infer_target_path(arguments, parent_result),
                parent_result=parent_result,
                skill=skill,
                agent=agent,
            )
            tasks.append(qa_result)
            if self.dry_run:
                gates.append(GateResult("QA", "DRY-RUN", "QA prompt was prepared but not executed."))
            else:
                gates.append(evaluate_qa_report(qa_result.last_message))
            session.start_node(
                "gate",
                "QA",
                parent_id=skill_node,
                status=_node_status_for_gate(gates[-1]),
                metadata={"reason": gates[-1].reason},
            )

        stop_results = hooks.fire(
            "Stop",
            payload={"command": clean_command, "session": session.id},
            session=session,
            dry_run=self.dry_run,
        )
        stop_block = hook_block_reason(stop_results, assume_yes=self.assume_yes)
        if stop_block:
            gates.append(GateResult("STOP-HOOK", "BLOCKED", stop_block))
        hooks.fire(
            "SessionEnd",
            payload={"command": clean_command, "session": session.id, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        session.event("session.stop", f"Finished /{clean_command}")

        exit_code = primary.returncode
        for task in tasks:
            exit_code = max(exit_code, task.returncode)
        for gate in gates:
            if gate.status in {"FAIL", "BLOCKED"}:
                exit_code = max(exit_code, 2)
        self._record_memory(
            session,
            command=clean_command,
            arguments=arguments,
            status="PASS" if exit_code == 0 else "FAIL",
            notes=(primary.last_message[:4000] if primary else ""),
            gates=gates,
        )
        session.finish_node(skill_node, status="done" if exit_code == 0 else "failed")
        session.set_status("done" if exit_code == 0 else "failed")
        return RuntimeResult(session=session, primary=primary, tasks=tasks, gates=gates, exit_code=exit_code)

    def run_strict_skill(self, command: str, arguments: str, max_steps: int = 8) -> RuntimeResult:
        clean_command = command[1:] if command.startswith("/") else command
        session = self._new_session(f"strict-{clean_command}")
        skill = self.loader.load_skill(clean_command)
        agent_name, agent = self._load_routed_agent(clean_command, skill)
        hooks = self._hooks_for(skill, agent)
        profile = invocation_profile(
            skill=skill,
            agent=agent,
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            explicit_output_style=self.output_style,
        )
        session.event("session.start", f"Starting strict /{clean_command}", arguments=arguments)
        session.set_status("running")
        skill_node = session.start_node(
            "skill",
            clean_command,
            display_name=f"/{clean_command}",
            metadata={"command": clean_command, "arguments": arguments, "agent": agent_name, "strict_tools": True},
        )
        session.write_json("workflow-plan.json", build_workflow_plan(clean_command, arguments, self.qa_mode).to_dict())
        hooks.fire(
            "UserPromptSubmit",
            matcher_value=f"/{clean_command}",
            payload={"user_prompt": f"/{clean_command} {arguments}".strip(), "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        session_hooks = hooks.fire(
            "SessionStart",
            matcher_value=f"{clean_command} {arguments}",
            payload={"command": clean_command, "arguments": arguments, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )

        tasks: list[CodexRunResult] = []
        task_lock = threading.Lock()

        def task_runner(task_agent: str, purpose: str, prompt: str, index: int) -> str:
            result = self._run_agent_task(
                session=session,
                parent_command=clean_command,
                agent_name=task_agent,
                purpose=purpose,
                inputs=prompt,
                parent_result="Strict runtime Task action",
                index=index,
                hooks=hooks,
                strict_tools=True,
                max_steps=max(2, max_steps // 2),
            )
            with task_lock:
                tasks.append(result)
            return result.last_message

        worker_registry = WorkerRegistry(task_runner, session_dir=session.dir)

        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=hooks,
            session=session,
            assume_yes=self.assume_yes,
            task_runner=task_runner,
            worker_registry=worker_registry,
            allowed_tools=skill.metadata.get("allowed-tools"),
            plugin_root=self.loader.plugin_root_for(skill.path),
            additional_dirs=self.additional_dirs,
            invocation_arguments=arguments,
            agent_mcp_servers=self._agent_mcp_servers(agent),
            agent_name=agent_name,
            agent_memory_scope=agent_memory_scope(agent),
        )
        loop = StrictActionLoop(
            codex=self.codex,
            session=session,
            project_root=self.project_root,
            schema_path=_runtime_schema_path(),
            tool_executor=executor,
            max_steps=max_steps,
            model=profile.model,
            reasoning_effort=profile.effort,
            use_output_schema=self.strict_schema,
        )
        primary_node = session.start_node(
            "agent",
            agent_name,
            parent_id=skill_node,
            metadata={"purpose": f"Strict execute /{clean_command}", "skill": clean_command},
        )
        loop_result = loop.run(
            command=clean_command,
            arguments=arguments,
            skill=skill,
            agent=agent,
            context_bundle=self._context_bundle(session=session, hook_results=session_hooks),
            skill_support=self.loader.describe_skill_support(skill),
            runtime_profile=self._runtime_profile(profile, skill=skill, agent=agent),
            assume_yes=self.assume_yes,
            qa_mode=self.qa_mode,
            dry_run=self.dry_run,
        )
        session.finish_node(primary_node, status="done" if loop_result.status in {"FINAL", "DRY-RUN"} else "blocked")
        session.write_json(
            "strict-result.json",
            {
                "status": loop_result.status,
                "final": loop_result.final,
                "tool_results": [
                    {"tool": result.tool, "status": result.status, "summary": result.summary, "data": result.data}
                    for result in loop_result.tool_results
                ],
            },
        )

        gates: list[GateResult] = []
        if loop_result.status == "BLOCKED":
            gates.append(GateResult("STRICT", "BLOCKED", loop_result.final))
        elif loop_result.status == "DRY-RUN":
            gates.append(GateResult("STRICT", "DRY-RUN", "Strict action prompt was prepared but not executed."))
        else:
            gates.append(GateResult("STRICT", "PASS", "Strict action loop reached final status."))
        session.start_node(
            "gate",
            "STRICT",
            parent_id=skill_node,
            status=_node_status_for_gate(gates[-1]),
            metadata={"reason": gates[-1].reason},
        )

        if loop_result.status not in {"BLOCKED", "DRY-RUN"} and self._should_run_required_qa(clean_command, arguments):
            qa_result = self._run_required_qa(
                session=session,
                target_path=self._infer_target_path(arguments, loop_result.final),
                parent_result=loop_result.final,
                skill=skill,
                agent=agent,
            )
            tasks.append(qa_result)
            gates.append(evaluate_qa_report(qa_result.last_message))
            session.start_node(
                "gate",
                "QA",
                parent_id=skill_node,
                status=_node_status_for_gate(gates[-1]),
                metadata={"reason": gates[-1].reason},
            )

        stop_results = hooks.fire(
            "Stop",
            payload={"command": clean_command, "session": session.id, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        stop_block = hook_block_reason(stop_results, assume_yes=self.assume_yes)
        if stop_block:
            gates.append(GateResult("STOP-HOOK", "BLOCKED", stop_block))
        hooks.fire(
            "SessionEnd",
            payload={"command": clean_command, "session": session.id, "strict_tools": True, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        session.event("session.stop", f"Finished strict /{clean_command}")

        primary = loop_result.codex_runs[-1] if loop_result.codex_runs else None
        exit_code = primary.returncode if primary is not None else 0
        for task in tasks:
            exit_code = max(exit_code, task.returncode)
        for gate in gates:
            if gate.status in {"FAIL", "BLOCKED"}:
                exit_code = max(exit_code, 2)
        self._record_memory(
            session,
            command=f"strict-{clean_command}",
            arguments=arguments,
            status="PASS" if exit_code == 0 else "FAIL",
            notes=loop_result.final[:4000],
            gates=gates,
        )
        session.finish_node(skill_node, status="done" if exit_code == 0 else "failed")
        session.set_status("done" if exit_code == 0 else "failed")
        return RuntimeResult(session=session, primary=primary, tasks=tasks, gates=gates, exit_code=exit_code)

    def run_agent(self, agent_name: str, prompt_text: str) -> RuntimeResult:
        session = self._new_session(f"agent-{agent_name}")
        agent = self.loader.load_agent(agent_name)
        hooks = self._hooks_for(agent=agent)
        profile = invocation_profile(
            agent=agent,
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            explicit_output_style=self.output_style,
        )
        session.event("session.start", f"Starting agent {agent_name}", arguments=prompt_text)
        session.set_status("running")
        agent_node = session.start_node(
            "agent",
            agent_name,
            metadata={"purpose": "Direct agent invocation", "prompt": prompt_text[:1000]},
        )
        context = self._context_bundle(session=session)
        hooks.fire(
            "UserPromptSubmit",
            payload={"user_prompt": prompt_text, "agent": agent_name},
            session=session,
            dry_run=self.dry_run,
        )
        prompt = agent_task_prompt(
            parent_command="agent",
            task_agent=agent,
            purpose="Direct agent invocation",
            inputs=prompt_text,
            parent_result="",
            context_bundle=context,
            project_root=self.project_root,
            runtime_profile=self._runtime_profile(profile, agent=agent),
            preloaded_skills=self._preloaded_skills(agent, arguments=prompt_text),
            agent_memory=self._agent_memory_context(agent),
        )
        hooks.fire(
            "SubagentStart",
            payload={"agent_type": agent_name, "agent": agent_name, "purpose": "Direct agent invocation"},
            session=session,
            dry_run=self.dry_run,
        )
        result = self.codex.exec_prompt(
            session=session,
            label=f"agent-{agent_name}",
            workdir=self.project_root,
            prompt=prompt,
            dry_run=self.dry_run,
            model=profile.model,
            reasoning_effort=profile.effort,
        )
        stop_results = hooks.fire(
            "SubagentStop",
            payload={"agent_type": agent_name, "agent": agent_name, "returncode": result.returncode},
            session=session,
            dry_run=self.dry_run,
        )
        gates: list[GateResult] = []
        stop_block = hook_block_reason(stop_results, assume_yes=self.assume_yes)
        exit_code = result.returncode
        if stop_block:
            gates.append(GateResult("SUBAGENT-HOOK", "BLOCKED", stop_block))
            exit_code = max(exit_code, 2)
        hooks.fire(
            "SessionEnd",
            payload={"agent": agent_name, "session": session.id, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        self._record_memory(
            session,
            command=f"agent-{agent_name}",
            arguments=prompt_text,
            status="PASS" if exit_code == 0 else "FAIL",
            notes=result.last_message[:4000],
            gates=gates,
        )
        session.finish_node(
            agent_node,
            status="done" if exit_code == 0 else "failed",
            evidence={
                "prompt": str(result.prompt_path),
                "stdout": str(result.stdout_path),
                "stderr": str(result.stderr_path),
                "last_message": str(result.last_message_path),
            },
        )
        session.event("session.stop", f"Finished agent {agent_name}")
        session.set_status("done" if exit_code == 0 else "failed")
        return RuntimeResult(session=session, primary=result, tasks=[], gates=gates, exit_code=exit_code)

    def resume_session(self, session_or_path: str, prompt_text: str = "") -> RuntimeResult:
        session = self._new_session("resume")
        context = replay_context(self.project_root, session_or_path)
        question_context = pending_question_context(self.project_root, session_or_path)
        runtime_context = self._context_bundle(session=session)
        session.event("session.start", "Starting transcript resume", source=session_or_path)
        session.set_status("running")
        resume_node = session.start_node("skill", "resume", metadata={"source": session_or_path, "prompt": prompt_text[:1000]})
        prompt = (
            "# Runtime Transcript Resume\n\n"
            "Continue from the reconstructed prior runtime transcript. Do not assume files are unchanged; verify live files before editing.\n\n"
            f"{context}\n\n"
            f"{question_context}\n\n"
            "## Current Runtime Context\n\n"
            f"{runtime_context}\n\n"
            "## New User Instruction\n\n"
            f"{prompt_text or 'Continue the previous workflow and report the next useful action.'}\n"
        )
        result = self.codex.exec_prompt(
            session=session,
            label="resume",
            workdir=self.project_root,
            prompt=prompt,
            dry_run=self.dry_run,
        )
        self.hooks.fire(
            "SessionEnd",
            payload={"command": "resume", "session": session.id, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        session.event("session.stop", "Finished transcript resume")
        waiting = False
        if result.returncode == 0:
            waiting = self._record_waiting_question(
                session,
                node_id=resume_node,
                source_label="resume",
                message=result.last_message,
                parent_message=prompt_text or session_or_path,
                evidence={"last_message": str(result.last_message_path), "stdout": str(result.stdout_path), "stderr": str(result.stderr_path)},
                metadata={"returncode": result.returncode},
            )
        if not waiting:
            self._record_memory(
                session,
                command="resume",
                arguments=session_or_path,
                status="PASS" if result.returncode == 0 else "FAIL",
                notes=result.last_message[:4000],
                gates=[],
            )
        if not waiting:
            session.finish_node(resume_node, status="done" if result.returncode == 0 else "failed")
            session.set_status("done" if result.returncode == 0 else "failed")
        else:
            maybe_update_session_memory(
                session,
                command="resume",
                arguments=session_or_path,
                note=result.last_message[:4000],
                status="WAITING_USER",
                force=True,
            )
        return RuntimeResult(session=session, primary=result, tasks=[], gates=[], exit_code=0 if waiting else result.returncode)

    def answer_question(self, session_or_path: str, answer: str, *, max_steps: int = 8) -> RuntimeResult:
        answered = answer_pending_question(self.project_root, session_or_path, answer)
        prompt = (
            "The user answered the pending runtime question. Continue the prior workflow from the point where it paused.\n\n"
            f"Question: {answered.get('question', '')}\n"
            f"Answer: {answer}\n"
        )
        if self._session_was_conversational(session_or_path):
            return self.run_chat_loop(prompt, max_steps=max_steps, resume_from=session_or_path)
        return self.resume_session(session_or_path, prompt)

    def _session_was_conversational(self, session_or_path: str) -> bool:
        try:
            session_dir = find_session_dir(self.project_root, session_or_path)
            state = json.loads((session_dir / "session-state.json").read_text(encoding="utf-8", errors="replace"))
        except (FileNotFoundError, OSError, ValueError):
            return False
        metadata = state.get("metadata") if isinstance(state, dict) else {}
        return isinstance(metadata, dict) and bool(metadata.get("conversational_loop") or metadata.get("chat"))

    def run_strict_smoke(self, read_path: str = "README.md", max_steps: int = 3) -> RuntimeResult:
        session = self._new_session("strict-smoke")
        session.event("session.start", "Starting strict smoke", read_path=read_path)
        session.set_status("running")
        skill_node = session.start_node(
            "skill",
            "strict-smoke",
            display_name="/strict-smoke",
            metadata={"read_path": read_path, "strict_tools": True},
        )
        self.hooks.fire(
            "UserPromptSubmit",
            payload={"user_prompt": f"/strict-smoke {read_path}", "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        self.hooks.fire(
            "SessionStart",
            payload={"command": "strict-smoke", "read_path": read_path, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )

        skill = MarkdownDocument(
            path=self.project_root / "codex-skill-runtime-core" / "STRICT_SMOKE.md",
            metadata={"name": "strict-smoke", "allowed-tools": ["Read"]},
            body=(
                f"This is a runtime smoke test. Step 1: request exactly one read_file action for `{read_path}`. "
                "After the observation is returned, respond with status final and summarize the file in one sentence. "
                "Do not request any other files."
            ),
            raw="",
        )
        agent = MarkdownDocument(
            path=skill.path,
            metadata={"name": "strict-smoke-agent"},
            body="You verify that the strict runtime action loop can execute one read action and reach final status.",
            raw="",
        )
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=self.hooks,
            session=session,
            assume_yes=True,
            allowed_tools=["Read"],
            additional_dirs=self.additional_dirs,
        )
        loop = StrictActionLoop(
            codex=self.codex,
            session=session,
            project_root=self.project_root,
            schema_path=_runtime_schema_path(),
            tool_executor=executor,
            max_steps=max_steps,
            use_output_schema=self.strict_schema,
        )
        agent_node = session.start_node(
            "agent",
            "strict-smoke-agent",
            parent_id=skill_node,
            metadata={"purpose": "Strict runtime smoke test"},
        )
        loop_result = loop.run(
            command="strict-smoke",
            arguments=read_path,
            skill=skill,
            agent=agent,
            context_bundle="",
            assume_yes=True,
            qa_mode="off",
            dry_run=self.dry_run,
        )
        session.finish_node(agent_node, status="done" if loop_result.status in {"FINAL", "DRY-RUN"} else "blocked")
        session.write_json(
            "strict-result.json",
            {
                "status": loop_result.status,
                "final": loop_result.final,
                "tool_results": [
                    {"tool": result.tool, "status": result.status, "summary": result.summary, "data": result.data}
                    for result in loop_result.tool_results
                ],
            },
        )
        stop_results = self.hooks.fire(
            "Stop",
            payload={"command": "strict-smoke", "session": session.id, "strict_tools": True},
            session=session,
            dry_run=self.dry_run,
        )
        stop_block = hook_block_reason(stop_results, assume_yes=True)
        if stop_block:
            session.event("hook.blocked", "Stop hook blocked strict smoke", reason=stop_block)
        self.hooks.fire(
            "SessionEnd",
            payload={"command": "strict-smoke", "session": session.id, "strict_tools": True, "reason": "complete"},
            session=session,
            dry_run=self.dry_run,
        )
        session.event("session.stop", "Finished strict smoke")

        primary = loop_result.codex_runs[-1] if loop_result.codex_runs else None
        status = "PASS" if loop_result.status == "FINAL" else loop_result.status
        gates = [GateResult("STRICT-SMOKE", status, loop_result.final)]
        exit_code = primary.returncode if primary is not None else 0
        if status not in {"PASS", "DRY-RUN"}:
            exit_code = max(exit_code, 2)
        if stop_block:
            gates.append(GateResult("STOP-HOOK", "BLOCKED", stop_block))
            exit_code = max(exit_code, 2)
        self._record_memory(
            session,
            command="strict-smoke",
            arguments=read_path,
            status="PASS" if exit_code == 0 else "FAIL",
            notes=loop_result.final[:4000],
            gates=gates,
        )
        session.finish_node(skill_node, status="done" if exit_code == 0 else "failed")
        session.set_status("done" if exit_code == 0 else "failed")
        return RuntimeResult(session=session, primary=primary, tasks=[], gates=gates, exit_code=exit_code)

    def _run_agent_task(
        self,
        *,
        session: RuntimeSession,
        parent_command: str,
        agent_name: str,
        purpose: str,
        inputs: str,
        parent_result: str,
        index: int,
        hooks: HookDispatcher | None = None,
        strict_tools: bool = False,
        max_steps: int = 4,
    ) -> CodexRunResult:
        try:
            agent = self.loader.load_agent(agent_name)
        except FileNotFoundError:
            agent = self._synthetic_agent(
                agent_name,
                f"You are the `{agent_name}` subagent requested by the runtime. "
                "No matching agent file was found, so execute the delegated prompt directly.",
            )
        active_hooks = hooks or self._hooks_for(agent=agent)
        profile = invocation_profile(
            agent=agent,
            project_root=self.project_root,
            assume_yes=self.assume_yes,
            explicit_output_style=self.output_style,
        )
        active_hooks.fire(
            "SubagentStart",
            payload={"agent_type": agent_name, "agent": agent_name, "purpose": purpose},
            session=session,
            dry_run=self.dry_run,
        )
        agent_node = session.start_node(
            "agent",
            agent_name,
            metadata={"purpose": purpose, "parent_command": parent_command, "task_index": index},
        )
        if strict_tools:
            task_skill = MarkdownDocument(
                path=agent.path,
                metadata={
                    "name": f"agent-task-{agent_name}",
                    "allowed-tools": agent.metadata.get("tools", ""),
                    "agent": agent_name,
                    "model": agent.metadata.get("model", ""),
                    "effort": agent.metadata.get("effort", ""),
                },
                body=(
                    f"Purpose: {purpose}\n\n"
                    f"Inputs:\n{inputs}\n\n"
                    f"Parent result/context:\n{parent_result}"
                ),
                raw="",
            )

            def nested_task_runner(nested_agent: str, nested_purpose: str, nested_prompt: str) -> str:
                nested = self._run_agent_task(
                    session=session,
                    parent_command=parent_command,
                    agent_name=nested_agent,
                    purpose=nested_purpose,
                    inputs=nested_prompt,
                    parent_result=f"Nested task from {agent_name}",
                    index=index + 100,
                    hooks=active_hooks,
                    strict_tools=False,
                )
                return nested.last_message

            executor = ToolExecutor(
                project_root=self.project_root,
                hooks=active_hooks,
                session=session,
                assume_yes=self.assume_yes,
                task_runner=nested_task_runner,
                allowed_tools=agent.metadata.get("tools"),
                plugin_root=self.loader.plugin_root_for(agent.path),
                additional_dirs=self.additional_dirs,
                agent_mcp_servers=self._agent_mcp_servers(agent),
                agent_name=agent_name,
                agent_memory_scope=agent_memory_scope(agent),
            )
            loop = StrictActionLoop(
                codex=self.codex,
                session=session,
                project_root=self.project_root,
                schema_path=_runtime_schema_path(),
                tool_executor=executor,
                max_steps=max_steps,
                model=profile.model,
                reasoning_effort=profile.effort,
                use_output_schema=self.strict_schema,
            )
            loop_result = loop.run(
                command=f"agent-{agent_name}",
                arguments=purpose,
                skill=task_skill,
                agent=agent,
                context_bundle=self._context_bundle(session=session),
                skill_support=self.loader.describe_skill_support(agent),
                runtime_profile=self._runtime_profile(profile, agent=agent),
                assume_yes=self.assume_yes,
                qa_mode="off",
                dry_run=self.dry_run,
            )
            session.write_json(
                f"task-{index}-{agent_name}/strict-result.json",
                {
                    "status": loop_result.status,
                    "final": loop_result.final,
                    "tool_results": [
                        {"tool": result.tool, "status": result.status, "summary": result.summary, "data": result.data}
                        for result in loop_result.tool_results
                    ],
                },
            )
            result = loop_result.codex_runs[-1] if loop_result.codex_runs else self.codex.exec_prompt(
                session=session,
                label=f"task-{index}-{agent_name}-empty",
                workdir=self.project_root,
                prompt="",
                dry_run=True,
            )
            if loop_result.status == "BLOCKED":
                result.returncode = max(result.returncode, 2)
        else:
            prompt = agent_task_prompt(
                parent_command=parent_command,
                task_agent=agent,
                purpose=purpose,
                inputs=inputs,
                parent_result=parent_result,
                context_bundle=self._context_bundle(session=session),
                project_root=self.project_root,
                runtime_profile=self._runtime_profile(profile, agent=agent),
                preloaded_skills=self._preloaded_skills(agent, arguments=inputs),
                agent_memory=self._agent_memory_context(agent),
            )
            result = self.codex.exec_prompt(
                session=session,
                label=f"task-{index}-{agent_name}",
                workdir=self.project_root,
                prompt=prompt,
                dry_run=self.dry_run,
                model=profile.model,
                reasoning_effort=profile.effort,
            )
        stop_results = active_hooks.fire(
            "SubagentStop",
            payload={"agent_type": agent_name, "agent": agent_name, "returncode": result.returncode},
            session=session,
            dry_run=self.dry_run,
        )
        stop_block = hook_block_reason(stop_results, assume_yes=self.assume_yes)
        if stop_block:
            result.returncode = max(result.returncode, 2)
            session.event("hook.blocked", "SubagentStop hook blocked task", agent=agent_name, reason=stop_block)
        session.finish_node(
            agent_node,
            status="done" if result.returncode == 0 else "failed",
            evidence={
                "prompt": str(result.prompt_path),
                "stdout": str(result.stdout_path),
                "stderr": str(result.stderr_path),
                "last_message": str(result.last_message_path),
            },
        )
        return result

    def _load_routed_agent(self, command: str, skill: MarkdownDocument) -> tuple[str, MarkdownDocument]:
        agent_name = str(skill.metadata.get("agent") or "")
        if agent_name:
            try:
                return agent_name, self.loader.load_agent(agent_name)
            except FileNotFoundError:
                return agent_name, self._synthetic_agent(
                    agent_name,
                    f"You are the `{agent_name}` agent declared by the skill frontmatter. "
                    "No separate agent file was found, so follow the skill instructions directly.",
                )

        synthetic = self._synthetic_agent(
            "main-session",
            "You are the main runtime session executing a Claude skill that does not declare "
            "a dedicated subagent. Follow the skill body directly, stay within the allowed "
            "tools, and use runtime Task/Agent actions when the skill requires delegation.",
            model=str(skill.metadata.get("model", "sonnet")),
            path=skill.path,
        )
        return "main-session", synthetic

    def _synthetic_agent(
        self,
        name: str,
        body: str,
        *,
        model: str = "sonnet",
        path: Path | None = None,
    ) -> MarkdownDocument:
        return MarkdownDocument(
            path=path or self.project_root / f"{name}.md",
            metadata={"name": name, "model": model},
            body=body,
            raw="",
        )

    def _new_session(self, label: str) -> RuntimeSession:
        return RuntimeSession(
            self.project_root,
            label,
            metadata={
                "target_workspace": str(self.project_root),
                "skill_repositories": [str(path.resolve()) for path in self.additional_dirs],
                "dry_run": self.dry_run,
                "qa_mode": self.qa_mode,
            },
        )

    def _run_required_qa(
        self,
        *,
        session: RuntimeSession,
        target_path: str,
        parent_result: str,
        skill: MarkdownDocument | None = None,
        agent: MarkdownDocument | None = None,
    ) -> CodexRunResult:
        qa_resolution = resolve_qa_agent(
            self.project_root,
            skill=skill,
            agent=agent,
            additional_dirs=self.additional_dirs,
        )
        qa_agent_name = qa_resolution.agent_name
        try:
            qa_agent = self.loader.load_agent(qa_agent_name)
        except FileNotFoundError:
            qa_agent = self._synthetic_agent(
                qa_agent_name,
                "You are a QA tester. Verify behavior with executable evidence and report a verdict.",
            )
        self.hooks.fire(
            "SubagentStart",
            payload={
                "agent_type": qa_agent_name,
                "agent": qa_agent_name,
                "purpose": "Required runtime QA pass",
                "qa_resolution": qa_resolution.__dict__,
            },
            session=session,
            dry_run=self.dry_run,
        )
        qa_node = session.start_node(
            "agent",
            qa_agent_name,
            metadata={"purpose": "Required runtime QA pass", "qa_resolution": qa_resolution.__dict__},
        )
        prompt = qa_prompt(
            task_agent=qa_agent,
            project_root=self.project_root,
            target_path=target_path,
            parent_result=parent_result,
            context_bundle=self._context_bundle(session=session),
        )
        result = self.codex.exec_prompt(
            session=session,
            label=f"required-qa-{qa_agent_name}",
            workdir=self.project_root,
            prompt=prompt,
            dry_run=self.dry_run,
        )
        self.hooks.fire(
            "SubagentStop",
            payload={"agent_type": qa_agent_name, "agent": qa_agent_name, "returncode": result.returncode},
            session=session,
            dry_run=self.dry_run,
        )
        session.finish_node(
            qa_node,
            status="done" if result.returncode == 0 else "failed",
            evidence={
                "prompt": str(result.prompt_path),
                "stdout": str(result.stdout_path),
                "stderr": str(result.stderr_path),
                "last_message": str(result.last_message_path),
            },
        )
        return result

    def _should_run_required_qa(self, command: str, arguments: str) -> bool:
        if self.qa_mode == "off":
            return False
        if self.qa_mode == "required":
            return True
        for pattern in _qa_auto_patterns():
            if ":" in pattern:
                command_pattern, argument_pattern = pattern.split(":", 1)
            else:
                command_pattern, argument_pattern = pattern, "*"
            if fnmatch.fnmatch(command, command_pattern) and fnmatch.fnmatch(arguments.lower(), argument_pattern.lower()):
                return True
        return False

    def _infer_target_path(self, arguments: str, parent_result: str) -> str:
        for token in [*arguments.split(), *parent_result.split()]:
            clean = token.strip("`'\".,:;")
            if not clean or clean.startswith("-"):
                continue
            candidate = Path(clean)
            if not candidate.is_absolute():
                candidate = self.project_root / candidate
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            try:
                resolved.relative_to(self.project_root)
            except ValueError:
                continue
            if resolved.exists():
                return clean
        return str(self.project_root)

    def _with_hook_skill_context(self, context: str, hook_results: list[object]) -> str:
        sections = [context] if context else []
        for result in hook_results:
            command = getattr(result, "command", "")
            if not isinstance(command, str) or not command.startswith("skill:"):
                continue
            reference = command.removeprefix("skill:")
            try:
                skill = self.loader.load_skill_by_reference(reference)
            except FileNotFoundError:
                sections.append(f"## Hook Skill Missing\n\nRequested hook skill `{reference}` was not found in this runtime root.")
                continue
            sections.append(
                "## Hook-Injected Skill: "
                f"{skill.metadata.get('name') or skill.path.parent.name}\n\n"
                f"Source: `{skill.path}`\n\n"
                f"{skill.body}"
            )
        return "\n\n---\n\n".join(sections)

    def _hooks_for(
        self,
        skill: MarkdownDocument | None = None,
        agent: MarkdownDocument | None = None,
    ) -> HookDispatcher:
        inline_sources: list[tuple[Path, dict[str, object], Path | None]] = []
        for document in [skill, agent]:
            if document is None:
                continue
            hooks = document.metadata.get("hooks")
            if isinstance(hooks, dict):
                inline_sources.append((document.path, {"hooks": hooks}, self.loader.plugin_root_for(document.path)))
        if not inline_sources:
            return self.hooks
        return HookDispatcher(
            self.loader.settings_candidates(),
            self.project_root,
            prompt_runner=self._run_prompt_hook,
            inline_sources=inline_sources,
        )

    def _preloaded_skills(self, agent: MarkdownDocument, *, arguments: str = "") -> str:
        sections: list[str] = []
        for reference in agent_skill_references(agent):
            try:
                skill = self.loader.load_skill_by_reference(reference)
            except FileNotFoundError:
                sections.append(f"### Missing skill: {reference}\n\nThe agent declared this skill, but the runtime could not load it.")
                continue
            sections.append(
                f"### {skill.metadata.get('name') or skill.path.parent.name}\n\n"
                f"Source: `{skill.path}`\n\n"
                f"{skill.body}"
            )
        return "\n\n---\n\n".join(sections)

    def _agent_memory_context(self, agent: MarkdownDocument) -> str:
        scope = agent_memory_scope(agent)
        name = str(agent.metadata.get("name") or agent.path.stem)
        return agent_memory_context(self.project_root, agent_name=name, scope=scope)

    def _agent_mcp_servers(self, agent: MarkdownDocument):
        return servers_from_agent_mcp_specs(
            agent.metadata.get("mcpServers"),
            project_root=self.project_root,
            plugin_root=self.loader.plugin_root_for(agent.path),
            additional_dirs=self.additional_dirs,
        )

    def _context_bundle(self, *, session: RuntimeSession, hook_results: list[object] | None = None) -> str:
        sections: list[ContextSection] = []
        context_mode = _context_mode()
        include_history = _context_history_enabled(context_mode)
        include_registry = _context_registry_enabled(context_mode)
        include_external_context = _context_external_enabled(context_mode)

        def add_section(name: str, text: str, *, priority: int = 100, required: bool = False) -> None:
            if text:
                sections.append(ContextSection(name=name, text=text, priority=priority, required=required))

        context = self.loader.read_context_bundle()
        add_section("context-files", context, priority=10, required=True)
        touched_paths = session.touched_paths()
        if include_external_context:
            add_section("bridge-context", bridge_context(self.project_root), priority=30)
            add_section("voice-context", voice_context(self.project_root), priority=35)
            add_section("ide-context", ide_context(self.project_root), priority=35)
            add_section("mcp-context", mcp_instructions_context(self.project_root, additional_dirs=self.additional_dirs), priority=20)
            add_section("capability-context", capability_context(self.project_root, additional_dirs=self.additional_dirs), priority=20)
            add_section("project-memory", project_memory_context(self.project_root), priority=25)
        add_section("compact-state", compact_state_context(session), priority=25)
        add_section("plan-mode", plan_mode_context(session), priority=25)
        add_section("session-memory", session_memory_context(session), priority=15, required=True)
        add_section("invoked-skills", session.invoked_skills_context(max_chars=20000), priority=15, required=True)
        if include_registry:
            skill_registry = self.loader.skill_registry_context(
                touched_paths=touched_paths,
                context_window_tokens=_context_window_tokens(),
            )
            add_section("skill-registry", skill_registry, priority=40)
        if include_history:
            durable_memory = relevant_memory_context(
                self.project_root,
                query=_context_query(session),
                recent_tools=_recent_tool_names(session),
                selector=self._memory_side_query_selector(session) if _side_query_enabled(context_mode) else None,
            )
            add_section("durable-memory", durable_memory, priority=45)
            memory = runtime_memory_context(self.project_root, exclude_session=session.id)
            add_section("runtime-memory", memory, priority=60)
        budgeted = apply_context_budget(sections, context_window=context_window_tokens())
        record_compact_state(session, budgeted)
        bundle_sections = [section.text for section in budgeted.sections]
        bundle_sections.append(budget_context_for_prompt(budgeted))
        bundle = "\n\n---\n\n".join(bundle_sections)
        if hook_results:
            return self._with_hook_skill_context(bundle, hook_results)
        return bundle

    def _runtime_profile(
        self,
        profile,
        *,
        skill: MarkdownDocument | None = None,
        agent: MarkdownDocument | None = None,
    ) -> str:
        system_prompt = build_compat_system_prompt(
            project_root=self.project_root,
            skill=skill,
            agent=agent,
            options=SystemPromptOptions(
                output_style=profile.output_style,
                permission_mode=profile.permission_mode,
                custom_system_prompt=self.custom_system_prompt,
                append_system_prompt=self.append_system_prompt,
                coordinator=profile.coordinator,
                scratchpad_dir=profile.scratchpad_dir,
            ),
        )
        return "\n\n".join(part for part in [profile.prompt_section(), system_prompt] if part.strip())

    def _record_memory(
        self,
        session: RuntimeSession,
        *,
        command: str,
        arguments: str,
        status: str,
        notes: str,
        gates: list[GateResult],
    ) -> None:
        try:
            maybe_update_session_memory(
                session,
                command=command,
                arguments=arguments,
                note=notes,
                status=status,
                force=True,
            )
            record_session_summary(
                session,
                command=command,
                arguments=arguments,
                status=status,
                notes=notes,
                gates=gates,
            )
            background = _memory_jobs_background()
            extraction_job = run_memory_extraction_job(
                self.project_root,
                session,
                command=command,
                arguments=arguments,
                status=status,
                notes=notes,
                gates=gates,
                background=background,
            )
            consolidation_job = run_memory_consolidation_job(self.project_root, background=background)
            session.event(
                "memory.jobs",
                "Scheduled runtime memory extraction and consolidation jobs",
                extraction_job=str(extraction_job),
                consolidation_job=str(consolidation_job),
                background=background,
            )
        except Exception as exc:
            session.event("memory.error", "Failed to record runtime memory", error=str(exc))

    def _memory_side_query_selector(self, session: RuntimeSession):
        def selector(query: str, headers: list[MemoryHeader], manifest: str, recent_tools: list[str]) -> list[str] | None:
            self._side_query_counter += 1
            valid = {item.filename for item in headers}
            prompt = (
                "# Runtime Memory Side Query\n\n"
                "Select memory files useful for the current task. Return only JSON with key `selected_memories` as an array of filenames. "
                "Only choose filenames from the manifest. Choose at most five. Return an empty array if none are clearly useful.\n\n"
                f"## Query\n\n{query[:8000]}\n\n"
                f"## Recently Used Tools\n\n{', '.join(recent_tools[:20])}\n\n"
                f"## Memory Manifest\n\n{manifest[:20000]}\n"
            )
            result = self.codex.exec_prompt(
                session=session,
                label=f"memory-side-query-{self._side_query_counter:03d}",
                workdir=self.project_root,
                prompt=prompt,
                dry_run=self.dry_run,
                timeout_seconds=45,
            )
            if self.dry_run or result.returncode != 0:
                return None
            try:
                parsed = parse_json_response(result.last_message)
            except ValueError:
                session.event("memory.side_query_error", "Memory side-query returned invalid JSON")
                return None
            selected = parsed.get("selected_memories")
            if not isinstance(selected, list):
                return None
            filtered = [str(item) for item in selected if str(item) in valid]
            session.event("memory.side_query", "Selected relevant memories via side-query", selected=filtered)
            return filtered

        return selector


def _runtime_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "action-result.schema.json"


def _chat_turn_prompt(*, message: str, context: str, skill_registry: str) -> str:
    return f"""# Codex Skill Runtime Chat Turn

You are the real assistant for a generic skill runtime UI.

Your response will be shown in the main chat as the assistant message. Do not pretend that runtime routing rules are your own thoughts. If you need more information from the user, ask a concise clarifying question. If a skill should be used, say which visible skill you would use and why, but do not claim it has already run unless the transcript says it did.

Runtime/tool/model process events are shown separately by the UI. Keep your chat response human-readable. Do not dump raw skill score lists. Do not invent hidden reasoning.

## User Message

{message}

## Visible Skill Registry

{skill_registry or "No visible skills were discovered."}

## Runtime Context

{context}
"""


def _chat_loop_skill_body(*, message: str) -> str:
    return f"""# Natural-Language Runtime Conversation

The user is interacting through the generic runtime chat UI. Treat this as the primary Claude Code style conversation entry point, not as a passive Q&A surface.

## User Message

{message}

## Conversational Execution Rules

- Decide whether the request can be answered directly or needs runtime work.
- If a visible skill matches the user request, request a `skill` action before improvising.
- After a `skill` action returns a loaded skill body, follow that skill body directly.
- If the loaded skill or current task needs another skill, request another `skill` action; nested skill invocation is allowed.
- Use runtime tools for file reads/writes, commands, MCP, memory, artifacts, and delegation.
- Use `brief` or `SendUserMessage` for short human-visible progress updates that should appear in the main conversation.
- Use `ask_user_question` only when a required user decision cannot be safely inferred.
- If the user gave enough autonomy, continue with reasonable defaults and pause only for missing credentials, irreversible/destructive choices, external costs, or major product-direction decisions.
- Return `status: final` only when the useful work is complete or when a direct answer is sufficient.
"""


def _resume_context_block(project_root: Path, resume_from: str | None) -> str:
    if not resume_from:
        return ""
    try:
        replay = replay_context(project_root, resume_from)
    except (FileNotFoundError, OSError, ValueError) as exc:
        replay = f"Runtime could not reconstruct the prior session replay: {exc}"
    try:
        question = pending_question_context(project_root, resume_from)
    except (FileNotFoundError, OSError, ValueError) as exc:
        question = f"Runtime could not reconstruct the pending question context: {exc}"
    return (
        "\n\n---\n\n"
        "## Prior Conversational Session Resume Context\n\n"
        f"Source session: `{resume_from}`\n\n"
        "Continue the prior workflow from this context. Preserve loaded skill instructions, tool observations, "
        "worker state, memory, and the user's answer. Verify live files before editing.\n\n"
        f"{question}\n\n"
        f"{replay}\n"
    )


def _pending_question_exists(session: RuntimeSession) -> bool:
    pending_path = session.path("pending-question.json")
    answer_path = session.path("pending-question-answer.json")
    if not pending_path.exists():
        return False
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    if not isinstance(pending, dict) or not pending.get("question"):
        return False
    try:
        answer = json.loads(answer_path.read_text(encoding="utf-8", errors="replace")) if answer_path.exists() else {}
    except (OSError, ValueError):
        answer = {}
    return not (isinstance(answer, dict) and answer.get("status") == "answered")


def _context_window_tokens() -> int | None:
    return context_window_tokens()


def _context_query(session: RuntimeSession) -> str:
    parts = [session.label, json.dumps(session.metadata, ensure_ascii=False)]
    if session.events_path.exists():
        try:
            events = [
                json.loads(line)
                for line in session.events_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError):
            events = []
        for event in events[-20:]:
            if isinstance(event, dict):
                parts.append(str(event.get("message") or ""))
                data = event.get("data")
                if isinstance(data, dict):
                    parts.append(json.dumps(data, ensure_ascii=False)[:1000])
    return "\n".join(part for part in parts if part)


def _recent_tool_names(session: RuntimeSession) -> list[str]:
    tools_dir = session.dir / "tools"
    if not tools_dir.exists():
        return []
    names: list[str] = []
    for path in sorted(tools_dir.glob("*.json"))[-20:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("tool"):
            names.append(str(data.get("tool")))
    return names


def _qa_auto_patterns() -> list[str]:
    raw = os.environ.get("SKILL_RUNTIME_QA_AUTO_PATTERNS") or os.environ.get("CODEX_SKILL_RUNTIME_QA_AUTO_PATTERNS") or ""
    return [item.strip() for item in raw.replace("\n", ";").split(";") if item.strip()]


def _context_mode() -> str:
    value = os.environ.get("SKILL_RUNTIME_CONTEXT_MODE") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_MODE") or "full"
    normalized = value.strip().lower()
    if normalized in {"lean", "lite", "minimal", "local"}:
        return "lean"
    if normalized in {"full", "default", "complete"}:
        return "full"
    return "full"


def _context_history_enabled(context_mode: str | None = None) -> bool:
    explicit = os.environ.get("SKILL_RUNTIME_CONTEXT_HISTORY") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_HISTORY")
    if explicit is not None and explicit.strip():
        return _env_truthy_text(explicit)
    return (context_mode or _context_mode()) != "lean"


def _context_registry_enabled(context_mode: str | None = None) -> bool:
    explicit = os.environ.get("SKILL_RUNTIME_CONTEXT_REGISTRY") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_REGISTRY")
    if explicit is not None and explicit.strip():
        return _env_truthy_text(explicit)
    return (context_mode or _context_mode()) != "lean"


def _context_external_enabled(context_mode: str | None = None) -> bool:
    explicit = os.environ.get("SKILL_RUNTIME_CONTEXT_EXTERNAL") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_EXTERNAL")
    if explicit is not None and explicit.strip():
        return _env_truthy_text(explicit)
    return (context_mode or _context_mode()) != "lean"


def _side_query_enabled(context_mode: str | None = None) -> bool:
    value = os.environ.get("SKILL_RUNTIME_MEMORY_SIDE_QUERY") or os.environ.get("CODEX_SKILL_RUNTIME_MEMORY_SIDE_QUERY") or "auto"
    normalized = value.strip().lower()
    if normalized in {"auto", ""}:
        return (context_mode or _context_mode()) != "lean"
    return normalized not in {"0", "false", "no", "off", "disabled"}


def _env_truthy_text(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _memory_jobs_background() -> bool:
    value = os.environ.get("SKILL_RUNTIME_MEMORY_JOBS") or os.environ.get("CODEX_SKILL_RUNTIME_MEMORY_JOBS") or "inline"
    return value.strip().lower() in {"background", "async", "thread", "true", "1"}


def _node_status_for_gate(gate: GateResult) -> str:
    if gate.status in {"PASS", "WARN", "DRY-RUN"}:
        return "passed"
    if gate.status == "BLOCKED":
        return "blocked"
    return "failed"
