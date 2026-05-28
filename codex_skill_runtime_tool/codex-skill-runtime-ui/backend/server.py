#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


TOOL_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = TOOL_ROOT.parent
CORE_CLI = TOOL_ROOT / "codex-skill-runtime-core" / "core_cli.py"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "frontend"
DEFAULT_ENV = TOOL_ROOT / "config" / "skill-runtime.env"


class ServerState:
    def __init__(self, *, runtime_env: Path, state_root: Path) -> None:
        self.runtime_env = runtime_env
        self.state_root = state_root
        self.processes: dict[str, dict[str, object]] = {}
        self.lock = threading.Lock()

    @property
    def sessions_root(self) -> Path:
        return self.state_root / "sessions"


class RuntimeUIHandler(BaseHTTPRequestHandler):
    state: ServerState

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[%s] %s\n" % (datetime.now().isoformat(timespec="seconds"), format % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_static("index.html")
        if parsed.path.startswith("/static/"):
            return self._serve_static(parsed.path.removeprefix("/static/"))
        if parsed.path == "/api/health":
            return self._json(self._health())
        if parsed.path == "/api/sessions":
            return self._json({"sessions": self._sessions()})
        if parsed.path.startswith("/api/sessions/"):
            session_id = unquote(parsed.path.removeprefix("/api/sessions/")).strip("/")
            return self._json(self._session_detail(session_id))
        if parsed.path == "/api/skills":
            return self._json(self._skills())
        if parsed.path == "/api/file":
            query = parse_qs(parsed.query)
            return self._serve_file(query.get("path", [""])[0])
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/run":
                return self._json(self._start_runtime(payload, "run"))
            if parsed.path == "/api/resume":
                return self._json(self._start_runtime(payload, "resume"))
            if parsed.path == "/api/answer":
                return self._json(self._start_runtime(payload, "answer"))
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=500)
        self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_static(self, relative: str) -> None:
        path = (STATIC_ROOT / relative).resolve()
        if not _is_under(path, STATIC_ROOT) or not path.exists() or path.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = _content_type(path)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, raw_path: str) -> None:
        if not raw_path:
            return self._json({"ok": False, "error": "path is required"}, status=400)
        path = Path(raw_path).expanduser().resolve()
        allowed_roots = [WORKSPACE_ROOT.resolve(), self.state.state_root.resolve()]
        if not any(_is_under(path, root) for root in allowed_roots):
            return self._json({"ok": False, "error": "path is outside the workspace"}, status=403)
        if not path.exists() or path.is_dir():
            return self._json({"ok": False, "error": "file not found"}, status=404)
        content_type = _content_type(path)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data: object, *, status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _health(self) -> dict[str, object]:
        config = _load_env(self.state.runtime_env)
        return {
            "ok": True,
            "workspace_root": str(WORKSPACE_ROOT),
            "tool_root": str(TOOL_ROOT),
            "runtime_env": str(self.state.runtime_env),
            "state_root": str(self.state.state_root),
            "core_cli": str(CORE_CLI),
            "codex_api_key_file": _expand_env_value(config.get("CODEX_API_KEY_FILE", "")),
            "codex_base_url": config.get("CODEX_BASE_URL", ""),
            "forge_base_url": config.get("SKILL_RUNTIME_ENV_FORGE_BASE_URL", ""),
            "comfyui_base_url": config.get("SKILL_RUNTIME_ENV_COMFYUI_BASE_URL", ""),
            "processes": self._process_snapshot(),
        }

    def _skills(self) -> dict[str, object]:
        command = [
            sys.executable,
            "-B",
            str(CORE_CLI),
            "--runtime-env",
            str(self.state.runtime_env),
            "inspect",
        ]
        completed = subprocess.run(command, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=60, check=False)
        if completed.returncode != 0:
            return {"ok": False, "returncode": completed.returncode, "stderr": completed.stderr[-4000:]}
        try:
            data = json.loads(completed.stdout)
        except ValueError:
            return {"ok": False, "stdout": completed.stdout[-4000:]}
        return {"ok": True, **data}

    def _sessions(self) -> list[dict[str, object]]:
        sessions_root = self.state.sessions_root
        if not sessions_root.exists():
            return []
        rows = []
        for session_dir in sorted((p for p in sessions_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
            state = _read_json(session_dir / "session-state.json")
            summary = _read_json(session_dir / "summary.json")
            row = {
                "id": session_dir.name,
                "path": str(session_dir),
                "updated_at": datetime.fromtimestamp(session_dir.stat().st_mtime).isoformat(timespec="seconds"),
                "status": _session_status(state, summary, session_dir),
                "label": _first_text(state.get("label") if isinstance(state, dict) else "", summary.get("label") if isinstance(summary, dict) else "", session_dir.name),
                "current_skill": state.get("current_skill", "") if isinstance(state, dict) else "",
                "current_agents": state.get("current_agents", []) if isinstance(state, dict) else [],
                "summary": _compact_summary(summary),
            }
            rows.append(row)
        return rows[:300]

    def _session_detail(self, session_id: str) -> dict[str, object]:
        session_dir = _find_session_dir(self.state.sessions_root, session_id)
        if session_dir is None:
            return {"ok": False, "error": "session not found", "session_id": session_id}
        return {
            "ok": True,
            "id": session_dir.name,
            "path": str(session_dir),
            "state": _read_json(session_dir / "session-state.json"),
            "tree": _tree_or_replay(session_dir),
            "artifacts": _artifacts(session_dir),
            "events": _read_jsonl(session_dir / "events.jsonl", limit=300),
            "transcript": _read_jsonl(session_dir / "transcript.jsonl", limit=160),
            "files": _session_files(session_dir),
            "pending_question": _read_json(session_dir / "pending-question.json"),
            "pending_answer": _read_json(session_dir / "pending-question-answer.json"),
            "summary": _read_json(session_dir / "summary.json"),
        }

    def _start_runtime(self, payload: dict[str, object], operation: str) -> dict[str, object]:
        command = [
            sys.executable,
            "-B",
            str(CORE_CLI),
            "--runtime-env",
            str(self.state.runtime_env),
        ]
        if operation == "run":
            invocation = str(payload.get("invocation") or payload.get("command") or "").strip()
            arguments = str(payload.get("arguments") or "")
            if not invocation:
                raise ValueError("invocation is required")
            if not invocation.startswith("/"):
                invocation = "/" + invocation
            qa_mode = str(payload.get("qa") or "").strip()
            if qa_mode in {"auto", "off", "required"}:
                command.extend(["--qa", qa_mode])
            max_steps = payload.get("max_steps")
            if max_steps not in {None, ""}:
                command.extend(["--max-steps", str(int(max_steps))])
            if "strict_tools" in payload and not bool(payload.get("strict_tools")):
                command.append("--no-strict-tools")
            elif bool(payload.get("strict_tools")):
                command.append("--strict-tools")
            command.extend(["run", invocation])
            if arguments:
                command.append(arguments)
        elif operation == "resume":
            session = str(payload.get("session") or "").strip()
            prompt = str(payload.get("prompt") or "")
            if not session:
                raise ValueError("session is required")
            command.extend(["resume", session])
            if prompt:
                command.append(prompt)
        elif operation == "answer":
            session = str(payload.get("session") or "").strip()
            answer = str(payload.get("answer") or "")
            if not session or not answer:
                raise ValueError("session and answer are required")
            command.extend(["answer", session, answer])
        else:
            raise ValueError(f"unsupported operation: {operation}")

        process_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        stdout = self.state.state_root / "ui-processes" / f"{process_id}.stdout.txt"
        stderr = self.state.state_root / "ui-processes" / f"{process_id}.stderr.txt"
        stdout.parent.mkdir(parents=True, exist_ok=True)
        out_handle = stdout.open("wb")
        err_handle = stderr.open("wb")
        process = subprocess.Popen(
            command,
            cwd=str(WORKSPACE_ROOT),
            stdout=out_handle,
            stderr=err_handle,
        )
        out_handle.close()
        err_handle.close()
        with self.state.lock:
            self.state.processes[process_id] = {
                "pid": process.pid,
                "operation": operation,
                "command": command,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "stdout": str(stdout),
                "stderr": str(stderr),
                "process": process,
            }
        return {"ok": True, "process_id": process_id, "pid": process.pid, "stdout": str(stdout), "stderr": str(stderr)}

    def _process_snapshot(self) -> list[dict[str, object]]:
        rows = []
        with self.state.lock:
            for process_id, data in list(self.state.processes.items()):
                process = data.get("process")
                returncode = process.poll() if isinstance(process, subprocess.Popen) else None
                rows.append(
                    {
                        "id": process_id,
                        "pid": data.get("pid"),
                        "operation": data.get("operation"),
                        "started_at": data.get("started_at"),
                        "returncode": returncode,
                        "stdout": data.get("stdout"),
                        "stderr": data.get("stderr"),
                    }
                )
        return rows


def _session_status(state: object, summary: object, session_dir: Path) -> str:
    if isinstance(state, dict) and state.get("status"):
        return str(state["status"])
    if isinstance(summary, dict) and summary.get("status"):
        return "done" if str(summary["status"]).upper() == "PASS" else "failed"
    if not (session_dir / "summary.json").exists():
        return "unknown"
    return "done"


def _compact_summary(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict):
        return {}
    keep = {
        "command",
        "arguments",
        "status",
        "created_at",
        "updated_at",
        "session_id",
    }
    compact: dict[str, object] = {key: summary[key] for key in keep if key in summary}
    notes = summary.get("notes")
    if isinstance(notes, str):
        compact["notes"] = notes[:500]
    gates = summary.get("gates")
    if isinstance(gates, list):
        compact["gates"] = gates[:8]
    return compact


def _tree_or_replay(session_dir: Path) -> dict[str, object]:
    tree = _read_json(session_dir / "task-tree.json")
    if isinstance(tree, dict) and isinstance(tree.get("nodes"), list):
        return tree
    return _derive_tree_from_events(session_dir)


def _derive_tree_from_events(session_dir: Path) -> dict[str, object]:
    events = _read_jsonl(session_dir / "events.jsonl", limit=1000)
    nodes = []
    root_id = "node-0001"
    nodes.append(
        {
            "id": root_id,
            "parent_id": None,
            "type": "session",
            "namespace": "",
            "name": session_dir.name,
            "display_name": session_dir.name,
            "status": "done" if (session_dir / "summary.json").exists() else "unknown",
            "started_at": "",
            "finished_at": None,
            "evidence": {},
            "metadata": {},
            "child_ids": [],
        }
    )
    counter = 1
    for event in events:
        type_ = str(event.get("type") or "")
        if type_ in {"codex.prepare", "tool.start", "question.pending"}:
            counter += 1
            node_id = f"node-{counter:04d}"
            nodes[0]["child_ids"].append(node_id)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            nodes.append(
                {
                    "id": node_id,
                    "parent_id": root_id,
                    "type": "tool" if type_ == "tool.start" else ("question" if type_ == "question.pending" else "agent"),
                    "namespace": "",
                    "name": str(data.get("tool") or data.get("label") or type_),
                    "display_name": str(data.get("tool") or data.get("label") or type_),
                    "status": "waiting_user" if type_ == "question.pending" else "done",
                    "started_at": str(event.get("timestamp") or ""),
                    "finished_at": None,
                    "evidence": {},
                    "metadata": data,
                    "child_ids": [],
                }
            )
    return {"session_id": session_dir.name, "root_node_id": root_id, "nodes": nodes}


def _artifacts(session_dir: Path) -> dict[str, object]:
    data = _read_json(session_dir / "artifacts.json")
    if isinstance(data, dict) and isinstance(data.get("artifacts"), list):
        return data
    found = []
    for path in session_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".ogg", ".flac", ".md", ".json"}:
            found.append({"path": str(path), "type": _artifact_type(path), "previewable": True, "created_by_node_id": "", "created_by_agent": ""})
    return {"session_id": session_dir.name, "artifacts": found[:200]}


def _session_files(session_dir: Path) -> list[dict[str, object]]:
    rows = []
    for path in session_dir.rglob("*"):
        if path.is_file() and path.name in {"prompt.md", "stdout.jsonl", "stderr.txt", "last-message.md", "response.json", "strict-result.json", "summary.json"}:
            rows.append({"path": str(path), "name": path.name, "relative": str(path.relative_to(session_dir)), "bytes": path.stat().st_size})
    return sorted(rows, key=lambda item: str(item["relative"]))[:500]


def _find_session_dir(sessions_root: Path, session_id: str) -> Path | None:
    direct = sessions_root / session_id
    if direct.exists() and direct.is_dir():
        return direct
    matches = sorted(sessions_root.glob(f"*{session_id}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _read_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    except OSError:
        return []
    return rows[-limit:]


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _expand_env_value(value.strip(), values)
    return values


def _expand_env_value(value: str, current: dict[str, str] | None = None) -> str:
    values = {
        "SKILL_RUNTIME_TOOL_ROOT": str(TOOL_ROOT),
        "SKILL_RUNTIME_WORKSPACE_ROOT": str(WORKSPACE_ROOT),
        **os.environ,
        **(current or {}),
    }
    result = value
    for key, item in values.items():
        result = result.replace("${" + key + "}", str(item))
    return result


def _state_root_from_env(runtime_env: Path) -> Path:
    values = _load_env(runtime_env)
    configured = values.get("SKILL_RUNTIME_STATE_ROOT", "")
    if configured:
        return Path(configured).expanduser().resolve()
    return TOOL_ROOT / ".skill-runtime" / "state"


def _artifact_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return "image"
    if suffix in {".wav", ".mp3", ".ogg", ".flac", ".opus"}:
        return "audio"
    return "document" if suffix in {".md", ".txt", ".json", ".csv", ".yaml", ".yml"} else "file"


def _content_type(path: Path) -> str:
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if content_type.startswith("text/") or path.suffix.lower() in {".js", ".json", ".md", ".svg"}:
        return f"{content_type}; charset=utf-8"
    return content_type


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Web UI for Codex Skill Runtime.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime-env", default=str(DEFAULT_ENV))
    args = parser.parse_args(argv)
    runtime_env = Path(args.runtime_env).expanduser().resolve()
    state = ServerState(runtime_env=runtime_env, state_root=_state_root_from_env(runtime_env))
    RuntimeUIHandler.state = state
    server = ThreadingHTTPServer((args.host, args.port), RuntimeUIHandler)
    print(f"Codex Skill Runtime UI: http://{args.host}:{args.port}")
    print(f"Runtime env: {runtime_env}")
    print(f"State root: {state.state_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
