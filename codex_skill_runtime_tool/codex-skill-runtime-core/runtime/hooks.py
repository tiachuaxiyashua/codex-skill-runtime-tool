from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .session import RuntimeSession
from .state_paths import runtime_state_path


@dataclass
class HookResult:
    event: str
    matcher: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    skipped: bool = False
    output: dict[str, Any] | None = None
    decision: str | None = None
    system_message: str = ""
    permission_decision: str | None = None
    updated_input: dict[str, Any] | None = None


@dataclass(frozen=True)
class HookSource:
    path: Path
    settings: dict[str, Any]
    plugin_root: Path | None
    script_root: Path


@dataclass(frozen=True)
class PermissionDecision:
    status: str
    reason: str
    rule: str | None = None


PromptHookRunner = Callable[
    [str, dict[str, Any], RuntimeSession | None, Path | None, int],
    subprocess.CompletedProcess[str],
]


class HookDispatcher:
    def __init__(
        self,
        settings_path: Path | Iterable[Path],
        project_root: Path,
        *,
        prompt_runner: PromptHookRunner | None = None,
        inline_sources: Iterable[tuple[Path, dict[str, Any], Path | None]] | None = None,
    ) -> None:
        paths = [settings_path] if isinstance(settings_path, Path) else list(settings_path)
        self.settings_path = paths[0] if paths else project_root / ".claude" / "settings.json"
        self.settings_paths = paths
        self.project_root = project_root
        self.prompt_runner = prompt_runner
        self.inline_sources = list(inline_sources or [])
        self.sources = self._load_sources()
        self.settings = self._merged_settings()

    def _load_sources(self) -> list[HookSource]:
        sources: list[HookSource] = []
        for path in self.settings_paths:
            if not path.exists():
                continue
            settings = json.loads(path.read_text(encoding="utf-8"))
            plugin_root = _plugin_root_for_settings(path)
            sources.append(HookSource(path=path, settings=settings, plugin_root=plugin_root, script_root=_hook_script_root(path, plugin_root, self.project_root)))
        if not sources and self.settings_path.exists():
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            plugin_root = _plugin_root_for_settings(self.settings_path)
            sources.append(HookSource(path=self.settings_path, settings=settings, plugin_root=plugin_root, script_root=_hook_script_root(self.settings_path, plugin_root, self.project_root)))
        for path, settings, plugin_root in self.inline_sources:
            sources.append(HookSource(path=path, settings=settings, plugin_root=plugin_root, script_root=_hook_script_root(path, plugin_root, self.project_root)))
        return sources

    def _merged_settings(self) -> dict[str, Any]:
        merged: dict[str, Any] = {"hooks": {}, "permissions": {"allow": [], "ask": [], "deny": []}}
        for source in self.sources:
            for event, entries in source.settings.get("hooks", {}).items():
                merged["hooks"].setdefault(event, []).extend(entries)
            for key in ["allow", "ask", "deny"]:
                merged["permissions"].setdefault(key, []).extend(source.settings.get("permissions", {}).get(key, []))
        return merged

    def fire(
        self,
        event: str,
        *,
        matcher_value: str = "",
        payload: dict[str, Any] | None = None,
        session: RuntimeSession | None = None,
        dry_run: bool = False,
    ) -> list[HookResult]:
        results: list[HookResult] = []
        hook_payload = self._build_payload(event, payload or {}, session=session)
        payload_text = json.dumps(hook_payload, ensure_ascii=False)

        for source in self.sources:
            hooks = source.settings.get("hooks", {}).get(event, [])
            for entry in hooks:
                matcher = entry.get("matcher", "")
                if not _matches(matcher, matcher_value):
                    continue
                for hook in entry.get("hooks", []):
                    hook_type = hook.get("type")
                    if hook_type == "skill":
                        skill_name = str(hook.get("skill", ""))
                        result = HookResult(
                            event=event,
                            matcher=matcher,
                            command=f"skill:{skill_name}",
                            returncode=0,
                            stdout=skill_name,
                            stderr="",
                        )
                        results.append(result)
                        if session is not None:
                            session.event(
                                "hook",
                                f"{event} hook skill: {skill_name}",
                                matcher=matcher,
                                returncode=0,
                                skipped=False,
                                stdout=skill_name,
                                stderr="",
                                source=str(source.path),
                            )
                        continue

                    if hook_type == "prompt":
                        prompt = _expand_plugin_root(str(hook.get("prompt", "")), source.plugin_root)
                        timeout = int(hook.get("timeout", 30))
                        if dry_run:
                            result = _make_hook_result(
                                event=event,
                                matcher=matcher,
                                command=f"prompt:{prompt[:120]}",
                                returncode=0,
                                stdout="",
                                stderr="",
                                skipped=True,
                            )
                        elif self.prompt_runner is None:
                            result = _make_hook_result(
                                event=event,
                                matcher=matcher,
                                command=f"prompt:{prompt[:120]}",
                                returncode=0,
                                stdout=json.dumps({"continue": True}, ensure_ascii=False),
                                stderr="prompt hook runner is not configured",
                                skipped=True,
                            )
                        else:
                            completed = self.prompt_runner(prompt, hook_payload, session, source.plugin_root, timeout)
                            result = _make_hook_result(
                                event=event,
                                matcher=matcher,
                                command=f"prompt:{prompt[:120]}",
                                returncode=completed.returncode,
                                stdout=completed.stdout,
                                stderr=completed.stderr,
                            )
                        results.append(result)
                        if session is not None:
                            session.event(
                                "hook",
                                f"{event} prompt hook",
                                matcher=matcher,
                                returncode=result.returncode,
                                skipped=result.skipped,
                                stdout=(result.stdout or "")[-2000:],
                                stderr=(result.stderr or "")[-2000:],
                                decision=result.decision,
                                permission_decision=result.permission_decision,
                                updated_input=result.updated_input,
                                source=str(source.path),
                            )
                        continue

                    if hook_type != "command":
                        continue
                    command = _expand_plugin_root(str(hook.get("command", "")), source.plugin_root)
                    timeout = int(hook.get("timeout", 10))
                    if dry_run:
                        result = _make_hook_result(event=event, matcher=matcher, command=command, returncode=0, stdout="", stderr="", skipped=True)
                    else:
                        completed = self._run_command(
                            command=command,
                            payload_text=payload_text,
                            timeout=timeout,
                            session=session,
                            event=event,
                            plugin_root=source.plugin_root,
                            hook_script_root=source.script_root,
                        )
                        result = _make_hook_result(
                            event=event,
                            matcher=matcher,
                            command=command,
                            returncode=completed.returncode,
                            stdout=completed.stdout,
                            stderr=completed.stderr,
                        )
                    results.append(result)
                    if session is not None:
                        session.event(
                            "hook",
                            f"{event} hook: {command}",
                            matcher=matcher,
                            returncode=result.returncode,
                            skipped=result.skipped,
                            stdout=(result.stdout or "")[-2000:],
                            stderr=(result.stderr or "")[-2000:],
                            decision=result.decision,
                            permission_decision=result.permission_decision,
                            updated_input=result.updated_input,
                            source=str(source.path),
                        )
        return results

    def _build_payload(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        session: RuntimeSession | None,
    ) -> dict[str, Any]:
        common: dict[str, Any] = {
            "session_id": session.id if session is not None else "",
            "transcript_path": str(session.events_path if session is not None else runtime_state_path(self.project_root, "transcript.jsonl")),
            "cwd": str(self.project_root),
            "permission_mode": "default",
            "hook_event_name": event,
        }
        common.update(payload)
        if "tool" in common and "tool_name" not in common:
            common["tool_name"] = common["tool"]
        return common

    def is_denied_bash(self, command: str) -> bool:
        return self.permission_decision("Bash", {"command": command}, assume_yes=True).status == "DENY"

    def permission_decision(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        *,
        assume_yes: bool = False,
    ) -> PermissionDecision:
        permissions = self.settings.get("permissions", {})
        for key, status in [("deny", "DENY"), ("ask", "ASK"), ("allow", "ALLOW")]:
            for rule in permissions.get(key, []):
                if not isinstance(rule, str):
                    continue
                expanded = _expand_plugin_root(rule, _plugin_root_for_rule_source(rule, self.sources))
                if _tool_rule_matches(expanded, tool_name, parameters):
                    if status == "ASK" and assume_yes:
                        return PermissionDecision("ALLOW", f"assume-yes approved ask rule: {expanded}", expanded)
                    return PermissionDecision(status, f"matched permission rule: {expanded}", expanded)
        return PermissionDecision("ALLOW", "no blocking permission rule matched", None)

    def _run_command(
        self,
        *,
        command: str,
        payload_text: str,
        timeout: int,
        session: RuntimeSession | None,
        event: str,
        plugin_root: Path | None = None,
        hook_script_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.project_root)
        if session is not None:
            env["CLAUDE_ENV_FILE"] = str(session.path("hook-env.json"))
        if plugin_root is not None:
            env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        parts = command.split()
        if len(parts) >= 2 and parts[0] == "bash" and parts[1].endswith(".sh") and session is not None:
            source = ((hook_script_root or self.project_root) / parts[1]).resolve()
            if source.exists() and source.is_file():
                normalized = source.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
                shim = session.path("hook-shims", f"{event}-{source.name}")
                shim.write_text(normalized, encoding="utf-8", newline="\n")
                payload_path = session.path("hook-payloads", f"{event}-{source.name}.json")
                payload_path.write_text(payload_text + "\n", encoding="utf-8")
                args = ["bash", _bash_path(shim), *parts[2:]]
                try:
                    with payload_path.open("r", encoding="utf-8") as payload_handle:
                        return subprocess.run(
                            args,
                            cwd=str(self.project_root),
                            stdin=payload_handle,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            capture_output=True,
                            timeout=_bash_shim_timeout(timeout),
                            check=False,
                            env=env,
                        )
                except subprocess.TimeoutExpired as exc:
                    return _timeout_completed(args, exc)

        try:
            return subprocess.run(
                command,
                cwd=str(self.project_root),
                input=payload_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return _timeout_completed(command, exc)


def _bash_path(path: Path) -> str:
    text = str(path.resolve())
    if len(text) >= 3 and text[1:3] == ":\\":
        drive = text[0].lower()
        rest = text[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return text.replace("\\", "/")


def _bash_shim_timeout(timeout: int) -> int:
    if os.name != "nt":
        return timeout
    try:
        grace = int(os.environ.get("SKILL_RUNTIME_BASH_HOOK_TIMEOUT_GRACE_SECONDS", "10"))
    except ValueError:
        grace = 10
    return max(timeout, timeout + max(0, grace))


def _timeout_completed(args: Any, exc: subprocess.TimeoutExpired) -> subprocess.CompletedProcess[str]:
    stdout = _timeout_text(exc.stdout)
    stderr = _timeout_text(exc.stderr)
    timeout_message = f"Hook command timed out after {exc.timeout} seconds."
    stderr = f"{stderr}\n{timeout_message}".strip()
    return subprocess.CompletedProcess(args=args, returncode=124, stdout=stdout, stderr=stderr)


def _timeout_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def hook_block_reason(results: Iterable[HookResult], *, assume_yes: bool = False) -> str | None:
    for result in results:
        if result.skipped:
            continue
        message = result.system_message or result.stderr.strip() or result.stdout.strip()
        if result.returncode == 2:
            return message or f"{result.event} hook blocked by exit code 2: {result.command}"
        if result.decision in {"block", "blocked", "deny", "denied"}:
            return message or f"{result.event} hook blocked: {result.command}"
        if result.permission_decision in {"deny", "denied"}:
            return message or f"{result.event} hook denied tool use: {result.command}"
        if result.permission_decision == "ask" and not assume_yes:
            return message or f"{result.event} hook requires approval: {result.command}"
    return None


def hook_updated_input(results: Iterable[HookResult]) -> dict[str, Any]:
    updated: dict[str, Any] = {}
    for result in results:
        if result.updated_input:
            updated.update(result.updated_input)
    return updated


def _make_hook_result(
    *,
    event: str,
    matcher: str,
    command: str,
    returncode: int,
    stdout: str,
    stderr: str,
    skipped: bool = False,
) -> HookResult:
    output = _parse_hook_output(stdout, stderr)
    decision = _normalize_decision(output.get("decision")) if output else None
    system_message = ""
    permission_decision = None
    updated_input = None
    if output:
        if output.get("continue") is False and decision is None:
            decision = "block"
        system_message = str(output.get("systemMessage") or output.get("reason") or "")
        hook_specific = output.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            permission_decision = _normalize_decision(hook_specific.get("permissionDecision"))
            candidate = hook_specific.get("updatedInput")
            if isinstance(candidate, dict):
                updated_input = candidate
        candidate = output.get("updatedInput")
        if isinstance(candidate, dict):
            updated_input = {**(updated_input or {}), **candidate}
        if permission_decision is None:
            permission_decision = _normalize_decision(output.get("permissionDecision"))
    if returncode == 2 and decision is None:
        decision = "block"
    return HookResult(
        event=event,
        matcher=matcher,
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        skipped=skipped,
        output=output,
        decision=decision,
        system_message=system_message,
        permission_decision=permission_decision,
        updated_input=updated_input,
    )


def _parse_hook_output(stdout: str, stderr: str) -> dict[str, Any] | None:
    for text in [stdout, stderr, f"{stdout}\n{stderr}"]:
        parsed = _parse_first_json_object(text)
        if parsed is not None:
            return parsed
    return None


def _parse_first_json_object(text: str) -> dict[str, Any] | None:
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_decision(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _matches(pattern: str, value: str) -> bool:
    if pattern == "":
        return True
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return pattern == value


def _plugin_root_for_settings(path: Path) -> Path | None:
    for parent in [path.parent, *path.parents]:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return parent.resolve()
    return None


def _hook_script_root(path: Path, plugin_root: Path | None, project_root: Path) -> Path:
    if plugin_root is not None:
        return plugin_root.resolve()
    for parent in [path.parent, *path.parents]:
        if parent.name == ".claude":
            return parent.parent.resolve()
    if path.parent.name == "hooks":
        return path.parent.parent.resolve()
    return project_root.resolve()


def _expand_plugin_root(value: str, plugin_root: Path | None) -> str:
    if plugin_root is None:
        return value
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))


def _plugin_root_for_rule_source(rule: str, sources: list[HookSource]) -> Path | None:
    if "CLAUDE_PLUGIN_ROOT" not in rule:
        return None
    for source in sources:
        if source.plugin_root is not None:
            return source.plugin_root
    return None


def _tool_rule_matches(rule: str, tool_name: str, parameters: dict[str, Any]) -> bool:
    rule = rule.strip()
    if not rule:
        return False
    if rule == tool_name:
        return True
    if not rule.startswith(f"{tool_name}("):
        return False
    inner = rule.removeprefix(f"{tool_name}(").removesuffix(")")
    subject = _rule_subject(tool_name, parameters)
    regex = re.escape(inner).replace("\\*", ".*")
    return re.fullmatch(regex, subject) is not None or re.match(regex, subject) is not None


def _rule_subject(tool_name: str, parameters: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(parameters.get("command", ""))
    if tool_name == "WebFetch":
        return str(parameters.get("url", ""))
    if tool_name in {"Read", "Write", "Edit", "MultiEdit"}:
        return str(parameters.get("path", ""))
    if tool_name == "mcp":
        return str(parameters.get("tool", ""))
    return " ".join(f"{key}={value}" for key, value in sorted(parameters.items()))
