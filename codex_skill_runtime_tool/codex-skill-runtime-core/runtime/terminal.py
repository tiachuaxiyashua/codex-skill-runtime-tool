from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TerminalResult:
    command: list[str] | str
    returncode: int
    stdout: str
    stderr: str
    cwd: str


def run_powershell(command: str, *, cwd: Path, timeout: int = 120) -> TerminalResult:
    executable = os.environ.get("SKILL_RUNTIME_POWERSHELL") or _default_powershell()
    completed = subprocess.run(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return TerminalResult(
        command=[executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        cwd=str(cwd),
    )


def run_terminal_capture(
    command: str | list[str],
    *,
    cwd: Path,
    shell: str = "auto",
    timeout: int = 120,
) -> TerminalResult:
    if isinstance(command, list):
        cmd = [str(item) for item in command]
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return TerminalResult(command=cmd, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr, cwd=str(cwd))
    if shell.lower() in {"powershell", "pwsh"}:
        return run_powershell(command, cwd=cwd, timeout=timeout)
    if shell.lower() == "none":
        return run_terminal_capture(shlex.split(command), cwd=cwd, shell="auto", timeout=timeout)
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return TerminalResult(command=command, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr, cwd=str(cwd))


def run_python_repl(code_text: str, *, cwd: Path, globals_path: Path | None = None) -> TerminalResult:
    namespace: dict[str, Any] = {"__name__": "__runtime_repl__"}
    if globals_path is not None and globals_path.exists():
        try:
            namespace.update(json.loads(globals_path.read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError):
            pass
    stdout = io.StringIO()
    stderr = io.StringIO()
    old_cwd = Path.cwd()
    returncode = 0
    try:
        os.chdir(cwd)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code_text, namespace)
    except Exception as exc:
        stderr.write(f"{type(exc).__name__}: {exc}\n")
        returncode = 1
    finally:
        os.chdir(old_cwd)
    return TerminalResult(command="python-repl", returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue(), cwd=str(cwd))


def persist_terminal_capture(session_dir: Path, *, name: str, result: TerminalResult) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-") or "capture"
    path = session_dir / "terminal-captures" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _default_powershell() -> str:
    for candidate in ["pwsh", "powershell"]:
        import shutil

        found = shutil.which(candidate)
        if found:
            return found
    return "powershell"
