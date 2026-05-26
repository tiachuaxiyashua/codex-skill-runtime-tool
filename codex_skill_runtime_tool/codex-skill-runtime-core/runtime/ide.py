from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path


@dataclass(frozen=True)
class IDESelection:
    file_path: str
    text: str
    start_line: int | None = None
    end_line: int | None = None


def write_ide_selection(project_root: Path, selection: IDESelection) -> Path:
    path = runtime_state_path(project_root, "ide", "selection.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "file_path": selection.file_path,
                "text": selection.text,
                "start_line": selection.start_line,
                "end_line": selection.end_line,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def write_ide_diagnostics(project_root: Path, diagnostics: list[dict[str, Any]]) -> Path:
    path = runtime_state_path(project_root, "ide", "diagnostics.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ide_context(project_root: Path, *, max_chars: int = 20000) -> str:
    root = runtime_state_path(project_root, "ide")
    sections: list[str] = []
    selection = _load_json(root / "selection.json")
    if isinstance(selection, dict):
        text = str(selection.get("text") or "")
        sections.append(
            "### Active IDE Selection\n"
            f"- File: `{selection.get('file_path', '')}`\n"
            f"- Range: {selection.get('start_line', '')}-{selection.get('end_line', '')}\n\n"
            "```text\n"
            f"{text[:8000]}\n"
            "```"
        )
    diagnostics = _load_json(root / "diagnostics.json")
    if isinstance(diagnostics, list) and diagnostics:
        rows = []
        for item in diagnostics[:80]:
            if not isinstance(item, dict):
                continue
            rows.append(
                f"- `{item.get('file', item.get('file_path', ''))}`:{item.get('line', '')} "
                f"{item.get('severity', '')} {item.get('message', '')}"
            )
        if rows:
            sections.append("### IDE/LSP Diagnostics\n" + "\n".join(rows))
    if not sections:
        return ""
    return ("## Runtime IDE Context\n\n" + "\n\n".join(sections))[:max_chars]


def run_lsp_command(command: list[str], *, project_root: Path, timeout: int = 30) -> dict[str, Any]:
    if not command:
        return {"status": "BLOCKED", "message": "No LSP command configured."}
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {"status": "BLOCKED", "message": "LSP command timed out", "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    return {
        "status": "OK" if completed.returncode == 0 else "ERROR",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
