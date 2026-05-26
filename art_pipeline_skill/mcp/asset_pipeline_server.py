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

from assetgen_cli import (  # noqa: E402
    DEFAULT_BASE_URL,
    check_backend,
    record_visual_review,
    validate_asset_pack,
)


TOOLS = [
    {
        "name": "check_backend",
        "description": "Check whether local Forge/A1111 API is reachable, has --api enabled, and has a checkpoint loaded.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {"type": "string"}
            }
        },
    },
    {
        "name": "generate_asset_pack",
        "description": "Generate a 2D asset pack from asset_request.json, postprocess PNGs, and write asset_manifest.json.",
        "inputSchema": {
            "type": "object",
            "required": ["spec_path", "output_dir", "manifest_path"],
            "properties": {
                "spec_path": {"type": "string"},
                "output_dir": {"type": "string"},
                "manifest_path": {"type": "string"},
                "style_profile_path": {"type": "string"},
                "base_url": {"type": "string"},
                "timeout": {"type": "integer"}
            }
        },
    },
    {
        "name": "validate_asset_pack",
        "description": "Validate a generated asset_manifest.json and write validation_report.json next to it.",
        "inputSchema": {
            "type": "object",
            "required": ["manifest_path"],
            "properties": {
                "manifest_path": {"type": "string"}
            }
        },
    },
    {
        "name": "record_visual_review",
        "description": "Record an approved or rejected visual QA decision after inspecting generated PNG output.",
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
                "serverInfo": {"name": "asset-pipeline", "version": "0.1.0"},
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
    base_url = str(arguments.get("base_url") or os.environ.get("FORGE_BASE_URL") or DEFAULT_BASE_URL)
    try:
        if name == "check_backend":
            _debug("check_backend start")
            data = check_backend(base_url=base_url).to_dict()
            _debug("check_backend done")
            return _tool_result(data, status="ok" if data.get("ok") else "error")
        if name == "generate_asset_pack":
            _debug("generate_asset_pack start")
            manifest_path = Path(str(arguments["manifest_path"]))
            command = [
                sys.executable,
                str(SCRIPTS_DIR / "assetgen_cli.py"),
                "--project-root",
                str(project_root),
                "--base-url",
                base_url,
                "generate",
                "--spec",
                str(arguments["spec_path"]),
                "--out",
                str(arguments["output_dir"]),
                "--manifest",
                str(manifest_path),
                "--timeout",
                str(int(arguments.get("timeout") or 300)),
            ]
            if arguments.get("style_profile_path"):
                command.extend(["--style-profile", str(arguments["style_profile_path"])])
            timeout = int(arguments.get("timeout") or 300) + 60
            _debug("generate_asset_pack subprocess start")
            completed = subprocess.run(command, cwd=str(project_root), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
            _debug(f"generate_asset_pack subprocess done returncode={completed.returncode}")
            if completed.returncode != 0:
                return _tool_result(
                    {
                        "message": "assetgen_cli generate failed",
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                    },
                    status="error",
                )
            resolved_manifest = manifest_path if manifest_path.is_absolute() else project_root / manifest_path
            _debug(f"generate_asset_pack read manifest {resolved_manifest}")
            full_manifest = json.loads(resolved_manifest.read_text(encoding="utf-8-sig"))
            data = _summarize_manifest(full_manifest)
            status = "ok" if data.get("validation", {}).get("status") == "passed" else "error"
            _debug("generate_asset_pack done")
            return _tool_result(data, status=status)
        if name == "validate_asset_pack":
            _debug("validate_asset_pack start")
            data = validate_asset_pack(
                manifest_path=Path(str(arguments["manifest_path"])),
                project_root=project_root,
            )
            _debug("validate_asset_pack done")
            status = "ok" if data.get("validation", {}).get("status") == "passed" else "error"
            return _tool_result(data, status=status)
        if name == "record_visual_review":
            _debug("record_visual_review start")
            data = record_visual_review(
                manifest_path=Path(str(arguments["manifest_path"])),
                decision=str(arguments["decision"]),
                reviewer=str(arguments["reviewer"]),
                notes=str(arguments.get("notes") or ""),
                project_root=project_root,
            )
            _debug("record_visual_review done")
            return _tool_result(data, status="ok")
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
        "model": manifest.get("model", ""),
        "manifest_path": manifest.get("manifest_path", ""),
        "validation_report_path": manifest.get("validation_report_path", ""),
        "validation": manifest.get("validation", {}),
        "visual_review": manifest.get("visual_review", {}),
        "ready_for_integration": bool(manifest.get("ready_for_integration", False)),
        "assets": [
            {
                "id": asset.get("id", ""),
                "type": asset.get("type", ""),
                "path": asset.get("path", ""),
                "raw_path": asset.get("raw_path", ""),
                "target_width": asset.get("target_width"),
                "target_height": asset.get("target_height"),
                "transparent": asset.get("transparent"),
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


def _debug(message: str) -> None:
    path = os.environ.get("ASSET_PIPELINE_DEBUG_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
