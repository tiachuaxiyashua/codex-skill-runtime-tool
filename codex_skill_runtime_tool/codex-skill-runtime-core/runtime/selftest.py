from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from pathlib import Path

from .bridge import LocalBridge, bridge_context
from .capabilities import discover_capabilities
from .codex_cli import CodexCLI
from .frontmatter import MarkdownDocument
from .gates import evaluate_qa_report
from .hooks import HookDispatcher, hook_block_reason, hook_updated_input
from .ide import IDESelection, ide_context, write_ide_diagnostics, write_ide_selection
from .large_results import write_replacement_manifest
from .loaders import SkillRepositoryLoader
from .memdir import consolidate_memories, extract_session_memories, relevant_memory_context, scan_memory_files
from .memory import project_memory_context, record_session_summary, runtime_memory_context
from .mcp import discover_mcp_servers, mcp_instructions_context
from .mcp_oauth import complete_oauth_authorization, start_oauth_authorization, stored_oauth_headers, token_record_from_auth_output
from .microcompact import TIME_BASED_MC_CLEARED_MESSAGE, compact_observations
from .jobs import JobRegistry
from .plugins import set_plugin_enabled
from .prompts import skill_prompt
from .questions import answer_pending_question, load_pending_question, pending_question_context
from .runtime import CodexSkillRuntime
from .secure_store import SecureTokenStore
from .session import RuntimeSession
from .session_memory import session_memory_context, update_session_memory
from .state_paths import runtime_state_path
from .system_prompt import SystemPromptOptions, build_compat_system_prompt, clear_system_prompt_section_cache
from .tasks import parse_task_requests
from .token_budget import ContextSection, apply_context_budget
from .tool_executor import ToolExecutor
from .transcript import replay_context
from .voice import VoiceRuntime, session_text, voice_context
from .workers import WorkerRegistry


@dataclass
class Check:
    name: str
    status: str
    details: str


class SelfTestFailure(AssertionError):
    pass


class SelfTester:
    def __init__(
        self,
        *,
        project_root: Path,
        codex_executable: str,
        model: str | None,
        live_qa_target: Path | None,
        live_strict_target: str | None = None,
        additional_dirs: list[Path] | None = None,
    ) -> None:
        self.loaded_root = project_root.resolve()
        self.additional_dirs = [path.resolve() for path in (additional_dirs or [])]
        self.source_root = self._contract_source_root()
        self.project_root = self._prepare_fixture_root()
        self.codex_executable = codex_executable
        self.model = model
        self.live_qa_target = live_qa_target.expanduser().resolve() if live_qa_target is not None else None
        self.live_strict_target = live_strict_target
        self.results: list[Check] = []

    def run_all(self) -> int:
        checks = [
            self._loader_discovery,
            self._frontmatter_contract,
            self._task_and_gate_contract,
            self._codex_dry_run_contract,
            self._strict_dry_run_contract,
            self._tool_executor_contract,
            self._session_terminal_status_contract,
            self._permission_contract,
            self._command_preprocessing_contract,
            self._plugin_manifest_contract,
            self._skill_registry_contract,
            self._generic_platform_contract,
            self._question_pause_contract,
            self._project_memory_contract,
            self._hook_decision_contract,
            self._external_layout_contract,
            self._mcp_bridge_contract,
            self._compat_gap_contract,
            self._worker_registry_contract,
            self._large_tool_result_contract,
            self._model_effort_command_contract,
            self._codex_api_proxy_config_contract,
            self._isolated_runtime_env_contract,
            self._memory_compaction_contract,
            self._session_memory_contract,
            self._memdir_recall_contract,
            self._token_budget_contract,
            self._microcompact_contract,
            self._system_prompt_contract,
            self._transcript_resume_contract,
            self._mcp_oauth_store_contract,
            self._bridge_voice_ide_contract,
            self._hook_shim_contract,
            self._live_strict_contract,
            self._live_codex_qa_contract,
            self._claude_tree_clean,
        ]
        for check in checks:
            self._run_check(check)

        for result in self.results:
            print(f"{result.status}: {result.name} - {result.details}")
        failed = [result for result in self.results if result.status == "FAIL"]
        print(f"SELFTEST_SUMMARY total={len(self.results)} failed={len(failed)}")
        return 1 if failed else 0

    def _run_check(self, func) -> None:
        name = func.__name__.removeprefix("_").replace("_", "-")
        try:
            details = func()
        except SelfTestFailure as exc:
            self.results.append(Check(name, "FAIL", str(exc)))
        except Exception as exc:  # pragma: no cover - CLI diagnostics path.
            self.results.append(Check(name, "FAIL", f"{type(exc).__name__}: {exc}"))
        else:
            if isinstance(details, tuple):
                status, message = details
                self.results.append(Check(name, status, message))
            else:
                self.results.append(Check(name, "PASS", details))

    def _loader_discovery(self) -> str:
        loader = SkillRepositoryLoader(self.loaded_root, additional_dirs=self.additional_dirs)
        skills = loader.list_skills()
        agents = loader.list_agents()
        self._assert(len(skills) >= 70, f"expected >=70 skills, found {len(skills)}")
        self._assert(len(agents) >= 45, f"expected >=45 agents, found {len(agents)}")
        for skill in ["prototype", "team-qa", "setup-engine", "dev-story"]:
            self._assert(skill in skills or f"ccgs:{skill}" in skills, f"missing skill {skill}")
        for agent in ["prototyper", "qa-tester", "qa-lead"]:
            self._assert(agent in agents, f"missing agent {agent}")
        return f"skills={len(skills)} agents={len(agents)}"

    def _frontmatter_contract(self) -> str:
        loader = SkillRepositoryLoader(self.loaded_root, additional_dirs=self.additional_dirs)
        prototype = loader.load_skill("prototype")
        team_qa = loader.load_skill("team-qa")
        qa_tester = loader.load_agent("qa-tester")

        self._assert(prototype.metadata.get("agent") == "prototyper", "prototype must route to prototyper")
        self._assert(team_qa.metadata.get("agent") == "qa-lead", "team-qa must route to qa-lead")
        allowed_tools = prototype.metadata.get("allowed-tools", [])
        self._assert("Task" in allowed_tools, "prototype must allow Task")
        self._assert("AskUserQuestion" in allowed_tools, "prototype must allow AskUserQuestion")
        qa_tools = qa_tester.metadata.get("tools", [])
        self._assert("Bash" in qa_tools, "qa-tester must allow Bash")
        return "prototype/team-qa/qa-tester frontmatter matched expected routing"

    def _task_and_gate_contract(self) -> str:
        requests = parse_task_requests(
            "RUNTIME_TASK_REQUEST: agent=qa-tester; purpose=verify prototype; inputs=prototypes/foo"
        )
        self._assert(len(requests) == 1, "expected one parsed task")
        self._assert(requests[0].agent == "qa-tester", "task agent mismatch")

        passing = evaluate_qa_report("VERDICT: PASS\n\nEVIDENCE MATRIX\n- command ran")
        self._assert(passing.status == "PASS", f"expected PASS, got {passing.status}")
        blocked = evaluate_qa_report("VERDICT: PASS\n\nNo evidence here")
        self._assert(blocked.status == "BLOCKED", "PASS without evidence must block")
        missing = evaluate_qa_report("Looks fine")
        self._assert(missing.status == "BLOCKED", "missing verdict must block")
        weak = evaluate_qa_report("VERDICT: PASS\n\nEvidence: trust me")
        self._assert(weak.status == "BLOCKED", "PASS without evidence matrix must block")
        return "Task parser and QA gate reject weak QA output"

    def _codex_dry_run_contract(self) -> str:
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=True,
            assume_yes=True,
            qa_mode="required",
        )
        result = runtime.run_skill(
            "/prototype",
            "Tile map coin collection prototype --path engine --spike",
        )
        self._assert(result.exit_code == 0, f"dry-run exit should be 0, got {result.exit_code}")
        self._assert(result.primary is not None, "primary dry-run missing")
        self._assert(len(result.tasks) == 1, "required QA task dry-run missing")
        self._assert(result.gates and result.gates[0].status == "DRY-RUN", "dry-run QA gate mismatch")

        command_json = result.primary.prompt_path.parent / "dry-run-command.json"
        data = json.loads(command_json.read_text(encoding="utf-8"))
        command = data["command"]
        self._assert("exec" in command, "codex command missing exec")
        exec_index = command.index("exec")
        approval_index = command.index("--ask-for-approval")
        self._assert(approval_index < exec_index, "global approval flag must appear before exec")

        prompt = result.primary.prompt_path.read_text(encoding="utf-8")
        self._assert("## Skill Body" in prompt, "skill body missing from prompt")
        self._assert("## Agent Body" in prompt, "agent body missing from prompt")
        self._assert("RUNTIME_TASK_REQUEST" in prompt, "Task contract missing from prompt")

        qa_prompt = result.tasks[0].prompt_path.read_text(encoding="utf-8")
        self._assert("EVIDENCE MATRIX" in qa_prompt, "QA prompt missing evidence requirement")
        self._assert("intermediate state updates" in qa_prompt, "QA prompt missing intermediate-state check")
        return f"session={result.session.id}"

    def _strict_dry_run_contract(self) -> str:
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=True,
            assume_yes=True,
            qa_mode="off",
        )
        result = runtime.run_strict_skill(
            "/prototype",
            "Tile map coin collection prototype --path engine --spike",
            max_steps=2,
        )
        self._assert(result.exit_code == 0, f"strict dry-run exit should be 0, got {result.exit_code}")
        self._assert(result.primary is not None, "strict dry-run primary missing")
        self._assert(result.gates and result.gates[0].status == "DRY-RUN", "strict dry-run gate mismatch")
        plan = result.session.path("workflow-plan.json")
        self._assert(plan.exists(), "workflow plan missing")
        plan_text = plan.read_text(encoding="utf-8")
        self._assert("strict-action-loop" in plan_text, "prototype plan missing strict-action-loop phase")
        command_json = result.primary.prompt_path.parent / "dry-run-command.json"
        data = json.loads(command_json.read_text(encoding="utf-8"))
        self._assert("--output-schema" in data["command"], "strict run must use output schema")
        return f"session={result.session.id}"

    def _tool_executor_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-tools")
        hooks = HookDispatcher(self.project_root / ".claude" / "settings.json", self.project_root)
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=hooks,
            session=session,
            assume_yes=True,
        )

        read = executor.execute({"tool": "read_file", "reason": "test read", "parameters": {"path": ".claude/skills/prototype/SKILL.md", "max_chars": 2000}})
        self._assert(read.status == "OK" and "prototype" in read.data.get("content", ""), "read_file failed")
        glob_result = executor.execute({"tool": "glob", "reason": "test glob", "parameters": {"pattern": ".claude/agents/*.md"}})
        self._assert(glob_result.status == "OK" and glob_result.data.get("total", 0) >= 45, "glob failed")
        grep = executor.execute({"tool": "grep", "reason": "test grep", "parameters": {"path": ".claude/skills/prototype/SKILL.md", "pattern": "AskUserQuestion"}})
        self._assert(grep.status == "OK" and grep.data.get("matches"), "grep failed")
        external_probe = session.path("external-read-probe.txt")
        external_probe.write_text("EXTERNAL_READ_PROBE", encoding="utf-8")
        absolute_glob = executor.execute({"tool": "glob", "reason": "test external glob", "parameters": {"pattern": str(external_probe)}})
        self._assert(absolute_glob.status == "OK" and absolute_glob.data.get("total") == 1, "absolute glob outside active project failed")
        absolute_grep = executor.execute({"tool": "grep", "reason": "test external grep", "parameters": {"path": str(external_probe), "pattern": "EXTERNAL_READ_PROBE"}})
        self._assert(absolute_grep.status == "OK" and absolute_grep.data.get("matches"), "absolute grep outside active project failed")

        write_path = Path(".selftest") / session.id / "tool-write.txt"
        write = executor.execute({"tool": "write_file", "reason": "test nested write", "parameters": {"arguments": {"path": str(write_path), "content": "alpha\n"}}})
        self._assert(write.status == "OK", "nested write_file arguments failed")
        edit = executor.execute({"tool": "edit_file", "reason": "test edit", "parameters": {"path": str(write_path), "old": "alpha", "new": "beta"}})
        self._assert(edit.status == "OK", "edit_file failed")
        denied = executor.execute({"tool": "write_file", "reason": "test denied write", "parameters": {"path": ".claude/SHOULD_NOT_WRITE.txt", "content": "no"}})
        self._assert(denied.status == "ERROR", ".claude write must be blocked")
        question = executor.execute({"tool": "ask_user_question", "reason": "test question", "parameters": {"question": "Proceed?", "options": ["yes", "no"]}})
        self._assert(question.status == "OK" and question.data.get("answer") == "yes", "assume-yes question failed")
        bash = executor.execute({"tool": "bash", "reason": "test bash", "parameters": {"command": "python -c \"print('SELFTEST_BASH_OK')\"", "timeout": 30}})
        self._assert(bash.status == "OK" and "SELFTEST_BASH_OK" in bash.data.get("stdout", ""), "bash tool failed")
        return f"session={session.id}"

    def _session_terminal_status_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-session-terminal-status")
        skill = session.start_node("skill", "selftest:status")
        agent = session.start_node("agent", "selftest-agent", parent_id=skill)
        failed_probe = session.start_node("tool", "read_file", parent_id=agent)
        session.finish_node(failed_probe, status="failed")
        session.finish_node(agent, status="done")
        session.finish_node(skill, status="done")
        session.set_status("done")
        state = json.loads(session.path("session-state.json").read_text(encoding="utf-8"))
        self._assert(state.get("status") == "done", "terminal session status must not be poisoned by earlier failed probe tools")
        return f"session={session.id}"

    def _permission_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-permissions")
        settings_path = session.path("settings.json")
        settings_path.write_text(
            json.dumps({"permissions": {"ask": ["Bash(git status*)"], "deny": ["Bash(git reset*)"]}}),
            encoding="utf-8",
        )
        hooks = HookDispatcher(settings_path, self.project_root)
        blocked = ToolExecutor(
            project_root=self.project_root,
            hooks=hooks,
            session=session,
            assume_yes=False,
            allowed_tools=["Read"],
        ).execute({"tool": "bash", "parameters": {"command": "git status --short", "timeout": 30}})
        self._assert(blocked.status == "BLOCKED", "ask permission must block without assume-yes")

        allowed = ToolExecutor(
            project_root=self.project_root,
            hooks=hooks,
            session=session,
            assume_yes=True,
            allowed_tools=["Read"],
        ).execute({"tool": "glob", "parameters": {"pattern": "README.md"}})
        self._assert(allowed.status == "OK", "skill allowed-tools must not be treated as a hard whitelist")

        denied = ToolExecutor(
            project_root=self.project_root,
            hooks=hooks,
            session=session,
            assume_yes=True,
        ).execute({"tool": "bash", "parameters": {"command": "git reset --hard", "timeout": 30}})
        self._assert(denied.status == "ERROR", "deny permission must block matching bash")
        return f"session={session.id}"

    def _command_preprocessing_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-command-preprocess")
        plugin_root = session.path("plugin")
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "pre"}), encoding="utf-8")
        (plugin_root / "commands").mkdir(parents=True, exist_ok=True)
        (plugin_root / "docs").mkdir(parents=True, exist_ok=True)
        (plugin_root / "docs" / "ref.md").write_text("REFERENCE_CONTENT", encoding="utf-8")
        (plugin_root / "plugin-note.md").write_text("PLUGIN_ROOT_CONTENT", encoding="utf-8")
        command_path = plugin_root / "commands" / "check.md"
        command_path.write_text("", encoding="utf-8")
        command = MarkdownDocument(
            path=command_path,
            metadata={"name": "check"},
            body=(
                "first=$1 second=$2 indexed=$ARGUMENTS[0] all=$ARGUMENTS\n"
                "local @docs/ref.md\n"
                "plugin @${CLAUDE_PLUGIN_ROOT}/plugin-note.md\n"
                "dynamic !`echo dyn-$1`\n"
            ),
            raw="",
        )
        prompt = skill_prompt(
            command="pre:check",
            arguments="alpha beta",
            skill=command,
            agent=MarkdownDocument(path=command_path, metadata={"name": "main-session"}, body="Execute.", raw=""),
            context_bundle="",
            project_root=plugin_root,
            assume_yes=True,
            qa_mode="off",
        )
        self._assert("first=alpha second=beta indexed=alpha all=alpha beta" in prompt, "positional arguments were not rendered")
        self._assert("REFERENCE_CONTENT" in prompt, "project-relative @file reference was not injected")
        self._assert("PLUGIN_ROOT_CONTENT" in prompt, "plugin-root @file reference was not injected")
        self._assert("dyn-alpha" in prompt and "!`" not in prompt, "dynamic context did not receive rendered positional arguments")
        return f"session={session.id}"

    def _plugin_manifest_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-plugin-manifest")
        root = session.path("plugin-root")
        plugin_root = root / "compat-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        manifest = {
            "name": "compat",
            "commands": "custom-commands",
            "agents": "custom-agents",
            "skills": "custom-skills",
            "hooks": "config/hooks.json",
            "mcpServers": "mcp/custom-mcp.json",
        }
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        for directory in ["commands", "custom-commands", "agents", "custom-agents", "skills/default-skill", "custom-skills/custom-skill", "hooks", "config", "mcp"]:
            (plugin_root / directory).mkdir(parents=True, exist_ok=True)
        (plugin_root / "commands" / "default.md").write_text("---\ndescription: default\n---\nDefault command", encoding="utf-8")
        (plugin_root / "custom-commands" / "custom.md").write_text("---\ndescription: custom\n---\nCustom command", encoding="utf-8")
        (plugin_root / "skills" / "default-skill" / "SKILL.md").write_text("---\nname: default-skill\n---\nDefault skill", encoding="utf-8")
        (plugin_root / "custom-skills" / "custom-skill" / "SKILL.md").write_text("---\nname: custom-skill\n---\nCustom skill", encoding="utf-8")
        (plugin_root / "agents" / "default-agent.md").write_text("---\nname: default-agent\n---\nDefault agent", encoding="utf-8")
        (plugin_root / "custom-agents" / "custom-agent.md").write_text("---\nname: custom-agent\n---\nCustom agent", encoding="utf-8")
        (plugin_root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": []}}), encoding="utf-8")
        (plugin_root / "config" / "hooks.json").write_text(json.dumps({"hooks": {"SessionEnd": []}}), encoding="utf-8")
        (plugin_root / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"defaultEcho": {"command": sys.executable, "args": ["-c", ""]}}}),
            encoding="utf-8",
        )
        (plugin_root / "mcp" / "custom-mcp.json").write_text(
            json.dumps({"mcpServers": {"customEcho": {"command": sys.executable, "args": ["-c", ""]}}}),
            encoding="utf-8",
        )

        loader = SkillRepositoryLoader(root)
        skills = loader.list_skills()
        agents = loader.list_agents()
        self._assert("compat:default" in skills, "default plugin commands must still load when custom commands are configured")
        self._assert("compat:custom" in skills, "custom plugin commands did not load")
        self._assert("compat:default-skill" in skills, "default plugin skills must still load when custom skills are configured")
        self._assert("compat:custom-skill" in skills, "custom plugin skills did not load")
        self._assert("default-agent" in agents and "custom-agent" in agents, "default/custom plugin agents did not load")
        settings = {path.name + ":" + path.parent.name for path in loader.settings_candidates()}
        self._assert("hooks.json:hooks" in settings and "hooks.json:config" in settings, "default/custom plugin hooks did not both load")
        server_names = {server.name for server in discover_mcp_servers(root)}
        self._assert({"defaultEcho", "customEcho"}.issubset(server_names), "plugin .mcp.json and manifest mcpServers path did not both load")
        return f"session={session.id}"

    def _skill_registry_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-skill-registry")
        ccgs_root = session.path("multi-root", "ccgs")
        art_root = session.path("multi-root", "art-plugin")
        (ccgs_root / "skills" / "start").mkdir(parents=True, exist_ok=True)
        (ccgs_root / "skills" / "start" / "SKILL.md").write_text(
            "---\nname: start\ndescription: Start a game workflow.\n---\nStart workflow.",
            encoding="utf-8",
        )
        (art_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (art_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "art", "skills": "skills"}),
            encoding="utf-8",
        )
        (art_root / "skills" / "generate-sprite").mkdir(parents=True, exist_ok=True)
        (art_root / "skills" / "generate-sprite" / "SKILL.md").write_text(
            "---\nname: generate-sprite\ndescription: Generate a sprite asset.\n---\nSprite workflow.",
            encoding="utf-8",
        )

        old = os.environ.get("SKILL_RUNTIME_NAMESPACES")
        os.environ["SKILL_RUNTIME_NAMESPACES"] = f"ccgs={ccgs_root}"
        try:
            loader = SkillRepositoryLoader(ccgs_root, additional_dirs=[art_root], bare=True)
            skills = loader.list_skills()
            self._assert("ccgs:start" in skills, "root namespace did not expose ccgs:start")
            self._assert("art:generate-sprite" in skills, "plugin namespace did not expose art:generate-sprite")
            self._assert(loader.load_skill("ccgs:start").path.name == "SKILL.md", "namespaced root skill did not load")
            self._assert(loader.load_skill_by_reference("art:generate-sprite").path.name == "SKILL.md", "namespaced plugin skill did not load")
            registry = loader.skill_registry_context(max_chars=600)
            self._assert("Runtime SkillTool Registry" in registry, "skill registry context missing")
            self._assert("ccgs:start" in registry and "art:generate-sprite" in registry, "skill registry omitted namespaced skills")
            self._assert("full SKILL.md content is loaded only through the `skill` action" in registry, "SkillTool contract missing")
        finally:
            if old is None:
                os.environ.pop("SKILL_RUNTIME_NAMESPACES", None)
            else:
                os.environ["SKILL_RUNTIME_NAMESPACES"] = old
        return f"session={session.id}"

    def _generic_platform_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-generic-platform")
        target = session.path("target-workspace")
        skill_repo = session.path("skill-repo")
        plugin_root = skill_repo / "generic-plugin"
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.md").write_text("TARGET_WORKSPACE", encoding="utf-8")
        (skill_repo / "skills" / "path-skill").mkdir(parents=True, exist_ok=True)
        (skill_repo / "skills" / "path-skill" / "SKILL.md").write_text(
            "---\nname: path-skill\ndescription: Path-filtered generic skill.\npaths: src/**\n---\nPath skill body.",
            encoding="utf-8",
        )
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "generic",
                    "skills": "skills",
                    "capabilities": [
                        {
                            "name": "local-renderer",
                            "kind": "test-service",
                            "endpoint": "http://127.0.0.1:1",
                            "description": "fixture capability",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (plugin_root / "skills" / "plugin-skill").mkdir(parents=True, exist_ok=True)
        (plugin_root / "skills" / "plugin-skill" / "SKILL.md").write_text(
            "---\nname: plugin-skill\ndescription: Generic plugin skill.\n---\nPlugin skill body.",
            encoding="utf-8",
        )
        (skill_repo / ".codex-skill-runtime").mkdir(parents=True, exist_ok=True)
        (skill_repo / ".codex-skill-runtime" / "capabilities.json").write_text(
            json.dumps({"capabilities": [{"name": "fixture-api", "kind": "http", "endpoint": "http://127.0.0.1:2"}]}),
            encoding="utf-8",
        )

        old_namespace = os.environ.get("SKILL_RUNTIME_NAMESPACES")
        os.environ["SKILL_RUNTIME_NAMESPACES"] = f"bench={skill_repo}"
        try:
            loader = SkillRepositoryLoader(target, additional_dirs=[skill_repo], bare=True)
            skills = loader.list_skills()
            self._assert("bench:path-skill" in skills, "skill repo was not discovered separately from target workspace")
            self._assert("generic:plugin-skill" in skills, "plugin skill was not discovered through additional skill repo")
            visible = loader.skill_listings(touched_paths=["src/main.py"], model_only=True)
            hidden = loader.skill_listings(touched_paths=["docs/readme.md"], model_only=True)
            self._assert(any(item.name == "bench:path-skill" for item in visible), "paths frontmatter did not expose matching skill")
            self._assert(not any(item.name == "bench:path-skill" for item in hidden), "paths frontmatter exposed non-matching skill")

            capabilities = discover_capabilities(target, additional_dirs=[skill_repo])
            names = {item.name for item in capabilities}
            self._assert({"fixture-api", "local-renderer"}.issubset(names), "capability registry missed file or plugin manifest capabilities")

            set_plugin_enabled(target, name="generic", root=plugin_root, enabled=False)
            disabled_loader = SkillRepositoryLoader(target, additional_dirs=[skill_repo], bare=True)
            self._assert("generic:plugin-skill" not in disabled_loader.list_skills(), "disabled plugin still exposed skills")
            set_plugin_enabled(target, name="generic", root=plugin_root, enabled=True)
            enabled_loader = SkillRepositoryLoader(target, additional_dirs=[skill_repo], bare=True)
            self._assert("generic:plugin-skill" in enabled_loader.list_skills(), "re-enabled plugin did not expose skills")

            executor = ToolExecutor(
                project_root=target,
                hooks=HookDispatcher([], target),
                session=session,
                assume_yes=False,
                additional_dirs=[skill_repo],
                allowed_tools=["Read", "Skill"],
            )
            read = executor.execute({"tool": "read_file", "parameters": {"path": "README.md"}})
            self._assert(read.status == "OK", "read inside target workspace failed")
            blocked_write = executor.execute({"tool": "write_file", "parameters": {"path": "blocked.txt", "content": "x"}})
            self._assert(blocked_write.status == "BLOCKED", "allowed-tools did not pause non-preapproved write")
            loaded = executor.execute({"tool": "skill", "parameters": {"name": "bench:path-skill", "allow_disabled": True}})
            self._assert(loaded.status == "OK", "nested skill action failed against separated skill repo")
            self._assert((session.dir / "invoked-skills.json").exists(), "invoked skill was not preserved in session state")

            jobs = JobRegistry(session.path("job-state"))
            job = jobs.create(operation="selftest", command=["python", "-V"], cwd=target, stdout=session.path("job.out"), stderr=session.path("job.err"))
            jobs.mark_started(job.id, pid=1)
            jobs.mark_finished(job.id, returncode=0)
            stored = jobs.get(job.id)
            self._assert(stored and stored.get("status") == "done", "persistent job lifecycle did not reach done")
        finally:
            if old_namespace is None:
                os.environ.pop("SKILL_RUNTIME_NAMESPACES", None)
            else:
                os.environ["SKILL_RUNTIME_NAMESPACES"] = old_namespace
        return f"target={target} skill_repo={skill_repo}"

    def _question_pause_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-question-pause")
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=HookDispatcher([], self.project_root),
            session=session,
            assume_yes=False,
        )
        result = executor.execute(
            {
                "tool": "ask_user_question",
                "parameters": {"question": "Pick a direction?", "options": ["A", "B"], "default": "A"},
            }
        )
        self._assert(result.status == "BLOCKED", "ask_user_question without assume-yes must block")
        pending = load_pending_question(self.project_root, session.id)
        self._assert(pending and pending.get("question") == "Pick a direction?", "pending question was not persisted")
        answered = answer_pending_question(self.project_root, session.id, "B")
        self._assert(answered.get("answer") == "B", "pending question answer was not persisted")
        context = pending_question_context(self.project_root, session.id)
        self._assert("User answer: B" in context, "pending question context did not include answer")
        return f"session={session.id}"

    def _project_memory_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-project-memory")
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=HookDispatcher([], self.project_root),
            session=session,
            assume_yes=True,
        )
        style = executor.execute(
            {
                "tool": "project_memory_write",
                "parameters": {"section": "style", "content": "Palette: teal, gold, charcoal.", "append": False},
            }
        )
        self._assert(style.status == "OK", "project_memory_write failed")
        asset = executor.execute(
            {
                "tool": "asset_register",
                "parameters": {"asset": {"id": "hero_idle", "path": "assets/hero_idle.png", "style_hash": "abc"}},
            }
        )
        self._assert(asset.status == "OK", "asset_register failed")
        read = executor.execute({"tool": "project_memory_read", "parameters": {"section": "all"}})
        self._assert("Palette: teal" in read.data.get("content", ""), "project memory read missed style")
        context = project_memory_context(self.project_root)
        self._assert("Runtime Project Memory" in context and "hero_idle" in context, "project memory context missing style or asset")
        return f"session={session.id}"

    def _hook_decision_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-hook-decisions")
        scripts = session.path("scripts")
        scripts.mkdir(parents=True, exist_ok=True)
        deny_script = scripts / "deny.py"
        deny_script.write_text(
            "import json, sys\n"
            "payload=json.load(sys.stdin)\n"
            "assert payload['hook_event_name']=='PreToolUse'\n"
            "assert payload['tool_name']=='Bash'\n"
            "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}, 'systemMessage': 'denied by hook'}), file=sys.stderr)\n",
            encoding="utf-8",
        )
        update_script = scripts / "update.py"
        update_script.write_text(
            "import json, sys\n"
            "json.load(sys.stdin)\n"
            "print(json.dumps({'hookSpecificOutput': {'updatedInput': {'command': 'echo hook-updated'}}}))\n",
            encoding="utf-8",
        )
        exit2_script = scripts / "exit2.py"
        exit2_script.write_text("import sys\nprint('exit two block', file=sys.stderr)\nsys.exit(2)\n", encoding="utf-8")

        deny_settings = session.path("deny-settings.json")
        deny_settings.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'"{sys.executable}" "{deny_script}"'}]}]}}),
            encoding="utf-8",
        )
        deny_hooks = HookDispatcher(deny_settings, self.project_root)
        deny_results = deny_hooks.fire(
            "PreToolUse",
            matcher_value="Bash",
            payload={"tool_name": "Bash", "tool_input": {"command": "echo no"}},
            session=session,
        )
        self._assert(hook_block_reason(deny_results) == "denied by hook", "permissionDecision deny was not interpreted as a block")

        update_settings = session.path("update-settings.json")
        update_settings.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f'"{sys.executable}" "{update_script}"'}]}]}}),
            encoding="utf-8",
        )
        update_hooks = HookDispatcher(update_settings, self.project_root)
        update_results = update_hooks.fire(
            "PreToolUse",
            matcher_value="Bash",
            payload={"tool_name": "Bash", "tool_input": {"command": "echo original"}},
            session=session,
        )
        self._assert(hook_updated_input(update_results).get("command") == "echo hook-updated", "updatedInput was not parsed")
        executor_result = ToolExecutor(
            project_root=self.project_root,
            hooks=update_hooks,
            session=session,
            assume_yes=True,
        ).execute({"tool": "bash", "parameters": {"command": "echo original", "timeout": 30}})
        self._assert(executor_result.status == "OK", f"updatedInput bash execution failed: {executor_result.summary}")
        self._assert("hook-updated" in executor_result.data.get("stdout", ""), "updatedInput did not mutate the executed bash command")

        exit2_settings = session.path("exit2-settings.json")
        exit2_settings.write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f'"{sys.executable}" "{exit2_script}"'}]}]}}),
            encoding="utf-8",
        )
        exit2_results = HookDispatcher(exit2_settings, self.project_root).fire("Stop", session=session)
        self._assert("exit two block" in (hook_block_reason(exit2_results) or ""), "hook exit code 2 was not interpreted as blocking")

        prompt_calls: list[dict[str, object]] = []

        def prompt_runner(prompt: str, payload: dict[str, object], runtime_session, plugin_root, timeout: int):
            prompt_calls.append(payload)
            return subprocess.CompletedProcess(["fake-prompt-hook"], 0, '{"decision": "block", "reason": "prompt hook block"}', "")

        prompt_settings = session.path("prompt-settings.json")
        prompt_settings.write_text(
            json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "prompt", "prompt": "Block test prompts"}]}]}}),
            encoding="utf-8",
        )
        prompt_results = HookDispatcher(prompt_settings, self.project_root, prompt_runner=prompt_runner).fire(
            "UserPromptSubmit",
            payload={"user_prompt": "test prompt"},
            session=session,
        )
        self._assert(prompt_calls and prompt_calls[0].get("user_prompt") == "test prompt", "prompt hook runner did not receive user_prompt payload")
        self._assert(hook_block_reason(prompt_results) == "prompt hook block", "prompt hook decision was not enforced")

        added_root = session.path("added-skill-root")
        added_hook = added_root / ".claude" / "hooks" / "relative-hook.sh"
        added_hook.parent.mkdir(parents=True, exist_ok=True)
        added_hook.write_text("#!/usr/bin/env bash\nprintf 'ADDED_HOOK_ROOT_OK'\nprintf 'ACTIVE_PROJECT' > hook-cwd-marker.txt\n", encoding="utf-8")
        added_settings = added_root / ".claude" / "settings.json"
        added_settings.write_text(
            json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "bash .claude/hooks/relative-hook.sh"}]}]}}),
            encoding="utf-8",
        )
        active_root = session.path("active-project")
        active_root.mkdir(parents=True, exist_ok=True)
        relative_results = HookDispatcher([added_settings], active_root).fire("SessionStart", session=session)
        self._assert(relative_results and relative_results[0].returncode == 0, "added-directory relative hook did not execute from its source root")
        self._assert("ADDED_HOOK_ROOT_OK" in relative_results[0].stdout, "added-directory relative hook output missing")
        self._assert((active_root / "hook-cwd-marker.txt").exists(), "added-directory hook did not execute against active project cwd")
        self._assert(not (added_root / "hook-cwd-marker.txt").exists(), "added-directory hook wrote into read-only source root")
        return f"session={session.id}"

    def _external_layout_contract(self) -> tuple[str, str] | str:
        external = runtime_state_path(self.project_root, "external-repos")
        required = [
            external / "the-startup",
            external / "arc",
            external / "coderabbit-skills",
            external / "anthropic-claude-code-public",
        ]
        if not all(path.exists() for path in required):
            return ("SKIP", "external GitHub skill repositories not downloaded")

        startup = SkillRepositoryLoader(external / "the-startup")
        startup_skills = startup.list_skills()
        self._assert("start:review" in startup_skills, "plugin skill namespace start:review missing")
        self._assert("team:api-contract-design" in startup_skills, "nested plugin skill missing")
        self._assert(startup.load_agent("build-feature").metadata.get("name") == "build-feature", "nested plugin agent missing")

        arc = SkillRepositoryLoader(external / "arc")
        self._assert(str(arc.load_skill("arc:audit").path).endswith("commands\\audit.md"), "top-level arc:audit should load command wrapper")
        self._assert(arc.load_skill_by_reference("audit").metadata.get("context") == "fork", "Skill reference should prefer forked SKILL.md")

        coderabbit = SkillRepositoryLoader(external / "coderabbit-skills")
        command = coderabbit.load_skill("coderabbit:coderabbit-review")
        prompt = skill_prompt(
            command="coderabbit:coderabbit-review",
            arguments="committed",
            skill=command,
            agent=MarkdownDocument(path=command.path, metadata={"name": "main-session"}, body="Execute command.", raw=""),
            context_bundle="",
            project_root=external / "coderabbit-skills",
            assume_yes=True,
            qa_mode="off",
        )
        self._assert("!`" not in prompt, "dynamic context markers should be rendered before prompt execution")
        self._assert("Current directory:" in prompt and "Git repo:" in prompt, "dynamic context command output missing")

        security = external / "anthropic-claude-code-public" / "plugins" / "security-guidance"
        session = RuntimeSession(security, "selftest-plugin-hook")
        plugin_hooks = HookDispatcher([security / "hooks" / "hooks.json"], security)
        hook_results = plugin_hooks.fire(
            "PreToolUse",
            matcher_value="Write",
            payload={"tool": "Write", "path": "x.py"},
            session=session,
            dry_run=True,
        )
        self._assert(hook_results and "CLAUDE_PLUGIN_ROOT" not in hook_results[0].command, "plugin hook did not expand CLAUDE_PLUGIN_ROOT")

        fork_session = RuntimeSession(external / "arc", "selftest-fork-skill")
        calls: list[tuple[str, str]] = []

        def task_runner(agent: str, purpose: str, prompt_text: str) -> str:
            calls.append((agent, purpose))
            return "FORK_OK"

        fork_result = ToolExecutor(
            project_root=external / "arc",
            hooks=HookDispatcher([], external / "arc"),
            session=fork_session,
            assume_yes=True,
            task_runner=task_runner,
        ).execute({"tool": "skill", "parameters": {"name": "audit"}})
        self._assert(fork_result.status == "OK" and fork_result.data.get("context") == "fork", "context: fork skill action not forked")
        self._assert(calls and calls[0][0] == "general-purpose", "forked skill did not use task runner")
        return "external command/plugin/fork contracts matched"

    def _mcp_bridge_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-mcp")
        mcp_root = session.path("mcp-root")
        mcp_root.mkdir(parents=True, exist_ok=True)
        server_path = mcp_root / "echo_mcp_server.py"
        server_path.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "for line in sys.stdin:",
                    "    if not line.strip():",
                    "        continue",
                    "    req = json.loads(line)",
                    "    if req.get('method') == 'initialize':",
                    "        print(json.dumps({'jsonrpc':'2.0','id':req.get('id'),'result':{'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'echo','version':'1'}}}), flush=True)",
                    "    elif req.get('method') == 'tools/call':",
                    "        args = req.get('params', {}).get('arguments', {})",
                    "        text = 'echo:' + str(args.get('text', ''))",
                    "        print(json.dumps({'jsonrpc':'2.0','id':req.get('id'),'result':{'content':[{'type':'text','text':text}], 'isError': False}}), flush=True)",
                ]
            ),
            encoding="utf-8",
        )

        class HTTPMCPHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                is_auth_endpoint = self.path == "/authmcp"
                if is_auth_endpoint:
                    if self.headers.get("Authorization") != "Bearer dynamic-token":
                        self.send_response(403)
                        self.end_headers()
                        self.wfile.write(b"missing auth token")
                        return
                elif self.headers.get("X-Helper") != "ok":
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"missing helper header")
                    return
                if payload.get("method") == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if payload.get("method") == "initialize":
                    body = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "http", "version": "1"},
                        },
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", "selftest-http-session")
                    self.end_headers()
                    self.wfile.write(json.dumps(body).encode("utf-8"))
                    return
                if payload.get("method") == "tools/call":
                    args = payload.get("params", {}).get("arguments", {})
                    body = {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "content": [{"type": "text", "text": ("auth:" if is_auth_endpoint else "http:") + str(args.get("text", ""))}],
                            "isError": False,
                        },
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(body).encode("utf-8"))
                    return
                self.send_response(404)
                self.end_headers()

        class SSEMCPHandler(BaseHTTPRequestHandler):
            events: "queue.Queue[dict[str, object] | None]" = queue.Queue()
            auth_events: "queue.Queue[dict[str, object] | None]" = queue.Queue()

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:
                if self.path == "/authsse" and self.headers.get("Authorization") != "Bearer dynamic-token":
                    self.send_response(403)
                    self.end_headers()
                    return
                if self.path not in {"/sse", "/authsse"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                endpoint_path = "/authmessage" if self.path == "/authsse" else "/message"
                endpoint = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}{endpoint_path}"
                self.wfile.write(f"event: endpoint\ndata: {endpoint}\n\n".encode("utf-8"))
                self.wfile.flush()
                events = self.auth_events if self.path == "/authsse" else self.events
                while True:
                    try:
                        event = events.get(timeout=15)
                    except queue.Empty:
                        break
                    if event is None:
                        break
                    self.wfile.write(("event: message\n" + "data: " + json.dumps(event) + "\n\n").encode("utf-8"))
                    self.wfile.flush()

            def do_POST(self) -> None:
                if self.path == "/authmessage" and self.headers.get("Authorization") != "Bearer dynamic-token":
                    self.send_response(403)
                    self.end_headers()
                    return
                if self.path not in {"/message", "/authmessage"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if payload.get("method") == "initialize":
                    target_events = self.auth_events if self.path == "/authmessage" else self.events
                    target_events.put(
                        {
                            "jsonrpc": "2.0",
                            "id": payload.get("id"),
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": "sse", "version": "1"},
                            },
                        }
                    )
                elif payload.get("method") == "tools/call":
                    args = payload.get("params", {}).get("arguments", {})
                    target_events = self.auth_events if self.path == "/authmessage" else self.events
                    target_events.put(
                        {
                            "jsonrpc": "2.0",
                            "id": payload.get("id"),
                            "result": {
                                "content": [{"type": "text", "text": "sse:" + str(args.get("text", ""))}],
                                "isError": False,
                            },
                        }
                    )
                self.send_response(202)
                self.end_headers()

        http_server = ThreadingHTTPServer(("127.0.0.1", 0), HTTPMCPHandler)
        sse_server = ThreadingHTTPServer(("127.0.0.1", 0), SSEMCPHandler)
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        sse_thread = threading.Thread(target=sse_server.serve_forever, daemon=True)
        http_thread.start()
        sse_thread.start()

        helper_path = mcp_root / "headers_helper.py"
        helper_path.write_text(
            "import json, os\n"
            "assert os.environ['CLAUDE_CODE_MCP_SERVER_NAME'] == 'httpEcho'\n"
            "assert os.environ['CLAUDE_CODE_MCP_SERVER_URL'].startswith('http://127.0.0.1:')\n"
            "print(json.dumps({'X-Helper': 'ok'}))\n",
            encoding="utf-8",
        )
        auth_path = mcp_root / "auth_command.py"
        auth_path.write_text(
            "import json, os\n"
            "assert os.environ['CLAUDE_CODE_MCP_SERVER_NAME'] in {'authEcho', 'sseAuthEcho'}\n"
            "print(json.dumps({'accessToken': 'dynamic-token'}))\n",
            encoding="utf-8",
        )
        (mcp_root / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "echo": {
                            "command": sys.executable,
                            "args": [str(server_path)],
                            "instructions": "MCP_INSTRUCTION_MARKER: echo text values exactly once.",
                        },
                        "httpEcho": {
                            "type": "http",
                            "url": f"http://127.0.0.1:{http_server.server_address[1]}/mcp",
                            "headersHelper": f'"{sys.executable}" "{helper_path}"',
                        },
                        "authEcho": {
                            "type": "http",
                            "url": f"http://127.0.0.1:{http_server.server_address[1]}/authmcp",
                            "authCommand": f'"{sys.executable}" "{auth_path}"',
                        },
                        "sseEcho": {
                            "type": "sse",
                            "url": f"http://127.0.0.1:{sse_server.server_address[1]}/sse",
                        },
                        "sseAuthEcho": {
                            "type": "sse",
                            "url": f"http://127.0.0.1:{sse_server.server_address[1]}/authsse",
                            "authCommand": f'"{sys.executable}" "{auth_path}"',
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            executor = ToolExecutor(
                project_root=mcp_root,
                hooks=HookDispatcher([], mcp_root),
                session=session,
                assume_yes=True,
            )
            mcp_context = mcp_instructions_context(mcp_root)
            self._assert("Runtime MCP Server Instructions" in mcp_context, "MCP instruction context header missing")
            self._assert("MCP_INSTRUCTION_MARKER" in mcp_context, "configured MCP instructions were not injected")
            result = executor.execute({"tool": "mcp__echo__echo", "parameters": {"arguments": {"text": "ok"}, "timeout": 10}})
            self._assert(result.status == "OK", f"stdio MCP bridge failed: {result.summary}")
            content = result.data.get("result", {}).get("content", [])
            self._assert(content and content[0].get("text") == "echo:ok", "stdio MCP bridge returned unexpected content")
            nested_mcp = executor.execute({"tool": "mcp", "parameters": {"arguments": {"tool": "mcp__echo__echo", "arguments": {"text": "nested-ok"}, "timeout": 10}}})
            self._assert(nested_mcp.status == "OK", f"nested MCP action arguments failed: {nested_mcp.summary}")
            nested_content = nested_mcp.data.get("result", {}).get("content", [])
            self._assert(nested_content and nested_content[0].get("text") == "echo:nested-ok", "nested MCP action returned unexpected content")

            http_result = executor.execute({"tool": "mcp__httpEcho__echo", "parameters": {"arguments": {"text": "ok"}, "timeout": 10}})
            self._assert(http_result.status == "OK", f"HTTP MCP bridge failed: {http_result.summary}")
            http_content = http_result.data.get("result", {}).get("content", [])
            self._assert(http_content and http_content[0].get("text") == "http:ok", "HTTP MCP bridge returned unexpected content")

            auth_result = executor.execute({"tool": "mcp__authEcho__echo", "parameters": {"arguments": {"text": "ok"}, "timeout": 10}})
            self._assert(auth_result.status == "OK", f"HTTP auth MCP bridge failed: {auth_result.summary}")
            auth_content = auth_result.data.get("result", {}).get("content", [])
            self._assert(auth_content and auth_content[0].get("text") == "auth:ok", "HTTP auth command bridge returned unexpected content")

            sse_result = executor.execute({"tool": "mcp__sseEcho__echo", "parameters": {"arguments": {"text": "ok"}, "timeout": 10}})
            self._assert(sse_result.status == "OK", f"SSE MCP bridge failed: {sse_result.summary}")
            sse_content = sse_result.data.get("result", {}).get("content", [])
            self._assert(sse_content and sse_content[0].get("text") == "sse:ok", "SSE MCP bridge returned unexpected content")

            sse_auth_result = executor.execute({"tool": "mcp__sseAuthEcho__echo", "parameters": {"arguments": {"text": "auth-ok"}, "timeout": 10}})
            self._assert(sse_auth_result.status == "OK", f"SSE auth MCP bridge failed: {sse_auth_result.summary}")
            sse_auth_content = sse_auth_result.data.get("result", {}).get("content", [])
            self._assert(sse_auth_content and sse_auth_content[0].get("text") == "sse:auth-ok", "SSE auth command bridge returned unexpected content")
        finally:
            SSEMCPHandler.events.put(None)
            SSEMCPHandler.auth_events.put(None)
            http_server.shutdown()
            sse_server.shutdown()
            http_server.server_close()
            sse_server.server_close()
        return f"session={session.id}"

    def _memory_compaction_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-memory")
        session.event("session.start", "memory contract start")
        session.write_json(
            "tools/001-read_file.json",
            {"tool": "read_file", "status": "OK", "summary": "Read README.md", "data": {"path": "README.md"}},
        )
        summary_path = record_session_summary(
            session,
            command="selftest-memory",
            arguments="",
            status="PASS",
            notes="Memory summary probe.",
            gates=[],
        )
        self._assert(summary_path.exists(), "session summary was not written")
        index_path = runtime_state_path(self.project_root, "sessions-index.json")
        self._assert(index_path.exists(), "session memory index was not written")
        context = runtime_memory_context(self.project_root, exclude_session="not-this-session", limit=5)
        self._assert("Runtime Memory / Compacted Session Context" in context, "runtime memory context header missing")
        self._assert("selftest-memory" in context and "Memory summary probe" in context, "runtime memory context did not include summary")
        return f"session={session.id}"

    def _session_memory_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-session-memory")
        session.event("session.start", "session memory contract start", arguments="alpha beta")
        session.write_json(
            "tools/001-read_file.json",
            {
                "tool": "read_file",
                "status": "OK",
                "summary": "Read contract file",
                "data": {"path": "README.md", "content": "SESSION_MEMORY_MARKER"},
            },
        )
        session.update_read_state(self.project_root / "README.md", "SESSION_MEMORY_READ_STATE")
        path = update_session_memory(
            session,
            command="selftest-session-memory",
            arguments="alpha beta",
            note="SESSION_MEMORY_NOTE_MARKER",
            status="PASS",
        )
        self._assert(path.exists(), "session memory summary was not written")
        context = session_memory_context(session)
        self._assert("Runtime Session Memory" in context, "session memory context header missing")
        self._assert("SESSION_MEMORY_NOTE_MARKER" in context, "session memory note missing")
        self._assert("Recent Tool Results" in context, "session memory tool section missing")
        state = json.loads((session.dir / "session-memory" / "state.json").read_text(encoding="utf-8"))
        self._assert(state.get("tool_count") == 1, "session memory state did not count tools")
        return f"session={session.id}"

    def _memdir_recall_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-memdir")
        session.event("session.start", "memdir contract start")
        record_session_summary(
            session,
            command="selftest-memdir",
            arguments="durable alpha",
            status="PASS",
            notes="DURABLE_MEMORY_MARKER",
            gates=[],
        )
        paths = extract_session_memories(
            self.project_root,
            session,
            command="selftest-memdir",
            arguments="durable alpha",
            status="PASS",
            notes="DURABLE_MEMORY_MARKER",
            gates=[],
        )
        self._assert(paths and paths[0].exists(), "durable memory topic was not written")
        headers = scan_memory_files(self.project_root)
        self._assert(any(header.filename.endswith("selftest-memdir.md") for header in headers), "memdir scan did not find topic")
        context = relevant_memory_context(self.project_root, query="durable alpha selftest-memdir")
        self._assert("Runtime Durable Memory Directory" in context, "durable memory context header missing")
        self._assert("DURABLE_MEMORY_MARKER" in context, "relevant durable memory did not include extracted note")
        overview = consolidate_memories(self.project_root, force=True)
        self._assert(overview is not None and overview.exists(), "memory consolidation overview was not written")
        self._assert("Runtime Durable Memory" in overview.read_text(encoding="utf-8"), "memory overview content missing")
        return f"session={session.id}"

    def _token_budget_contract(self) -> str:
        sections = [
            ContextSection("required", "REQUIRED_CONTEXT_MARKER", required=True, priority=1),
            ContextSection("large-optional", "OPTIONAL_CONTEXT_MARKER " + ("x" * 60000), priority=50),
        ]
        result = apply_context_budget(sections, context_window=6000, reserve_tokens=1000, min_preserved_tokens=1000)
        text = "\n".join(section.text for section in result.sections)
        self._assert("REQUIRED_CONTEXT_MARKER" in text, "required context was dropped")
        self._assert(result.estimated_tokens_after < result.estimated_tokens_before, "budget did not reduce context")
        self._assert("Runtime Context Budget" in result.report, "budget report header missing")
        self._assert(result.target_tokens == 5000, "budget target mismatch")
        return f"before={result.estimated_tokens_before} after={result.estimated_tokens_after}"

    def _microcompact_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-microcompact")
        observations = [
            {
                "step": 1,
                "actions": [
                    {
                        "tool": "read_file",
                        "status": "OK",
                        "summary": "large old result",
                        "data": {"content": "A" * 60000},
                    }
                ],
            },
            {
                "step": 2,
                "actions": [
                    {
                        "tool": "grep",
                        "status": "OK",
                        "summary": "recent result",
                        "data": {"content": "B" * 10000},
                    }
                ],
            },
        ]
        compacted, records = compact_observations(
            observations,
            session_dir=session.dir,
            threshold_chars=1000,
            keep_recent_steps=1,
        )
        self._assert(records, "microcompact did not write replacement records")
        compacted_data = compacted[0]["actions"][0]["data"]
        self._assert(TIME_BASED_MC_CLEARED_MESSAGE in json.dumps(compacted_data), "old observation was not replaced")
        self._assert(Path(records[0]["path"]).exists(), "microcompact full result was not persisted")
        manifest = session.dir / "microcompact" / "manifest.jsonl"
        self._assert(manifest.exists(), "microcompact manifest was not written")
        self._assert(compacted[1]["actions"][0]["data"]["content"].startswith("B"), "recent observation should be retained")
        return f"session={session.id}"

    def _system_prompt_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-system-prompt")
        style_dir = session.dir / "output-styles"
        style_dir.mkdir(parents=True, exist_ok=True)
        (style_dir / "audit.md").write_text("STYLE_AUDIT_MARKER", encoding="utf-8")
        custom_path = session.dir / "custom-system.md"
        custom_path.write_text("CUSTOM_SYSTEM_MARKER", encoding="utf-8")
        clear_system_prompt_section_cache()
        prompt = build_compat_system_prompt(
            project_root=session.dir,
            skill=MarkdownDocument(path=session.dir / "skill.md", metadata={"name": "probe-skill"}, body="", raw=""),
            agent=MarkdownDocument(path=session.dir / "agent.md", metadata={"name": "probe-agent"}, body="", raw=""),
            options=SystemPromptOptions(
                output_style="audit",
                permission_mode="acceptEdits",
                custom_system_prompt=f"@{custom_path}",
                append_system_prompt="APPEND_SYSTEM_MARKER",
                coordinator=True,
                scratchpad_dir=session.dir / "scratchpad",
            ),
        )
        for marker in [
            "Claude Code Runtime Compatibility",
            "Runtime Behavioral Contracts",
            "Runtime Tool And Delegation Contracts",
            "Runtime Context Lifecycle Contracts",
            "probe-skill",
            "probe-agent",
            "STYLE_AUDIT_MARKER",
            "CUSTOM_SYSTEM_MARKER",
            "APPEND_SYSTEM_MARKER",
            "Coordinator Runtime",
            "Read-before-edit contract",
            "Denied-tool retry contract",
            "Hook-feedback contract",
            "Prompt-injection detection contract",
            "Verify-before-complete contract",
            "Risk confirmation contract",
            "Dedicated-tool preference contract",
            "Parallel independent tool contract",
            "Compaction fact-preservation contract",
            "Delegation ownership contract",
            "Skill-discovery contract",
            "MCP instruction contract",
            "Scratchpad temp-files contract",
        ]:
            self._assert(marker in prompt, f"system prompt missing {marker}")
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=True,
            assume_yes=True,
            qa_mode="off",
            output_style="explanatory",
            system_prompt="RUNTIME_CUSTOM_SYSTEM",
            append_system_prompt="RUNTIME_APPEND_SYSTEM",
        )
        result = runtime.run_agent("qa-tester", "system prompt dry-run probe")
        self._assert(result.primary is not None, "runtime system prompt dry-run missing primary result")
        prompt_text = result.primary.prompt_path.read_text(encoding="utf-8", errors="replace")
        self._assert("RUNTIME_CUSTOM_SYSTEM" in prompt_text, "custom runtime system prompt was not injected")
        self._assert("RUNTIME_APPEND_SYSTEM" in prompt_text, "append runtime system prompt was not injected")
        self._assert("Prompt-injection detection contract" in prompt_text, "behavioral prompt contracts were not injected into runtime prompt")
        self._assert("Compaction fact-preservation contract" in prompt_text, "context lifecycle prompt contracts were not injected into runtime prompt")
        return f"session={session.id} runtime_session={result.session.id}"

    def _transcript_resume_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-transcript")
        session.event("session.start", "transcript contract start")
        session.event("tool.finish", "Read README.md", result={"tool": "read_file", "status": "OK"})
        readme = self.project_root / "README.md"
        if readme.exists():
            session.update_read_state(readme, "README_STATE_MARKER")
        session.write_json(
            "summary.json",
            {
                "command": "selftest-transcript",
                "arguments": "resume",
                "status": "PASS",
                "updated_at": "2026-05-24T00:00:00",
                "notes": "TRANSCRIPT_SUMMARY_MARKER",
            },
        )
        update_session_memory(
            session,
            command="selftest-transcript",
            arguments="resume",
            note="TRANSCRIPT_SESSION_MEMORY_MARKER",
            status="PASS",
        )

        def runner(agent: str, purpose: str, prompt: str, index: int) -> str:
            return f"TRANSCRIPT_WORKER_OUTPUT:{agent}:{index}"

        registry = WorkerRegistry(runner, session_dir=session.dir)
        registry.spawn(agent="resume-worker", purpose="resume worker probe", prompt="worker prompt", name="resume-probe")
        extract_session_memories(
            self.project_root,
            session,
            command="selftest-transcript",
            arguments="resume",
            status="PASS",
            notes="DURABLE_REPLAY_MARKER",
            gates=[],
        )
        full_path = session.path("large-tool-results", "001-data.txt")
        full_path.write_text("FULL_REPLACEMENT_MARKER", encoding="utf-8")
        write_replacement_manifest(
            session.dir,
            [
                {
                    "tool_id": "001",
                    "json_path": "data.content",
                    "path": str(full_path),
                    "bytes": 1024,
                    "replacement_text": "[LARGE_TOOL_RESULT]",
                }
            ],
        )
        context = replay_context(self.project_root, session.id)
        self._assert("Runtime Transcript Replay" in context, "replay header missing")
        self._assert("TRANSCRIPT_SUMMARY_MARKER" in context, "summary note missing from replay")
        self._assert("TRANSCRIPT_SESSION_MEMORY_MARKER" in context, "session memory missing from replay")
        self._assert("resume-worker" in context and "worker-001" in context, "worker records missing from replay")
        self._assert("DURABLE_REPLAY_MARKER" in context, "durable memory missing from replay")
        self._assert("tool.finish" in context, "tool timeline missing from replay")
        self._assert("Content Replacements" in context and "data.content" in context, "replacement manifest missing from replay")
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=True,
            assume_yes=True,
            qa_mode="off",
        )
        resumed = runtime.resume_session(session.id, "continue probe")
        self._assert(resumed.exit_code == 0 and resumed.primary is not None, "dry-run resume failed")
        prompt_text = resumed.primary.prompt_path.read_text(encoding="utf-8", errors="replace")
        self._assert("Runtime Transcript Replay" in prompt_text, "resume prompt did not include replay context")
        self._assert("Runtime Context Budget" in prompt_text, "resume prompt did not include runtime context budget")
        return f"session={session.id} resume_session={resumed.session.id}"

    def _mcp_oauth_store_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-mcp-oauth")
        auth_script = session.path("auth_command.py")
        auth_script.write_text(
            "import json\n"
            "print(json.dumps({'accessToken': 'cmd-token', 'expiresIn': 3600, 'scope': 'read'}))\n",
            encoding="utf-8",
        )
        command_config = {
            "type": "http",
            "url": "http://127.0.0.1:9/mcp",
            "authCommand": f'"{sys.executable}" "{auth_script}"',
        }
        auth_result = start_oauth_authorization(
            project_root=self.project_root,
            server_name="oauthCommand",
            config=command_config,
            server_url=command_config["url"],
        )
        self._assert(auth_result.get("status") == "authenticated", f"authCommand OAuth did not authenticate: {auth_result}")
        command_headers = stored_oauth_headers(project_root=self.project_root, server_name="oauthCommand", config=command_config)
        self._assert(command_headers.get("Authorization") == "Bearer cmd-token", "stored authCommand token header mismatch")

        class TokenHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                params = {key: values[0] for key, values in urllib.parse.parse_qs(body).items()}
                if params.get("grant_type") == "refresh_token" and params.get("refresh_token") == "refresh-token":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"access_token": "refreshed-token", "refresh_token": "refresh-token", "expires_in": 3600}).encode("utf-8"))
                    return
                if params.get("grant_type") != "authorization_code" or params.get("code") != "abc123":
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"access_token": "flow-token", "refresh_token": "refresh-token", "expires_in": 3600}).encode("utf-8"))

        token_server = ThreadingHTTPServer(("127.0.0.1", 0), TokenHandler)
        token_thread = threading.Thread(target=token_server.serve_forever, daemon=True)
        token_thread.start()
        flow_config = {
            "type": "http",
            "url": f"http://127.0.0.1:{token_server.server_address[1]}/mcp",
            "oauth": {
                "clientId": "client-id",
                "authorizationUrl": "https://auth.example.test/authorize",
                "tokenUrl": f"http://127.0.0.1:{token_server.server_address[1]}/token",
                "callbackPort": 8765,
            },
        }
        try:
            pending = start_oauth_authorization(
                project_root=self.project_root,
                server_name="oauthFlow",
                config=flow_config,
                server_url=flow_config["url"],
            )
            self._assert(pending.get("status") == "auth_url" and "authUrl" in pending, f"OAuth flow did not return auth URL: {pending}")
            pending_data = json.loads(Path(str(pending["pending_path"])).read_text(encoding="utf-8"))
            record = complete_oauth_authorization(
                project_root=self.project_root,
                server_name="oauthFlow",
                config=flow_config,
                callback_url=f"http://127.0.0.1:8765/callback?code=abc123&state={pending_data['state']}",
            )
            self._assert(record.access_token == "flow-token", "OAuth authorization code token was not stored")
            flow_headers = stored_oauth_headers(project_root=self.project_root, server_name="oauthFlow", config=flow_config)
            self._assert(flow_headers.get("Authorization") == "Bearer flow-token", "stored OAuth flow token header mismatch")
            expired = token_record_from_auth_output(
                server_name="oauthFlow",
                config=flow_config,
                output='{"access_token":"expired-token","refresh_token":"refresh-token","expires_at":1}',
            )
            SecureTokenStore(self.project_root).write(expired)
            refreshed_headers = stored_oauth_headers(project_root=self.project_root, server_name="oauthFlow", config=flow_config)
            self._assert(refreshed_headers.get("Authorization") == "Bearer refreshed-token", "stored OAuth refresh_token was not refreshed")
        finally:
            token_server.shutdown()
            token_server.server_close()

        direct_record = token_record_from_auth_output(
            server_name="directToken",
            config={"type": "http", "url": "http://127.0.0.1/direct"},
            output='{"access_token":"direct-token","expires_in":3600}',
        )
        SecureTokenStore(self.project_root).write(direct_record)
        direct_headers = stored_oauth_headers(
            project_root=self.project_root,
            server_name="directToken",
            config={"type": "http", "url": "http://127.0.0.1/direct"},
        )
        self._assert(direct_headers.get("Authorization") == "Bearer direct-token", "direct token store header mismatch")
        return f"session={session.id}"

    def _bridge_voice_ide_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-bridge-voice-ide")
        bridge = LocalBridge(self.project_root)
        env = bridge.register_environment(metadata={"test": "bridge"})
        work_id = bridge.enqueue_work(env.environment_id, kind="message", data={"text": "hello"})
        work = bridge.poll_work(env.environment_id)
        self._assert(isinstance(work, dict) and work.get("id") == work_id, "bridge poll did not deliver queued work")
        bridge.ack_work(env.environment_id, work_id)
        bridge.heartbeat(env.environment_id, work_id)
        bridge.write_session_event(session.id, {"type": "bridge.test", "message": "ok"})
        pointer = bridge.reconnect_session(env.environment_id, session.id)
        self._assert(pointer.exists() and "Runtime Bridge Context" in bridge_context(self.project_root), "bridge context missing")

        voice = VoiceRuntime(self.project_root)
        voice_session = voice.start()
        voice.append_transcript(voice_session.session_id, "VOICE_TRANSCRIPT_MARKER")
        finalized = voice.finalize(voice_session.session_id)
        self._assert("VOICE_TRANSCRIPT_MARKER" in session_text(finalized), "voice transcript text missing")
        self._assert("VOICE_TRANSCRIPT_MARKER" in voice_context(self.project_root), "voice context missing")

        write_ide_selection(
            self.project_root,
            IDESelection(file_path="README.md", text="IDE_SELECTION_MARKER", start_line=1, end_line=1),
        )
        write_ide_diagnostics(
            self.project_root,
            [{"file": "README.md", "line": 1, "severity": "info", "message": "IDE_DIAG_MARKER"}],
        )
        ide_text = ide_context(self.project_root)
        self._assert("IDE_SELECTION_MARKER" in ide_text and "IDE_DIAG_MARKER" in ide_text, "IDE context missing selection or diagnostics")

        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=HookDispatcher([], self.project_root),
            session=session,
            assume_yes=True,
        )
        bridge_tool = executor.execute({"tool": "bridge", "parameters": {"operation": "register", "metadata": {"via": "tool"}}})
        self._assert(bridge_tool.status == "OK" and bridge_tool.data.get("environment_id"), "bridge tool register failed")
        voice_tool = executor.execute({"tool": "voice", "parameters": {"operation": "start"}})
        self._assert(voice_tool.status == "OK" and voice_tool.data.get("session_id"), "voice tool start failed")
        ide_tool = executor.execute(
            {
                "tool": "ide",
                "parameters": {
                    "operation": "selection",
                    "file_path": "README.md",
                    "text": "IDE_TOOL_SELECTION",
                    "start_line": 1,
                    "end_line": 1,
                },
            }
        )
        self._assert(ide_tool.status == "OK", "ide tool selection failed")
        lsp_tool = executor.execute({"tool": "ide", "parameters": {"operation": "lsp_command", "command": [sys.executable, "--version"], "timeout": 10}})
        self._assert(lsp_tool.status in {"OK", "ERROR"}, "ide lsp command did not execute")
        return f"session={session.id}"

    def _compat_gap_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-compat-gap")
        skill_path = session.path("nested-skill", "SKILL.md")
        skill_path.write_text(
            "---\n"
            "name: nested-frontmatter\n"
            "description: nested frontmatter probe\n"
            "paths:\n"
            "  - src/**/*.py\n"
            "hooks:\n"
            "  SessionStart:\n"
            "    - matcher: nested\n"
            "      hooks:\n"
            "        - type: command\n"
            "          command: echo ok\n"
            "mcpServers:\n"
            "  - local\n"
            "skills: [verify, remember]\n"
            "effort: high\n"
            "---\n"
            "Body\n",
            encoding="utf-8",
        )
        document = MarkdownDocument(
            path=skill_path,
            metadata={},
            body="",
            raw="",
        )
        from .frontmatter import read_markdown_document
        from .compat import matches_paths, model_invocable

        parsed = read_markdown_document(skill_path)
        self._assert(isinstance(parsed.metadata.get("hooks"), dict), "nested hooks frontmatter was not parsed")
        self._assert(isinstance(parsed.metadata.get("mcpServers"), list), "nested mcpServers frontmatter was not parsed")
        self._assert(matches_paths(parsed, ["src/app/main.py"], base=session.dir), "paths frontmatter did not match expected file")
        self._assert(model_invocable(parsed), "skill should be model invocable by default")

        loader = SkillRepositoryLoader(self.project_root)
        bundled = loader.load_skill("verify")
        self._assert(bundled.metadata.get("source") == "bundled", "bundled verify skill was not available")
        return f"session={session.id}"

    def _worker_registry_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-workers")
        calls: list[tuple[str, str, str, int]] = []

        def runner(agent: str, purpose: str, prompt: str, index: int) -> str:
            calls.append((agent, purpose, prompt, index))
            return f"OUTPUT:{agent}:{index}:{prompt[-20:]}"

        registry = WorkerRegistry(runner, session_dir=session.dir)
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=HookDispatcher([], self.project_root),
            session=session,
            assume_yes=True,
            worker_registry=registry,
        )
        task = executor.execute({"tool": "task", "parameters": {"agent": "worker", "purpose": "research", "prompt": "find facts", "name": "facts"}})
        self._assert(task.status == "OK" and task.data.get("worker_id") == "worker-001", "worker task did not register")
        continued = executor.execute({"tool": "SendMessage", "parameters": {"to": "facts", "message": "now implement"}})
        self._assert(continued.status == "OK" and len(calls) == 2, "SendMessage did not continue named worker")
        stopped = executor.execute({"tool": "TaskStop", "parameters": {"task_id": "worker-001", "reason": "done"}})
        self._assert(stopped.status == "OK" and stopped.data.get("status") == "stopped", "TaskStop did not stop worker")
        workers_path = session.dir / "workers.json"
        self._assert(workers_path.exists(), "worker registry was not persisted")
        persisted = json.loads(workers_path.read_text(encoding="utf-8", errors="replace"))
        self._assert(persisted.get("workers") and persisted["workers"][0].get("id") == "worker-001", "persisted worker id missing")
        reloaded = WorkerRegistry(runner, session_dir=session.dir)
        described = reloaded.describe()
        self._assert(described and described[0].get("status") == "stopped", "persisted worker status was not restored")
        return f"session={session.id}"

    def _large_tool_result_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-large-result")
        rel = Path(".selftest") / session.id / "large.txt"
        large_file = self.project_root / rel
        large_file.parent.mkdir(parents=True, exist_ok=True)
        large_file.write_text("x" * 70000, encoding="utf-8")
        executor = ToolExecutor(
            project_root=self.project_root,
            hooks=HookDispatcher([], self.project_root),
            session=session,
            assume_yes=True,
        )
        result = executor.execute({"tool": "read_file", "parameters": {"path": str(rel), "max_chars": 80000}})
        self._assert(result.status == "OK", "large read failed")
        self._assert("_large_result_replacements" in result.data, "large tool result was not replaced with preview")
        replacement = result.data["_large_result_replacements"][0]
        self._assert(Path(replacement["path"]).exists(), "large tool result full output was not persisted")
        return f"session={session.id}"

    def _model_effort_command_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-model-effort")
        result = CodexCLI(executable=self.codex_executable).exec_prompt(
            session=session,
            label="model-effort",
            workdir=self.project_root,
            prompt="probe",
            dry_run=True,
            model="gpt-5.4",
            reasoning_effort="high",
        )
        command = result.command
        self._assert("--model" in command and "gpt-5.4" in command, "per-run model override missing")
        self._assert(any("model_reasoning_effort" in item for item in command), "per-run reasoning effort override missing")
        return f"session={session.id}"

    def _codex_api_proxy_config_contract(self) -> str:
        session = RuntimeSession(self.project_root, "selftest-codex-api-proxy")
        result = CodexCLI(
            executable=self.codex_executable,
            model="gpt-5.4",
            env={
                "OPENAI_API_KEY": "sk-test-secret",
                "HTTP_PROXY": "http://127.0.0.1:8888",
            },
            config_overrides=[
                'model_provider="proxy"',
                'model_providers.proxy.name="proxy"',
                'model_providers.proxy.base_url="https://proxy.example.test"',
                'model_providers.proxy.wire_api="responses"',
                "model_providers.proxy.requires_openai_auth=true",
            ],
            profile="proxy-profile",
        ).exec_prompt(
            session=session,
            label="proxy-config",
            workdir=self.project_root,
            prompt="probe",
            dry_run=True,
        )
        data = json.loads((session.dir / "proxy-config" / "dry-run-command.json").read_text(encoding="utf-8"))
        command = data["command"]
        self._assert("--profile" in command and "proxy-profile" in command, "codex profile override missing")
        self._assert(any(item == 'model_provider="proxy"' for item in command), "codex model_provider config missing")
        self._assert(any("model_providers.proxy.base_url" in item for item in command), "codex base_url config missing")
        self._assert(data["env"].get("OPENAI_API_KEY") == "[REDACTED]", "API key must be redacted in dry-run evidence")
        self._assert(data["env"].get("HTTP_PROXY") == "http://127.0.0.1:8888", "proxy env missing from dry-run evidence")
        self._assert(result.returncode == 0, "dry-run proxy config command failed")
        return f"session={session.id}"

    def _isolated_runtime_env_contract(self) -> str:
        import os

        from .universal_cli import _build_parser, _config_from_args, _runtime_from_config

        previous_state_root = os.environ.get("SKILL_RUNTIME_STATE_ROOT")
        session = RuntimeSession(self.project_root, "selftest-isolated-runtime-env")
        key_file = session.dir / "api-key.txt"
        env_file = session.dir / "skill-runtime.env"
        codex_home = session.dir / "isolated-codex-home"
        secret = "sk-runtime-env-secret"
        key_file.write_text(secret + "\n", encoding="utf-8")
        env_file.write_text(
            "\n".join(
                [
                    f"SKILL_RUNTIME_ROOT={self.project_root}",
                    f"SKILL_RUNTIME_CODEX_HOME={codex_home}",
                    f"SKILL_RUNTIME_STATE_ROOT={session.dir / 'runtime-state'}",
                    f"SKILL_RUNTIME_CODEX_EXECUTABLE={self.codex_executable}",
                    "SKILL_RUNTIME_DRY_RUN=true",
                    "SKILL_RUNTIME_STRICT_SCHEMA=false",
                    "SKILL_RUNTIME_ASSUME_YES=true",
                    "SKILL_RUNTIME_QA=off",
                    "CODEX_MODEL=gpt-5.4",
                    'CODEX_CONFIG=["model_reasoning_effort=low","network_access=enabled","model_context_window=1000000"]',
                    "CODEX_PROVIDER=runtimeenv",
                    "CODEX_BASE_URL=https://runtime-env-proxy.example.test",
                    "CODEX_WIRE_API=responses",
                    "CODEX_REQUIRES_OPENAI_AUTH=true",
                    f"CODEX_API_KEY_FILE={key_file}",
                    "HTTP_PROXY=http://127.0.0.1:7777",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        try:
            argv = ["--runtime-env", str(env_file), "inspect"]
            parser = _build_parser()
            args = parser.parse_args(argv)
            config = _config_from_args(args, argv=argv)

            self._assert(config.root == self.project_root, "runtime env root was not applied")
            self._assert(config.model == "gpt-5.4", "runtime env model was not applied")
            self._assert(config.dry_run is True and config.assume_yes is True, "runtime env booleans were not applied")
            self._assert(config.strict_schema is False, "runtime env strict schema flag was not applied")
            self._assert(config.codex_env and config.codex_env.get("CODEX_HOME") == str(codex_home.resolve()), "isolated CODEX_HOME missing")
            self._assert(config.isolated_codex_home == codex_home.resolve(), "isolated home path not recorded")
            self._assert(config.runtime_state_root == (session.dir / "runtime-state").resolve(), "runtime state root not recorded")
            self._assert(config.codex_config_path and config.codex_config_path.exists(), "isolated config.toml missing")
            self._assert(config.codex_auth_path and config.codex_auth_path.exists(), "isolated auth.json missing")

            config_text = config.codex_config_path.read_text(encoding="utf-8")
            self._assert('model = "gpt-5.4"' in config_text, "isolated config missing model")
            self._assert('model_reasoning_effort = "low"' in config_text, "isolated config did not quote string override")
            self._assert('network_access = "enabled"' in config_text, "isolated config did not quote network_access override")
            self._assert("model_context_window = 1000000" in config_text, "isolated config did not preserve integer override")
            self._assert('model_provider = "runtimeenv"' in config_text, "isolated config missing provider")
            self._assert("runtime-env-proxy.example.test" in config_text, "isolated config missing base_url")
            self._assert(secret not in config_text, "isolated config.toml must not contain API key")

            auth_data = json.loads(config.codex_auth_path.read_text(encoding="utf-8"))
            self._assert(auth_data.get("OPENAI_API_KEY") == secret, "isolated auth.json missing API key")

            runtime = _runtime_from_config(config)
            result = runtime.codex.exec_prompt(
                session=session,
                label="isolated-env-dry-run",
                workdir=self.project_root,
                prompt="probe",
                dry_run=True,
            )
            evidence = json.loads((session.dir / "isolated-env-dry-run" / "dry-run-command.json").read_text(encoding="utf-8"))
            self._assert(evidence["env"].get("CODEX_HOME") == str(codex_home.resolve()), "dry-run evidence missing isolated CODEX_HOME")
            self._assert(evidence["env"].get("OPENAI_API_KEY") == "[REDACTED]", "dry-run evidence leaked API key")
            self._assert(secret not in json.dumps(evidence, ensure_ascii=False), "dry-run evidence contains raw API key")
            self._assert(result.returncode == 0, "isolated env dry-run failed")
            return f"session={session.id} home={codex_home.resolve()}"
        finally:
            if previous_state_root is None:
                os.environ.pop("SKILL_RUNTIME_STATE_ROOT", None)
            else:
                os.environ["SKILL_RUNTIME_STATE_ROOT"] = previous_state_root

    def _hook_shim_contract(self) -> tuple[str, str] | str:
        if shutil.which("bash") is None:
            return ("SKIP", "bash not found; hook shim not exercised")

        session = RuntimeSession(self.project_root, "selftest-hook")
        hooks = HookDispatcher(self.project_root / ".claude" / "settings.json", self.project_root)
        start = hooks.fire(
            "SubagentStart",
            payload={"agent_type": "qa-tester"},
            session=session,
            dry_run=False,
        )
        stop = hooks.fire(
            "SubagentStop",
            payload={"agent_type": "qa-tester"},
            session=session,
            dry_run=False,
        )
        self._assert(start and start[0].returncode == 0, "SubagentStart hook failed")
        self._assert(stop and stop[0].returncode == 0, "SubagentStop hook failed")

        audit = self.project_root / "production" / "session-logs" / "agent-audit.log"
        self._assert(audit.exists(), "agent audit log was not written")
        text = audit.read_text(encoding="utf-8", errors="replace")
        self._assert("Agent invoked: qa-tester" in text, "agent invocation audit missing")
        self._assert("Agent completed: qa-tester" in text, "agent completion audit missing")
        return f"session={session.id}"

    def _live_codex_qa_contract(self) -> tuple[str, str] | str:
        if self.live_qa_target is None:
            return ("SKIP", "no --live-qa-target supplied")
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=False,
            assume_yes=True,
            qa_mode="off",
        )
        prompt = (
            f"Please QA this project: {self.live_qa_target}. "
            "Run available tests or create a temporary probe outside the project if needed. "
            "Focus on the requested behavior, intermediate state updates, reset flows, and visible feedback consistency. "
            "Do not modify project files. Final answer must include VERDICT and EVIDENCE MATRIX."
        )
        result = runtime.run_agent("qa-tester", prompt)
        self._assert(result.primary is not None, "live QA did not produce a primary result")
        self._assert(result.primary.returncode == 0, f"live QA codex exit={result.primary.returncode}")
        gate = evaluate_qa_report(result.primary.last_message)
        self._assert(gate.status in {"PASS", "WARN"}, f"live QA gate status={gate.status}: {gate.reason}")
        return f"session={result.session.id} gate={gate.status}"

    def _live_strict_contract(self) -> tuple[str, str] | str:
        if self.live_strict_target is None:
            return ("SKIP", "no --live-strict-target supplied")
        runtime = CodexSkillRuntime(
            project_root=self.project_root,
            codex=CodexCLI(executable=self.codex_executable, model=self.model),
            dry_run=False,
            assume_yes=True,
            qa_mode="off",
        )
        result = runtime.run_strict_smoke(self.live_strict_target, max_steps=3)
        self._assert(result.exit_code == 0, f"strict smoke exit={result.exit_code}")
        self._assert(result.gates and result.gates[0].status == "PASS", "strict smoke did not PASS")
        strict_result = result.session.path("strict-result.json")
        data = json.loads(strict_result.read_text(encoding="utf-8"))
        tools = data.get("tool_results", [])
        self._assert(any(tool.get("tool") == "read_file" and tool.get("status") == "OK" for tool in tools), "strict smoke did not execute read_file")
        return f"session={result.session.id}"

    def _claude_tree_clean(self) -> str:
        completed = subprocess.run(
            ["git", "diff", "--", ".claude"],
            cwd=str(self.source_root),
            text=True,
            capture_output=True,
            check=False,
        )
        self._assert(completed.returncode == 0, f"git diff failed: {completed.stderr}")
        self._assert(completed.stdout.strip() == "", ".claude has local modifications")
        return ".claude diff is empty"

    def _contract_source_root(self) -> Path:
        for candidate in [self.loaded_root, *self.additional_dirs]:
            if (candidate / ".claude" / "skills" / "prototype" / "SKILL.md").exists():
                return candidate
        return self.loaded_root

    def _prepare_fixture_root(self) -> Path:
        label = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        fixture = runtime_state_path(self.loaded_root, "selftest-fixtures", label)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            self.source_root,
            fixture,
            ignore=shutil.ignore_patterns(".git", ".codex-skill-runtime", ".skill-runtime", "__pycache__"),
        )
        return fixture

    def _assert(self, condition: bool, message: str) -> None:
        if not condition:
            raise SelfTestFailure(message)
