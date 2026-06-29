from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .api_transcript import append_api_message
from .session import RuntimeSession


_TRANSIENT_FAILURE_MARKERS = (
    "bad gateway",
    "connection reset",
    "connection refused",
    "connection timed out",
    "couldn't connect",
    "econnreset",
    "empty reply from server",
    "http 408",
    "http 409",
    "http 425",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "network error",
    "no stream output",
    "overloaded",
    "rate limit",
    "request timeout",
    "service unavailable",
    "stream disconnected",
    "stream ended without a terminal turn event",
    "temporary failure",
    "timed out",
    "too many requests",
    "unexpected status 408",
    "unexpected status 409",
    "unexpected status 425",
    "unexpected status 429",
    "unexpected status 500",
    "unexpected status 502",
    "unexpected status 503",
    "unexpected status 504",
    "upstream_error",
)

_STREAM_FAILURE_MARKERS = (
    "response.failed",
    "turn.failed",
)


@dataclass
class CodexRunResult:
    label: str
    command: list[str]
    returncode: int
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    last_message_path: Path
    raw_returncode: int = 0
    terminal_event: str = ""
    failure_reason: str = ""
    dry_run: bool = False

    @property
    def last_message(self) -> str:
        if self.last_message_path.exists():
            return self.last_message_path.read_text(encoding="utf-8", errors="replace")
        return ""


@dataclass
class _CodexAttemptResult:
    number: int
    command: list[str]
    stdout_path: Path
    stderr_path: Path
    last_message_path: Path
    raw_returncode: int
    returncode: int
    terminal_event: str
    failure_reason: str
    transient: bool


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
        global_args: Iterable[str] | None = None,
        profile: str | None = None,
    ) -> None:
        self.executable = executable
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.add_dirs = [Path(path).resolve() for path in (add_dirs or [])]
        self.env = dict(env or {})
        self.config_overrides = [str(value) for value in (config_overrides or []) if str(value).strip()]
        self.global_args = [str(value) for value in (global_args or []) if str(value).strip()]
        self.profile = profile

    def resolve_executable(self) -> str:
        if Path(self.executable).exists():
            return str(Path(self.executable).resolve())
        search_path = self.env.get("PATH") or os.environ.get("PATH")
        found = shutil.which(self.executable, path=search_path)
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
        stall_timeout_seconds: int | None = None,
        retry_attempts: int | None = None,
        retry_backoff_seconds: float | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> CodexRunResult:
        path_label = _safe_label(label)
        prompt_path = session.write_text(f"{path_label}/prompt.md", prompt)
        stdout_path = session.path(f"{path_label}/stdout.jsonl")
        stderr_path = session.path(f"{path_label}/stderr.txt")
        last_message_path = session.path(f"{path_label}/last-message.md")

        executable = self.resolve_executable()

        def build_command(output_last_message: Path) -> list[str]:
            command = [
                executable,
                "--sandbox",
                self.sandbox,
                "--ask-for-approval",
                self.approval,
            ]
            command.extend(self.global_args)
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
                    str(output_last_message),
                ]
            )
            if output_schema is not None:
                command.extend(["--output-schema", str(output_schema)])
            for directory in self.add_dirs:
                command.extend(["--add-dir", str(directory)])
            command.append("-")
            return command

        command = build_command(last_message_path)

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
                raw_returncode=0,
                terminal_event="dry_run",
                failure_reason="",
                dry_run=True,
            )

        effective_timeout = timeout_seconds if timeout_seconds is not None else _default_timeout_seconds(self.env)
        stall_timeout = stall_timeout_seconds if stall_timeout_seconds is not None else _default_stall_timeout_seconds(self.env)
        max_attempts = max(1, retry_attempts) if retry_attempts is not None else _default_retry_attempts(self.env)
        retry_backoff = retry_backoff_seconds if retry_backoff_seconds is not None else _default_retry_backoff_seconds(self.env)
        child_env = _child_env(self.env)
        attempts: list[_CodexAttemptResult] = []
        final_attempt: _CodexAttemptResult | None = None

        for attempt_number in range(1, max_attempts + 1):
            attempt_label = f"{path_label}/attempts/attempt-{attempt_number}"
            attempt_stdout_path = session.path(f"{attempt_label}/stdout.jsonl")
            attempt_stderr_path = session.path(f"{attempt_label}/stderr.txt")
            attempt_last_message_path = session.path(f"{attempt_label}/last-message.md")
            attempt_command = build_command(attempt_last_message_path)
            session.event(
                "codex.attempt_start",
                f"Starting Codex run attempt {attempt_number}/{max_attempts}: {label}",
                label=label,
                attempt=attempt_number,
                max_attempts=max_attempts,
                timeout_seconds=effective_timeout,
                stall_timeout_seconds=stall_timeout,
                command=_redacted_command(attempt_command),
            )
            completed = _run_codex_subprocess(
                command=attempt_command,
                cwd=workdir,
                prompt=prompt,
                stdout_path=attempt_stdout_path,
                stderr_path=attempt_stderr_path,
                timeout_seconds=effective_timeout,
                stall_timeout_seconds=stall_timeout,
                env=child_env,
            )
            effective_returncode, terminal_event, failure_reason = _analyze_codex_run(
                stdout_path=attempt_stdout_path,
                stderr_path=attempt_stderr_path,
                raw_returncode=completed.returncode,
            )
            transient = _is_transient_codex_failure(
                stdout_path=attempt_stdout_path,
                stderr_path=attempt_stderr_path,
                raw_returncode=completed.returncode,
                effective_returncode=effective_returncode,
                terminal_event=terminal_event,
                failure_reason=failure_reason,
            )
            attempt = _CodexAttemptResult(
                number=attempt_number,
                command=attempt_command,
                stdout_path=attempt_stdout_path,
                stderr_path=attempt_stderr_path,
                last_message_path=attempt_last_message_path,
                raw_returncode=completed.returncode,
                returncode=effective_returncode,
                terminal_event=terminal_event,
                failure_reason=failure_reason,
                transient=transient,
            )
            attempts.append(attempt)
            session.write_json(
                f"{attempt_label}/result.json",
                {
                    "label": label,
                    "attempt": attempt_number,
                    "max_attempts": max_attempts,
                    "returncode": effective_returncode,
                    "raw_returncode": completed.returncode,
                    "terminal_event": terminal_event,
                    "failure_reason": failure_reason,
                    "transient": transient,
                    "stdout": str(attempt_stdout_path),
                    "stderr": str(attempt_stderr_path),
                    "last_message": str(attempt_last_message_path),
                    "command": _redacted_command(attempt_command),
                },
            )
            session.event(
                "codex.attempt_finish",
                f"Codex run attempt {attempt_number}/{max_attempts} finished: {label}",
                label=label,
                attempt=attempt_number,
                max_attempts=max_attempts,
                returncode=effective_returncode,
                raw_returncode=completed.returncode,
                terminal_event=terminal_event,
                failure_reason=failure_reason,
                transient=transient,
                stdout=str(attempt_stdout_path),
                stderr=str(attempt_stderr_path),
                last_message=str(attempt_last_message_path),
            )

            final_attempt = attempt
            if effective_returncode == 0:
                break
            if attempt_number >= max_attempts or not transient:
                break
            session.event(
                "codex.retry",
                f"Retrying transient Codex run failure: {label}",
                label=label,
                next_attempt=attempt_number + 1,
                max_attempts=max_attempts,
                returncode=effective_returncode,
                raw_returncode=completed.returncode,
                terminal_event=terminal_event,
                failure_reason=failure_reason,
                backoff_seconds=retry_backoff,
            )
            if retry_backoff > 0:
                time.sleep(retry_backoff)

        if final_attempt is None:
            raise RuntimeError("Codex run did not produce an attempt result")
        _copy_attempt_outputs(final_attempt, stdout_path=stdout_path, stderr_path=stderr_path, last_message_path=last_message_path)

        effective_returncode = final_attempt.returncode
        terminal_event = final_attempt.terminal_event
        failure_reason = final_attempt.failure_reason
        raw_returncode = final_attempt.raw_returncode
        if effective_returncode != raw_returncode:
            session.event(
                "codex.failure",
                f"Codex run reported failure in stream despite exit code {raw_returncode}",
                terminal_event=terminal_event,
                failure_reason=failure_reason,
                raw_returncode=raw_returncode,
                effective_returncode=effective_returncode,
                attempts=len(attempts),
                stdout=str(stdout_path),
                stderr=str(stderr_path),
            )

        session.event(
            "codex.finish",
            f"Codex run finished: {label}",
            returncode=effective_returncode,
            raw_returncode=raw_returncode,
            terminal_event=terminal_event,
            failure_reason=failure_reason,
            attempts=len(attempts),
            stdout=str(stdout_path),
            stderr=str(stderr_path),
            last_message=str(last_message_path),
        )
        session.transcript_event(
            "transcript.assistant",
            f"Assistant output captured for {label}",
            label=label,
            returncode=effective_returncode,
            raw_returncode=raw_returncode,
            terminal_event=terminal_event,
            failure_reason=failure_reason,
            attempts=len(attempts),
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
            metadata={
                "returncode": effective_returncode,
                "raw_returncode": raw_returncode,
                "terminal_event": terminal_event,
                "failure_reason": failure_reason,
                "attempts": len(attempts),
            },
        )
        return CodexRunResult(
            label=label,
            command=command,
            returncode=effective_returncode,
            prompt_path=prompt_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            last_message_path=last_message_path,
            raw_returncode=raw_returncode,
            terminal_event=terminal_event,
            failure_reason=failure_reason,
        )


def _redacted_env(values: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if _looks_secret(key):
            result[key] = "[REDACTED]"
        else:
            result[key] = value
    return result


def _child_env(values: dict[str, str]) -> dict[str, str] | None:
    if not values:
        return None
    child_env = dict(os.environ)
    child_env.update(values)
    return child_env


def _default_timeout_seconds(values: dict[str, str] | None = None) -> int | None:
    raw = _env_value("SKILL_RUNTIME_CODEX_TIMEOUT_SECONDS", values)
    if raw is None or raw.strip() == "":
        return 300
    try:
        value = int(raw)
    except ValueError:
        return 300
    return value if value > 0 else None


def _default_stall_timeout_seconds(values: dict[str, str] | None = None) -> int | None:
    raw = _env_value("SKILL_RUNTIME_CODEX_STALL_TIMEOUT_SECONDS", values)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _default_retry_attempts(values: dict[str, str] | None = None) -> int:
    raw = _env_value("SKILL_RUNTIME_CODEX_RETRY_ATTEMPTS", values)
    if raw is None or raw.strip() == "":
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(1, value)


def _default_retry_backoff_seconds(values: dict[str, str] | None = None) -> float:
    raw = _env_value("SKILL_RUNTIME_CODEX_RETRY_BACKOFF_SECONDS", values)
    if raw is None or raw.strip() == "":
        return 2.0
    try:
        value = float(raw)
    except ValueError:
        return 2.0
    return max(0.0, value)


def _env_value(key: str, values: dict[str, str] | None = None) -> str | None:
    if values and key in values:
        return values[key]
    return os.environ.get(key)


def _safe_label(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return safe.strip("-") or "codex-run"


def _run_codex_subprocess(
    *,
    command: list[str],
    cwd: Path,
    prompt: str,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int | None,
    stall_timeout_seconds: int | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess:
    started_at = time.monotonic()
    last_output_at = [started_at]
    stopped_reason = [""]
    stdin_error = [""]

    def pump(source, target) -> None:
        while True:
            chunk = source.read(8192)
            if not chunk:
                break
            target.write(chunk)
            target.flush()
            last_output_at[0] = time.monotonic()

    def write_stdin(process: subprocess.Popen[bytes]) -> None:
        try:
            if process.stdin is not None:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
        except BrokenPipeError:
            pass
        except OSError as exc:
            stdin_error[0] = str(exc)

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **_codex_process_group_kwargs(),
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_thread = threading.Thread(target=pump, args=(process.stdout, stdout), daemon=True)
        stderr_thread = threading.Thread(target=pump, args=(process.stderr, stderr), daemon=True)
        stdin_thread = threading.Thread(target=write_stdin, args=(process,), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        stdin_thread.start()

        while process.poll() is None:
            now = time.monotonic()
            if timeout_seconds is not None and now - started_at > timeout_seconds:
                stopped_reason[0] = f"Codex CLI timed out after {timeout_seconds} seconds."
                _terminate_codex_process_group(process)
                break
            if stall_timeout_seconds is not None and now - last_output_at[0] > stall_timeout_seconds:
                stopped_reason[0] = f"Codex CLI produced no stream output for {stall_timeout_seconds} seconds."
                _terminate_codex_process_group(process)
                break
            time.sleep(0.2)

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate_codex_process_group(process, force=True)
            process.wait(timeout=5)

        stdin_thread.join(timeout=5)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdin_error[0]:
            stderr.write((f"\nCodex CLI stdin writer failed: {stdin_error[0]}\n").encode("utf-8"))
            stderr.flush()
        if stopped_reason[0]:
            stderr.write(("\n" + stopped_reason[0] + "\n").encode("utf-8"))
            stderr.flush()
            returncode = 125 if "no stream output" in stopped_reason[0] else 124
        else:
            returncode = process.returncode if process.returncode is not None else 1

    return subprocess.CompletedProcess(command, returncode)


def _codex_process_group_kwargs() -> dict[str, object]:
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {}


def _terminate_codex_process_group(process: subprocess.Popen[bytes], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    elif os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F" if force else ""],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except OSError:
            pass
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except OSError:
        pass


def _copy_attempt_outputs(
    attempt: _CodexAttemptResult,
    *,
    stdout_path: Path,
    stderr_path: Path,
    last_message_path: Path,
) -> None:
    _copy_or_empty(attempt.stdout_path, stdout_path)
    _copy_or_empty(attempt.stderr_path, stderr_path)
    _copy_or_empty(attempt.last_message_path, last_message_path)


def _copy_or_empty(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copyfile(source, target)
    else:
        target.write_text("", encoding="utf-8")


def _analyze_codex_run(*, stdout_path: Path, stderr_path: Path, raw_returncode: int) -> tuple[int, str, str]:
    terminal_event, terminal_payload = _terminal_event_from_stdout(stdout_path)
    failure_reason = _failure_reason_from_payload(terminal_payload)

    effective_returncode = raw_returncode
    if terminal_event in {"turn.failed", "turn.cancelled", "response.failed"} and raw_returncode == 0:
        effective_returncode = 2
    if raw_returncode != 0 and not failure_reason:
        failure_reason = _tail_text(stderr_path) or _tail_text(stdout_path)
    if effective_returncode == 0 and terminal_event == "" and (_contains_failure_markers(stderr_path) or _contains_failure_markers(stdout_path)):
        effective_returncode = 2
        if not failure_reason:
            failure_reason = _tail_text(stderr_path) or _tail_text(stdout_path)
    if effective_returncode == 0 and terminal_event == "" and _contains_marker(stdout_path, "turn.started"):
        effective_returncode = 2
        if not failure_reason:
            failure_reason = "Codex stream ended without a terminal turn event after turn.started."
    return effective_returncode, terminal_event, failure_reason


def _terminal_event_from_stdout(stdout_path: Path) -> tuple[str, dict[str, object] | None]:
    if not stdout_path.exists():
        return "", None
    try:
        lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", None
    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        event = _parse_json_object(line)
        if event is None:
            if "turn.failed" in line:
                return "turn.failed", None
            if "turn.completed" in line:
                return "turn.completed", None
            continue
        event_type = str(event.get("type") or "")
        if event_type in {"turn.completed", "turn.failed", "turn.cancelled", "response.failed"}:
            return event_type, event
    return "", None


def _parse_json_object(line: str) -> dict[str, object] | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _failure_reason_from_payload(payload: dict[str, object] | None) -> str:
    if not payload:
        return ""
    for key in ("message", "reason", "summary", "detail", "details"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "reason", "summary", "detail", "details"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    return ""


def _tail_text(path: Path, *, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()


def _contains_failure_markers(path: Path) -> bool:
    text = _tail_text(path, limit=12000)
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _TRANSIENT_FAILURE_MARKERS + _STREAM_FAILURE_MARKERS)


def _contains_marker(path: Path, marker: str) -> bool:
    text = _tail_text(path, limit=12000)
    return marker.lower() in text.lower() if text else False


def _is_transient_codex_failure(
    *,
    stdout_path: Path,
    stderr_path: Path,
    raw_returncode: int,
    effective_returncode: int,
    terminal_event: str,
    failure_reason: str,
) -> bool:
    if effective_returncode == 0:
        return False
    if terminal_event == "turn.cancelled":
        return False
    if raw_returncode in {124, 125}:
        return True
    text = "\n".join(
        part
        for part in (
            failure_reason,
            _tail_text(stdout_path, limit=12000),
            _tail_text(stderr_path, limit=12000),
        )
        if part
    ).lower()
    if not text:
        return False
    return any(marker in text for marker in _TRANSIENT_FAILURE_MARKERS)


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
