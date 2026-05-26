from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class GodotAudioSmokeError(RuntimeError):
    pass


def run_godot_audio_import_smoke(
    *,
    godot_exe: Path,
    godot_project_dir: Path,
    asset_paths: list[str],
    timeout: int = 120,
) -> dict[str, Any]:
    godot_exe = godot_exe.expanduser().resolve()
    godot_project_dir = godot_project_dir.expanduser().resolve()
    if not godot_exe.exists():
        raise GodotAudioSmokeError(f"Godot executable does not exist: {godot_exe}")
    if not godot_project_dir.exists():
        raise GodotAudioSmokeError(f"Godot project directory does not exist: {godot_project_dir}")
    if not (godot_project_dir / "project.godot").exists():
        raise GodotAudioSmokeError(f"project.godot not found in: {godot_project_dir}")
    resources = [_to_res_path(item, godot_project_dir=godot_project_dir) for item in asset_paths]
    if not resources:
        raise GodotAudioSmokeError("at least one audio asset path is required")

    script_path = godot_project_dir / "__audio_import_smoke.gd"
    script_path.write_text(_smoke_script(resources), encoding="utf-8")
    import_result = _run(
        [str(godot_exe), "--headless", "--path", str(godot_project_dir), "--import"],
        timeout=timeout,
    )
    smoke_result = _run(
        [str(godot_exe), "--headless", "--path", str(godot_project_dir), "--script", "res://__audio_import_smoke.gd"],
        timeout=timeout,
    )
    ok = import_result.returncode == 0 and smoke_result.returncode == 0 and "AUDIO_IMPORT_SMOKE_OK" in smoke_result.stdout
    return {
        "ok": ok,
        "godot_exe": str(godot_exe),
        "godot_project_dir": str(godot_project_dir),
        "resources": resources,
        "import": _completed_to_dict(import_result),
        "smoke": _completed_to_dict(smoke_result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a headless Godot audio import smoke test.")
    parser.add_argument("--godot-exe", default=os.environ.get("GODOT_EXE", ""))
    parser.add_argument("--godot-project", required=True)
    parser.add_argument("--asset", action="append", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if not args.godot_exe:
        print(json.dumps({"ok": False, "message": "--godot-exe or GODOT_EXE is required"}, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        result = run_godot_audio_import_smoke(
            godot_exe=Path(args.godot_exe),
            godot_project_dir=Path(args.godot_project),
            asset_paths=list(args.asset),
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


def _to_res_path(value: str, *, godot_project_dir: Path) -> str:
    value = value.strip()
    if value.startswith("res://"):
        return value
    path = Path(value)
    candidate = path if path.is_absolute() else godot_project_dir / path
    candidate = candidate.resolve()
    try:
        relative = candidate.relative_to(godot_project_dir)
    except ValueError as exc:
        raise GodotAudioSmokeError(f"asset must be inside the Godot project: {candidate}") from exc
    return "res://" + relative.as_posix()


def _smoke_script(resources: list[str]) -> str:
    lines = [
        "extends SceneTree",
        "",
        "func _init() -> void:",
        "\tvar audio_resources := PackedStringArray(" + json.dumps(resources, ensure_ascii=False) + ")",
        "\tfor resource_path in audio_resources:",
        "\t\tvar stream := load(resource_path)",
        "\t\tif stream == null:",
        "\t\t\tpush_error(\"AUDIO_IMPORT_SMOKE_FAIL stream_load_null \" + resource_path)",
        "\t\t\tquit(1)",
        "\t\t\treturn",
        "\t\tif not stream is AudioStream:",
        "\t\t\tpush_error(\"AUDIO_IMPORT_SMOKE_FAIL not_audio_stream \" + resource_path)",
        "\t\t\tquit(1)",
        "\t\t\treturn",
        "\tprint(\"AUDIO_IMPORT_SMOKE_OK count=%d\" % audio_resources.size())",
        "\tquit(0)",
        "",
    ]
    return "\n".join(lines)


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _completed_to_dict(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


if __name__ == "__main__":
    raise SystemExit(main())
