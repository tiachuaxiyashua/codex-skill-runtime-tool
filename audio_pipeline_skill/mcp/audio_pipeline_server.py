from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from audiogen_cli import (  # noqa: E402
    DEFAULT_BASE_URL,
    check_backend,
    check_models,
    doctor,
    record_listening_review,
    validate_audio_pack,
)
from godot_audio_smoke import run_godot_audio_import_smoke  # noqa: E402


TOOLS = [
    {
        "name": "check_backend",
        "description": "Check whether local Stability Matrix ComfyUI API is reachable and has nodes for requested audio pipelines (ACE-Step, QwenTTS, or MMAudio).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "pipelines": {"type": "array", "items": {"type": "string"}}
            }
        },
    },
    {
        "name": "check_models",
        "description": "Check whether model files for requested local audio pipelines are installed for ComfyUI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "comfyui_root": {"type": "string"},
                "pipelines": {"type": "array", "items": {"type": "string"}}
            }
        },
    },
    {
        "name": "doctor",
        "description": "Run one combined Stability Matrix ComfyUI setup diagnostic for requested audio pipelines: API reachability, required nodes, required model files, and next steps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "comfyui_root": {"type": "string"},
                "pipelines": {"type": "array", "items": {"type": "string"}}
            }
        },
    },
    {
        "name": "generate_audio_pack",
        "description": "Generate a game audio pack from audio_request.json through ComfyUI, postprocess output, and write audio_manifest.json.",
        "inputSchema": {
            "type": "object",
            "required": ["spec_path", "output_dir", "manifest_path"],
            "properties": {
                "spec_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "style_profile_path": {"type": "string"},
                "base_url": {"type": "string"},
                "comfyui_root": {"type": "string"},
                "timeout": {"type": "integer"}
            }
        },
    },
    {
        "name": "validate_audio_pack",
        "description": "Validate a generated audio_manifest.json and write a validation report next to it.",
        "inputSchema": {
            "type": "object",
            "required": ["manifest_path"],
            "properties": {
                "manifest_path": {"type": "string"}
            }
        },
    },
    {
        "name": "record_listening_review",
        "description": "Record an approved or rejected listening QA decision after hearing generated audio output.",
        "inputSchema": {
            "type": "object",
            "required": ["manifest_path", "decision", "reviewer"],
            "properties": {
                "manifest_path": {"type": "string"},
                "decision": {"type": "string", "enum": ["approved", "rejected"]},
                "reviewer": {"type": "string"},
                "notes": {"type": "string"}
            }
        },
    },
    {
        "name": "godot_audio_import_smoke",
        "description": "Run a headless Godot import smoke test for generated audio resources. Requires godot_exe or GODOT_EXE.",
        "inputSchema": {
            "type": "object",
            "required": ["godot_project_dir", "asset_paths"],
            "properties": {
                "godot_exe": {"type": "string"},
                "godot_project_dir": {"type": "string"},
                "asset_paths": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "integer"}
            }
        },
    },
]


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            response = _handle_request(request)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": _safe_request_id(raw),
                "error": {"code": -32603, "message": str(exc), "data": traceback.format_exc(limit=4)},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "audio-pipeline", "version": "0.2.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "tools/call params must be an object")
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "tools/call arguments must be an object")
        result = _call_tool(name, arguments)
        return _result(request_id, result)
    return _error(request_id, -32601, f"unknown method: {method}")


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    base_url = str(arguments.get("base_url") or os.environ.get("COMFYUI_BASE_URL") or DEFAULT_BASE_URL)
    comfyui_root = Path(str(arguments.get("comfyui_root") or os.environ.get("COMFYUI_ROOT") or "")).resolve() if (arguments.get("comfyui_root") or os.environ.get("COMFYUI_ROOT")) else None
    pipelines = arguments.get("pipelines") if isinstance(arguments.get("pipelines"), list) else None
    try:
        if name == "check_backend":
            data = check_backend(base_url=base_url, pipelines=[str(item) for item in pipelines] if pipelines else None).to_dict()
            return _tool_result(data, status="ok" if data.get("ok") else "error")
        if name == "check_models":
            data = check_models(base_url=base_url, comfyui_root=comfyui_root, pipelines=[str(item) for item in pipelines] if pipelines else None)
            return _tool_result(data, status="ok" if data.get("ok") else "error")
        if name == "doctor":
            data = doctor(base_url=base_url, comfyui_root=comfyui_root, pipelines=[str(item) for item in pipelines] if pipelines else None)
            return _tool_result(data, status="ok" if data.get("ok") else "error")
        if name == "generate_audio_pack":
            manifest_path = Path(str(arguments["manifest_path"]))
            command = [
                sys.executable,
                str(SCRIPTS_DIR / "audiogen_cli.py"),
                "--project-root",
                str(project_root),
                "--base-url",
                base_url,
            ]
            if comfyui_root is not None:
                command.extend(["--comfyui-root", str(comfyui_root)])
            command.extend(
                [
                    "generate",
                    "--spec",
                    str(arguments["spec_path"]),
                    "--out",
                    str(arguments["output_dir"]),
                    "--manifest",
                    str(manifest_path),
                    "--timeout",
                    str(int(arguments.get("timeout") or 900)),
                ]
            )
            if arguments.get("style_profile_path"):
                command.extend(["--style-profile", str(arguments["style_profile_path"])])
            timeout = int(arguments.get("timeout") or 900) + 60
            completed = subprocess.run(command, cwd=str(project_root), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
            if completed.returncode != 0:
                return _tool_result(
                    {
                        "message": "audiogen_cli generate failed",
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                    },
                    status="error",
                )
            resolved_manifest = manifest_path if manifest_path.is_absolute() else project_root / manifest_path
            full_manifest = json.loads(resolved_manifest.read_text(encoding="utf-8-sig"))
            status = "ok" if full_manifest.get("validation", {}).get("status") == "passed" else "error"
            return _tool_result(_summarize_manifest(full_manifest), status=status)
        if name == "validate_audio_pack":
            data = validate_audio_pack(
                manifest_path=Path(str(arguments["manifest_path"])),
                project_root=project_root,
            )
            status = "ok" if data.get("validation", {}).get("status") == "passed" else "error"
            return _tool_result(data, status=status)
        if name == "record_listening_review":
            data = record_listening_review(
                manifest_path=Path(str(arguments["manifest_path"])),
                decision=str(arguments["decision"]),
                reviewer=str(arguments["reviewer"]),
                notes=str(arguments.get("notes") or ""),
                project_root=project_root,
            )
            return _tool_result(data, status="ok")
        if name == "godot_audio_import_smoke":
            godot_exe_value = str(arguments.get("godot_exe") or os.environ.get("GODOT_EXE") or "")
            if not godot_exe_value:
                return _tool_result({"message": "godot_exe argument or GODOT_EXE environment variable is required"}, status="error")
            asset_paths = arguments.get("asset_paths")
            if not isinstance(asset_paths, list):
                return _tool_result({"message": "asset_paths must be an array"}, status="error")
            data = run_godot_audio_import_smoke(
                godot_exe=Path(godot_exe_value),
                godot_project_dir=Path(str(arguments["godot_project_dir"])),
                asset_paths=[str(item) for item in asset_paths],
                timeout=int(arguments.get("timeout") or 120),
            )
            return _tool_result(data, status="ok" if data.get("ok") else "error")
        return _tool_result({"message": f"unsupported tool: {name}"}, status="unsupported")
    except Exception as exc:
        return _tool_result({"message": str(exc), "traceback": traceback.format_exc(limit=6)}, status="error")


def _tool_result(data: dict[str, Any], *, status: str) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return {
        "status": status,
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
    }


def _summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": manifest.get("project", ""),
        "style_profile_id": manifest.get("style_profile_id", ""),
        "style_hash": manifest.get("style_hash", ""),
        "backend": manifest.get("backend", ""),
        "base_url": manifest.get("base_url", ""),
        "manifest_path": manifest.get("manifest_path", ""),
        "validation_report_path": manifest.get("validation_report_path", ""),
        "validation": manifest.get("validation", {}),
        "listening_review": manifest.get("listening_review", {}),
        "ready_for_integration": bool(manifest.get("ready_for_integration", False)),
        "assets": [
            {
                "id": asset.get("id", ""),
                "pipeline": asset.get("pipeline", ""),
                "type": asset.get("type", ""),
                "path": asset.get("path", ""),
                "raw_path": asset.get("raw_path", ""),
                "format": asset.get("format", ""),
                "target_duration_seconds": asset.get("target_duration_seconds"),
                "loop": asset.get("loop"),
                "validation": asset.get("validation", {}),
            }
            for asset in manifest.get("assets", [])
            if isinstance(asset, dict)
        ],
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _safe_request_id(raw: str) -> Any:
    try:
        data = json.loads(raw)
        return data.get("id")
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
