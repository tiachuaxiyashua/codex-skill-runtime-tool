from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .api_transcript import append_api_message
from .session import RuntimeSession


@dataclass
class CodexRunResult:
    label: str
    command: list[str]
    returncode: int
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    last_message_path: Path
    dry_run: bool = False

    @property
    def last_message(self) -> str:
        if self.last_message_path.exists():
            return self.last_message_path.read_text(encoding="utf-8", errors="replace")
        return ""


class CodexCLI:
    def __init__(
        self,
        executable: str = "codex",
        model: str | None = None,
        sandbox: str = "danger-full-access",
        approval: str = "never",
        add_dirs: Iterable[Path] | None = None,
        env: dict[str, str] | None = None,
        config_overrides: Iterable[str] | None = None,
        profile: str | None = None,
    ) -> None:
        self.executable = executable
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.add_dirs = [Path(path).resolve() for path in (add_dirs or [])]
        self.env = dict(env or {})
        self.config_overrides = [str(value) for value in (config_overrides or []) if str(value).strip()]
        self.profile = profile

    def resolve_executable(self) -> str:
        if Path(self.executable).exists():
            return str(Path(self.executable).resolve())
        found = shutil.which(self.executable)
        if found:
            return found
        raise FileNotFoundError(f"Codex CLI not found: {self.executable}")

    def exec_prompt(
        self,
        *,
        session: RuntimeSession,
        label: str,
        workdir: Path,
        prompt: str,
        output_schema: Path | None = None,
        dry_run: bool = False,
        timeout_seconds: int | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> CodexRunResult:
        path_label = _safe_label(label)
        prompt_path = session.write_text(f"{path_label}/prompt.md", prompt)
        stdout_path = session.path(f"{path_label}/stdout.jsonl")
        stderr_path = session.path(f"{path_label}/stderr.txt")
        last_message_path = session.path(f"{path_label}/last-message.md")

        executable = self.resolve_executable()
        command = [
            executable,
            "--sandbox",
            self.sandbox,
            "--ask-for-approval",
            self.approval,
        ]
        if self.profile:
            command.extend(["--profile", self.profile])
        for override in self.config_overrides:
            command.extend(["--config", override])
        effective_model = model or self.model
        if effective_model:
            command.extend(["--model", effective_model])
        if reasoning_effort:
            command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
        command.extend(
            [
                "exec",
                "--json",
                "--cd",
                str(workdir),
                "--skip-git-repo-check",
                "--output-last-message",
                str(last_message_path),
            ]
        )
        if output_schema is not None:
            command.extend(["--output-schema", str(output_schema)])
        for directory in self.add_dirs:
            command.extend(["--add-dir", str(directory)])
        command.append("-")

        session.event("codex.prepare", f"Prepared Codex run: {label}", command=_redacted_command(command))
        session.transcript_event(
            "transcript.user",
            f"Prompt prepared for {label}",
            label=label,
            prompt_path=str(prompt_path),
            workdir=str(workdir),
        )
        append_api_message(
            session,
            role="user",
            label=label,
            content=prompt,
            source_path=prompt_path,
            metadata={"workdir": str(workdir), "output_schema": str(output_schema) if output_schema else ""},
        )

        if dry_run:
            session.write_json(
                f"{path_label}/dry-run-command.json",
                {
                    "command": _redacted_command(command),
                    "workdir": str(workdir),
                    "prompt": str(prompt_path),
                    "last_message": str(last_message_path),
                    "env": _redacted_env(self.env),
                },
            )
            session.event("codex.dry_run", f"Dry-run skipped Codex run: {label}")
            session.transcript_event(
                "transcript.assistant",
                f"Dry-run placeholder for {label}",
                label=label,
                last_message_path=str(last_message_path),
                dry_run=True,
            )
            append_api_message(
                session,
                role="assistant",
                label=label,
                content="",
                source_path=last_message_path,
                metadata={"dry_run": True},
            )
            return CodexRunResult(
                label=label,
                command=command,
                returncode=0,
                prompt_path=prompt_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                last_message_path=last_message_path,
                dry_run=True,
            )

        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            child_env = None
            if self.env:
                import os

                child_env = dict(os.environ)
                child_env.update(self.env)
            completed = subprocess.run(
                command,
                cwd=str(workdir),
                input=prompt.encode("utf-8"),
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
                env=child_env,
            )

        session.event(
            "codex.finish",
            f"Codex run finished: {label}",
            returncode=completed.returncode,
            stdout=str(stdout_path),
            stderr=str(stderr_path),
            last_message=str(last_message_path),
        )
        session.transcript_event(
            "transcript.assistant",
            f"Assistant output captured for {label}",
            label=label,
            returncode=completed.returncode,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            last_message_path=str(last_message_path),
            preview=last_message_path.read_text(encoding="utf-8", errors="replace")[:4000]
            if last_message_path.exists()
            else "",
        )
        append_api_message(
            session,
            role="assistant",
            label=label,
            content=last_message_path.read_text(encoding="utf-8", errors="replace") if last_message_path.exists() else "",
            source_path=last_message_path,
            metadata={"returncode": completed.returncode},
        )
        return CodexRunResult(
            label=label,
            command=command,
            returncode=completed.returncode,
            prompt_path=prompt_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            last_message_path=last_message_path,
        )


def _redacted_env(values: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if _looks_secret(key):
            result[key] = "[REDACTED]"
        else:
            result[key] = value
    return result


def _safe_label(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return safe.strip("-") or "codex-run"


def _redacted_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for value in command:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        redacted.append(_redact_config_value(value))
        if value in {"--api-key", "--token", "--auth-token"}:
            redact_next = True
    return redacted


def _redact_config_value(value: str) -> str:
    lowered = value.lower()
    if not any(part in lowered for part in ["api_key", "apikey", "token", "secret", "password", "authorization", "credential"]):
        return value
    if "=" not in value:
        return "[REDACTED]"
    key, _raw = value.split("=", 1)
    return f"{key}=[REDACTED]"


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ["key", "token", "secret", "password", "credential", "auth"])
