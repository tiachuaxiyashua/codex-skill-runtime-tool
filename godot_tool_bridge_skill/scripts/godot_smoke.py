from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Godot import and headless smoke checks.")
    parser.add_argument("--project", required=True, help="Godot project directory containing project.godot.")
    parser.add_argument("--godot", default=None, help="Godot executable or directory.")
    parser.add_argument("--evidence-dir", default=None, help="Directory for stdout/stderr evidence.")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    evidence_dir = Path(args.evidence_dir).resolve() if args.evidence_dir else project / ".godot-smoke-evidence" / datetime.now().strftime("%Y%m%d-%H%M%S")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    executable = find_godot_executable(
        args.godot
        or os.environ.get("GODOT_EXE")
        or os.environ.get("SKILL_RUNTIME_ENV_GODOT_EXE")
        or os.environ.get("SKILL_RUNTIME_GODOT")
    )
    result = {
        "project_dir": str(project),
        "evidence_dir": str(evidence_dir),
        "godot": str(executable) if executable else None,
        "checks": [],
        "status": "FAIL",
    }

    if not (project / "project.godot").exists():
        result["error"] = "project.godot not found"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if executable is None:
        result["error"] = "Godot executable not found"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    checks = [
        ("import", [str(executable), "--headless", "--editor", "--path", str(project), "--quit-after", "5"]),
        ("smoke", [str(executable), "--headless", "--path", str(project), "--quit-after", "5"]),
    ]
    gameplay_script = project / "scripts" / "gameplay_test.gd"
    if gameplay_script.exists():
        checks.append(("gameplay-test", [str(executable), "--headless", "--path", str(project), "--script", "res://scripts/gameplay_test.gd"]))

    max_code = 0
    for label, command in checks:
        check = run_command(label, command, project=project, evidence_dir=evidence_dir, timeout=args.timeout)
        result["checks"].append(check)
        max_code = max(max_code, int(check["returncode"]))

    result["status"] = "PASS" if max_code == 0 else "FAIL"
    result["returncode"] = max_code
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return max_code


def find_godot_executable(godot_hint: str | None) -> Path | None:
    if not godot_hint:
        return None
    hint = Path(godot_hint)
    if hint.is_file():
        return hint.resolve()
    if hint.is_dir():
        console = sorted(hint.glob("*console*.exe"))
        if console:
            return console[0].resolve()
        plain = sorted(hint.glob("Godot*.exe"))
        if plain:
            return plain[0].resolve()
    return None


def run_command(label: str, command: list[str], *, project: Path, evidence_dir: Path, timeout: int) -> dict[str, object]:
    stdout_path = evidence_dir / f"{label}.stdout.txt"
    stderr_path = evidence_dir / f"{label}.stderr.txt"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        completed = subprocess.run(
            command,
            cwd=str(project),
            stdout=stdout,
            stderr=stderr,
            timeout=timeout,
            check=False,
        )
    return {
        "label": label,
        "command": command,
        "returncode": completed.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


if __name__ == "__main__":
    raise SystemExit(main())
