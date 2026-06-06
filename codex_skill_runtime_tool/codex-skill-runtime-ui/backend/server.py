#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


TOOL_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = TOOL_ROOT.parent
CORE_CLI = TOOL_ROOT / "codex-skill-runtime-core" / "core_cli.py"
CORE_ROOT = TOOL_ROOT / "codex-skill-runtime-core"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "frontend"
DEFAULT_ENV = TOOL_ROOT / "config" / "skill-runtime.env"

sys.path.insert(0, str(CORE_ROOT))

from runtime.capabilities import discover_capabilities  # noqa: E402
from runtime.jobs import JobRegistry  # noqa: E402
from runtime.plugins import set_plugin_enabled  # noqa: E402


class ServerState:
    def __init__(self, *, runtime_env: Path, state_root: Path) -> None:
        self.runtime_env = runtime_env
        self.state_root = state_root
        self.processes: dict[str, dict[str, object]] = {}
        self.jobs = JobRegistry(state_root)
        self.lock = threading.Lock()
        self.skills_cache: dict[str, object] | None = None
        self.skills_cache_at = 0.0

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
            query = parse_qs(parsed.query)
            include_diagnostics = _query_bool(query, "diagnostics", default=False)
            project_id = query.get("project", [""])[0]
            return self._json({"sessions": self._sessions(project_id=project_id, include_diagnostics=include_diagnostics)})
        if parsed.path.startswith("/api/sessions/"):
            session_id = unquote(parsed.path.removeprefix("/api/sessions/")).strip("/")
            return self._json(self._session_detail(session_id))
        if parsed.path == "/api/projects":
            return self._json(self._projects())
        if parsed.path == "/api/skills":
            return self._json(self._skills())
        if parsed.path == "/api/capabilities":
            return self._json(self._capabilities())
        if parsed.path == "/api/jobs":
            return self._json({"jobs": self._jobs()})
        if parsed.path == "/api/plugins":
            return self._json(self._plugins())
        if parsed.path == "/api/memory":
            query = parse_qs(parsed.query)
            return self._json(self._memory(query.get("session", [""])[0]))
        if parsed.path == "/api/memory/file":
            query = parse_qs(parsed.query)
            return self._json(self._memory_file(query.get("path", [""])[0]))
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
            if parsed.path == "/api/plugin":
                return self._json(self._set_plugin(payload))
            if parsed.path == "/api/projects":
                return self._json(self._upsert_project(payload))
            if parsed.path == "/api/projects/current":
                return self._json(self._set_current_project(payload))
            if parsed.path == "/api/memory/file":
                return self._json(self._write_memory_file(payload))
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                job_id = unquote(parsed.path.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/"))
                return self._json(self._cancel_job(job_id))
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
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, raw_path: str) -> None:
        if not raw_path:
            return self._json({"ok": False, "error": "path is required"}, status=400)
        path = Path(raw_path).expanduser().resolve()
        env_paths = _runtime_env_paths(self.state.runtime_env)
        allowed_roots = [
            WORKSPACE_ROOT.resolve(),
            TOOL_ROOT.resolve(),
            self.state.state_root.resolve(),
            env_paths["target_workspace"],
            *env_paths["skill_repos"],
            *_project_save_roots(self.state.runtime_env, self.state.state_root),
            *self._known_task_workspaces(),
        ]
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
        _apply_runtime_env_to_process(config)
        paths = _runtime_env_paths(self.state.runtime_env)
        capabilities = discover_capabilities(paths["target_workspace"], additional_dirs=paths["skill_repos"])
        return {
            "ok": True,
            "workspace_root": str(WORKSPACE_ROOT),
            "target_workspace": str(paths["target_workspace"]),
            "skill_repos": [str(path) for path in paths["skill_repos"]],
            "tool_root": str(TOOL_ROOT),
            "runtime_env": str(self.state.runtime_env),
            "state_root": str(self.state.state_root),
            "default_save_root": str(_default_save_root(self.state.runtime_env, self.state.state_root)),
            "current_project": _current_project(self.state.runtime_env, self.state.state_root),
            "core_cli": str(CORE_CLI),
            "codex_api_key_file": _expand_env_value(config.get("CODEX_API_KEY_FILE", "")),
            "codex_base_url": config.get("CODEX_BASE_URL", ""),
            "capabilities": len(capabilities),
            "processes": self._process_snapshot(),
            "jobs": self._jobs(),
        }

    def _skills(self) -> dict[str, object]:
        now = time.monotonic()
        with self.state.lock:
            if self.state.skills_cache is not None and now - self.state.skills_cache_at < 60:
                return {**self.state.skills_cache, "cached": True}
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
        result = {"ok": True, **data}
        with self.state.lock:
            self.state.skills_cache = result
            self.state.skills_cache_at = time.monotonic()
        return result

    def _capabilities(self) -> dict[str, object]:
        _apply_runtime_env_to_process(_load_env(self.state.runtime_env))
        paths = _runtime_env_paths(self.state.runtime_env)
        return {
            "ok": True,
            "capabilities": [item.to_dict() for item in discover_capabilities(paths["target_workspace"], additional_dirs=paths["skill_repos"])],
        }

    def _plugins(self) -> dict[str, object]:
        data = self._skills()
        if not data.get("ok"):
            return data
        return {"ok": True, "plugins": data.get("plugins", [])}

    def _projects(self) -> dict[str, object]:
        return _projects_response(self.state.runtime_env, self.state.state_root)

    def _upsert_project(self, payload: dict[str, object]) -> dict[str, object]:
        config = _load_projects_config(self.state.runtime_env, self.state.state_root)
        projects = [dict(item) for item in config["projects"] if isinstance(item, dict)]
        now = datetime.now().isoformat(timespec="seconds")
        raw_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip() or "未命名项目"
        project_id = _slug(raw_id or name) or "project"
        save_root_raw = str(payload.get("save_root") or "").strip()
        existing_ids = {str(item.get("id") or "") for item in projects}
        if raw_id and raw_id in existing_ids:
            project_id = raw_id
        elif project_id in existing_ids:
            base = project_id
            counter = 2
            while project_id in existing_ids:
                project_id = f"{base}-{counter}"
                counter += 1
        if save_root_raw:
            save_root = Path(save_root_raw).expanduser().resolve()
        else:
            save_root = (_projects_root(self.state.runtime_env, self.state.state_root) / project_id).resolve()
        save_root.mkdir(parents=True, exist_ok=True)
        updated = {
            "id": project_id,
            "name": name,
            "save_root": str(save_root),
            "created_at": now,
            "updated_at": now,
        }
        next_projects: list[dict[str, object]] = []
        replaced = False
        for item in projects:
            if item.get("id") == raw_id:
                updated["created_at"] = item.get("created_at") or now
                next_projects.append(updated)
                replaced = True
            else:
                next_projects.append(item)
        if not replaced:
            next_projects.insert(0, updated)
        config["projects"] = next_projects
        if bool(payload.get("make_current", True)):
            config["current_project_id"] = project_id
        _write_projects_config(self.state.state_root, config)
        return _projects_response(self.state.runtime_env, self.state.state_root)

    def _set_current_project(self, payload: dict[str, object]) -> dict[str, object]:
        project_id = str(payload.get("id") or payload.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project id is required")
        config = _load_projects_config(self.state.runtime_env, self.state.state_root)
        if project_id not in {str(item.get("id") or "") for item in config["projects"] if isinstance(item, dict)}:
            raise ValueError(f"project not found: {project_id}")
        config["current_project_id"] = project_id
        _write_projects_config(self.state.state_root, config)
        return _projects_response(self.state.runtime_env, self.state.state_root)

    def _sessions(self, *, project_id: str = "", include_diagnostics: bool = False) -> list[dict[str, object]]:
        sessions_root = self.state.sessions_root
        if not sessions_root.exists():
            return []
        project = _project_by_id(self.state.runtime_env, self.state.state_root, project_id)
        rows = []
        for session_dir in sorted((p for p in sessions_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
            state = _read_json(session_dir / "session-state.json")
            summary = _read_json(session_dir / "summary.json")
            workspace = state.get("root", "") if isinstance(state, dict) else ""
            diagnostic = _is_diagnostic_session(session_dir, state, summary, self.state.state_root)
            belongs = _session_belongs_to_project(state, summary, project)
            if diagnostic and not include_diagnostics:
                continue
            if project and not belongs and not (include_diagnostics and diagnostic):
                continue
            row = {
                "id": session_dir.name,
                "path": str(session_dir),
                "updated_at": datetime.fromtimestamp(session_dir.stat().st_mtime).isoformat(timespec="seconds"),
                "status": _session_status(state, summary, session_dir),
                "label": _first_text(state.get("label") if isinstance(state, dict) else "", summary.get("label") if isinstance(summary, dict) else "", session_dir.name),
                "current_skill": state.get("current_skill", "") if isinstance(state, dict) else "",
                "current_agents": state.get("current_agents", []) if isinstance(state, dict) else [],
                "workspace": workspace,
                "project_id": project.get("id", "") if project and belongs else "",
                "diagnostic": diagnostic,
                "summary": _compact_summary(summary),
            }
            rows.append(row)
        return rows[:300]

    def _session_detail(self, session_id: str) -> dict[str, object]:
        session_dir = _find_session_dir(self.state.sessions_root, session_id)
        if session_dir is None:
            return {"ok": False, "error": "session not found", "session_id": session_id}
        state = _read_json(session_dir / "session-state.json")
        workspace = Path(str(state.get("root") or "")) if isinstance(state, dict) and state.get("root") else None
        return {
            "ok": True,
            "id": session_dir.name,
            "path": str(session_dir),
            "state": state,
            "tree": _tree_or_replay(session_dir),
            "artifacts": _artifacts(session_dir),
            "events": _read_jsonl(session_dir / "events.jsonl", limit=300),
            "transcript": _read_jsonl(session_dir / "transcript.jsonl", limit=160),
            "files": _session_files(session_dir),
            "file_tree": _build_file_tree("运行记录", session_dir, max_files=900),
            "workspace_file_tree": _build_file_tree("任务文件", workspace, max_files=1200) if workspace else {},
            "workspace_path": str(workspace) if workspace else "",
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
        target_workspace = str(payload.get("target_workspace") or "").strip()
        task_workspace = ""
        if operation == "run":
            invocation = str(payload.get("invocation") or payload.get("command") or "").strip()
            arguments = str(payload.get("arguments") or "")
            if not invocation:
                raise ValueError("invocation is required")
            if not invocation.startswith("/"):
                invocation = "/" + invocation
            project = _project_by_id(self.state.runtime_env, self.state.state_root, str(payload.get("project_id") or ""))
            save_root = _resolve_save_root(payload, self.state.runtime_env, self.state.state_root, project=project)
            task_workspace_path = _create_task_workspace(save_root, invocation=invocation, arguments=arguments, project=project)
            task_workspace = str(task_workspace_path)
            target_workspace = task_workspace
            command.extend(["--target-workspace", target_workspace])
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
            if not target_workspace:
                target_workspace = self._workspace_for_session(session)
            if target_workspace:
                command.extend(["--target-workspace", target_workspace])
            command.extend(["resume", session])
            if prompt:
                command.append(prompt)
        elif operation == "answer":
            session = str(payload.get("session") or "").strip()
            answer = str(payload.get("answer") or "")
            if not session or not answer:
                raise ValueError("session and answer are required")
            if not target_workspace:
                target_workspace = self._workspace_for_session(session)
            if target_workspace:
                command.extend(["--target-workspace", target_workspace])
            command.extend(["answer", session, answer])
        else:
            raise ValueError(f"unsupported operation: {operation}")

        process_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        stdout = self.state.state_root / "ui-processes" / f"{process_id}.stdout.txt"
        stderr = self.state.state_root / "ui-processes" / f"{process_id}.stderr.txt"
        stdout.parent.mkdir(parents=True, exist_ok=True)
        job = self.state.jobs.create(
            operation=operation,
            command=command,
            cwd=WORKSPACE_ROOT,
            stdout=stdout,
            stderr=stderr,
            metadata={
                "target_workspace": target_workspace,
                "task_workspace": task_workspace,
                "ui_project_id": (project or {}).get("id", "") if operation == "run" else "",
                "ui_project_name": (project or {}).get("name", "") if operation == "run" else "",
                "ui_project_save_root": (project or {}).get("save_root", "") if operation == "run" else "",
            },
        )
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
            self.state.processes[job.id] = {
                "pid": process.pid,
                "operation": operation,
                "command": command,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "stdout": str(stdout),
                "stderr": str(stderr),
                "process": process,
            }
        self.state.jobs.mark_started(job.id, pid=process.pid)
        return {
            "ok": True,
            "process_id": job.id,
            "job_id": job.id,
            "pid": process.pid,
            "stdout": str(stdout),
            "stderr": str(stderr),
            "target_workspace": target_workspace,
            "task_workspace": task_workspace,
        }

    def _process_snapshot(self) -> list[dict[str, object]]:
        rows = []
        with self.state.lock:
            for process_id, data in list(self.state.processes.items()):
                process = data.get("process")
                returncode = process.poll() if isinstance(process, subprocess.Popen) else None
                if returncode is not None:
                    self.state.jobs.mark_finished(process_id, returncode=returncode)
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

    def _jobs(self) -> list[dict[str, object]]:
        self._process_snapshot()
        jobs = self.state.jobs.list(limit=300)
        for item in jobs:
            if item.get("status") in {"starting", "running", "cancel_requested"} and item.get("id") not in self.state.processes:
                self.state.jobs.mark_unknown_if_orphaned(str(item.get("id")))
        return self.state.jobs.list(limit=300)

    def _cancel_job(self, job_id: str) -> dict[str, object]:
        with self.state.lock:
            data = self.state.processes.get(job_id)
            process = data.get("process") if isinstance(data, dict) else None
            if isinstance(process, subprocess.Popen) and process.poll() is None:
                process.terminate()
        return self.state.jobs.cancel(job_id)

    def _set_plugin(self, payload: dict[str, object]) -> dict[str, object]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("plugin name is required")
        enabled = bool(payload.get("enabled"))
        root = str(payload.get("root") or "").strip() or None
        paths = _runtime_env_paths(self.state.runtime_env)
        state = set_plugin_enabled(paths["target_workspace"], name=name, root=root, enabled=enabled)
        with self.state.lock:
            self.state.skills_cache = None
            self.state.skills_cache_at = 0.0
        return {"ok": True, "state": state}

    def _workspace_for_session(self, session_id: str) -> str:
        session_dir = _find_session_dir(self.state.sessions_root, session_id)
        if session_dir is None:
            return ""
        state = _read_json(session_dir / "session-state.json")
        if isinstance(state, dict):
            root = str(state.get("root") or "").strip()
            if root:
                return root
            metadata = state.get("metadata")
            if isinstance(metadata, dict):
                return str(metadata.get("target_workspace") or "").strip()
        return ""

    def _known_task_workspaces(self) -> list[Path]:
        roots: list[Path] = []
        for session_dir in self.state.sessions_root.glob("*"):
            if not session_dir.is_dir():
                continue
            state = _read_json(session_dir / "session-state.json")
            if isinstance(state, dict) and state.get("root"):
                roots.append(Path(str(state["root"])).expanduser().resolve())
        for job in self.state.jobs.list(limit=1000):
            metadata = job.get("metadata")
            if isinstance(metadata, dict):
                for key in ("target_workspace", "task_workspace"):
                    value = str(metadata.get(key) or "").strip()
                    if value:
                        roots.append(Path(value).expanduser().resolve())
        return _unique_paths(roots)

    def _memory(self, session_id: str = "") -> dict[str, object]:
        project_root = self.state.state_root / "project-memory"
        durable_root = self.state.state_root / "memory"
        agent_root = self.state.state_root / "agent-memory"
        session_memory = {}
        if session_id:
            session_dir = _find_session_dir(self.state.sessions_root, session_id)
            if session_dir is not None:
                session_memory = {
                    "session_id": session_dir.name,
                    "root": str(session_dir / "session-memory"),
                    "summary": _memory_doc(session_dir / "session-memory" / "summary.md", title="Session summary"),
                    "compact": _memory_doc(session_dir / "session-memory" / "compact.md", title="Compact summary"),
                    "state": _memory_doc(session_dir / "session-memory" / "state.json", title="Session memory state"),
                }
        return {
            "ok": True,
            "state_root": str(self.state.state_root),
            "project_memory": {
                "root": str(project_root),
                "style": _memory_doc(project_root / "style-guide.md", title="全局风格"),
                "notes": _memory_doc(project_root / "project-notes.md", title="项目笔记"),
                "assets": _memory_doc(project_root / "asset-manifest.jsonl", title="资产清单"),
                "tree": _build_file_tree("项目记忆", project_root, max_files=300),
            },
            "durable_memory": {
                "root": str(durable_root),
                "overview": _memory_doc(durable_root / "MEMORY.md", title="MEMORY.md"),
                "topics": _memory_docs(durable_root / "topics"),
                "tree": _build_file_tree("耐久记忆", durable_root, max_files=500),
            },
            "agent_memory": {
                "root": str(agent_root),
                "items": _memory_docs(agent_root),
                "tree": _build_file_tree("Agent 记忆", agent_root, max_files=300),
            },
            "session_memory": session_memory,
        }

    def _memory_file(self, raw_path: str) -> dict[str, object]:
        path = Path(raw_path).expanduser().resolve()
        if not _is_memory_path(path, self.state.state_root):
            return {"ok": False, "error": "path is outside runtime memory roots"}
        return {"ok": True, "file": _memory_doc(path, title=path.name, include_content=True)}

    def _write_memory_file(self, payload: dict[str, object]) -> dict[str, object]:
        raw_path = str(payload.get("path") or "").strip()
        content = str(payload.get("content") or "")
        if not raw_path:
            raise ValueError("path is required")
        path = Path(raw_path).expanduser().resolve()
        if not _is_memory_path(path, self.state.state_root):
            raise ValueError("path is outside runtime memory roots")
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".txt"}:
            raise ValueError("only text memory files can be edited")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}


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


def _default_save_root(runtime_env: Path, state_root: Path) -> Path:
    values = _load_env(runtime_env)
    configured = (
        values.get("SKILL_RUNTIME_SAVE_ROOT")
        or values.get("SKILL_RUNTIME_OUTPUT_ROOT")
        or values.get("SKILL_RUNTIME_TASK_WORKSPACES")
        or ""
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return state_root / "task-workspaces"


def _projects_root(runtime_env: Path, state_root: Path) -> Path:
    values = _load_env(runtime_env)
    configured = values.get("SKILL_RUNTIME_PROJECTS_ROOT") or values.get("SKILL_RUNTIME_PROJECT_ROOT") or ""
    if configured:
        return Path(configured).expanduser().resolve()
    return WORKSPACE_ROOT / "runtime-projects"


def _projects_config_path(state_root: Path) -> Path:
    return state_root / "projects.json"


def _load_projects_config(runtime_env: Path, state_root: Path) -> dict[str, object]:
    path = _projects_config_path(state_root)
    data = _read_json(path)
    projects = data.get("projects") if isinstance(data, dict) else []
    if not isinstance(projects, list):
        projects = []
    normalized: list[dict[str, object]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        save_root = str(item.get("save_root") or "").strip()
        if not project_id or not save_root:
            continue
        normalized.append(
            {
                "id": project_id,
                "name": str(item.get("name") or project_id),
                "save_root": str(Path(save_root).expanduser().resolve()),
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    if not normalized:
        now = datetime.now().isoformat(timespec="seconds")
        normalized.append(
            {
                "id": "default",
                "name": "默认项目",
                "save_root": str(_projects_root(runtime_env, state_root).resolve()),
                "created_at": now,
                "updated_at": now,
            }
        )
    current = str(data.get("current_project_id") or "").strip() if isinstance(data, dict) else ""
    ids = {str(item.get("id") or "") for item in normalized}
    if current not in ids:
        current = str(normalized[0]["id"])
    return {"current_project_id": current, "projects": normalized}


def _write_projects_config(state_root: Path, config: dict[str, object]) -> None:
    path = _projects_config_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _projects_response(runtime_env: Path, state_root: Path) -> dict[str, object]:
    config = _load_projects_config(runtime_env, state_root)
    current = _project_by_id(runtime_env, state_root, str(config.get("current_project_id") or ""))
    return {
        "ok": True,
        "config_path": str(_projects_config_path(state_root)),
        "current_project_id": str((current or {}).get("id") or ""),
        "current_project": current,
        "projects": config.get("projects", []),
    }


def _current_project(runtime_env: Path, state_root: Path) -> dict[str, object]:
    return _project_by_id(runtime_env, state_root, "") or {}


def _project_by_id(runtime_env: Path, state_root: Path, project_id: str = "") -> dict[str, object] | None:
    config = _load_projects_config(runtime_env, state_root)
    wanted = str(project_id or config.get("current_project_id") or "").strip()
    projects = [item for item in config.get("projects", []) if isinstance(item, dict)]
    for item in projects:
        if str(item.get("id") or "") == wanted:
            return item
    return projects[0] if projects else None


def _project_save_roots(runtime_env: Path, state_root: Path) -> list[Path]:
    config = _load_projects_config(runtime_env, state_root)
    roots: list[Path] = []
    for item in config.get("projects", []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("save_root") or "").strip()
        if value:
            roots.append(Path(value).expanduser().resolve())
    return _unique_paths(roots)


def _session_belongs_to_project(state: object, summary: object, project: dict[str, object] | None) -> bool:
    if not project:
        return True
    project_id = str(project.get("id") or "")
    save_root = Path(str(project.get("save_root") or "")).expanduser().resolve() if project.get("save_root") else None
    for metadata in _session_metadata_sources(state, summary):
        if project_id and str(metadata.get("ui_project_id") or metadata.get("project_id") or "") == project_id:
            return True
        for key in ("ui_project_save_root", "save_root", "target_workspace", "task_workspace", "root"):
            path = _path_or_none(metadata.get(key))
            if path is not None and save_root is not None and _is_under(path, save_root):
                return True
    workspace = _path_or_none(state.get("root")) if isinstance(state, dict) else None
    if workspace is not None:
        marker = _read_json(workspace / ".skill-runtime-task.json")
        if isinstance(marker, dict):
            if project_id and str(marker.get("project_id") or "") == project_id:
                return True
            marker_save_root = _path_or_none(marker.get("save_root"))
            if marker_save_root is not None and save_root is not None and marker_save_root == save_root:
                return True
        return save_root is not None and _is_under(workspace, save_root)
    return False


def _session_metadata_sources(state: object, summary: object) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for source in (state, summary):
        if not isinstance(source, dict):
            continue
        sources.append(source)
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            sources.append(metadata)
    return sources


def _is_diagnostic_session(session_dir: Path, state: object, summary: object, state_root: Path) -> bool:
    chunks = [session_dir.name]
    for source in (state, summary):
        if isinstance(source, dict):
            for key in ("label", "command", "session_id"):
                chunks.append(str(source.get(key) or ""))
    text = " ".join(chunks).lower()
    if "selftest" in text or "__ui_smoke" in text or "strict-smoke" in text:
        return True
    workspace = _path_or_none(state.get("root")) if isinstance(state, dict) else None
    if workspace is None:
        return False
    diagnostic_roots = [
        state_root / "selftest-fixtures",
        state_root / "ui-smoke",
        state_root / "ui-save-smoke",
    ]
    return any(_is_under(workspace, root.resolve()) for root in diagnostic_roots)


def _path_or_none(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool) -> bool:
    values = query.get(key)
    if not values:
        return default
    text = str(values[0] or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_save_root(payload: dict[str, object], runtime_env: Path, state_root: Path, *, project: dict[str, object] | None = None) -> Path:
    raw = str(payload.get("save_root") or payload.get("output_root") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if project and project.get("save_root"):
        return Path(str(project["save_root"])).expanduser().resolve()
    return _default_save_root(runtime_env, state_root).resolve()


def _create_task_workspace(save_root: Path, *, invocation: str, arguments: str, project: dict[str, object] | None = None) -> Path:
    save_root = save_root.expanduser().resolve()
    save_root.mkdir(parents=True, exist_ok=True)
    label_source = f"{invocation} {arguments}".strip()
    slug = _slug(label_source) or "task"
    now = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    candidate = save_root / f"{now}-{slug[:48]}"
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = save_root / f"{now}-{slug[:44]}-{counter}"
    candidate.mkdir(parents=True, exist_ok=False)
    (candidate / ".skill-runtime-task.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "invocation": invocation,
                "arguments": arguments,
                "save_root": str(save_root),
                "project_id": str((project or {}).get("id") or ""),
                "project_name": str((project or {}).get("name") or ""),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return candidate


def _slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value).strip("-._")
    text = re.sub(r"-{2,}", "-", text)
    return text[:80]


def _build_file_tree(label: str, root: Path | None, *, max_files: int = 800, max_depth: int = 8) -> dict[str, object]:
    if root is None:
        return {"name": label, "type": "directory", "exists": False, "children": []}
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"name": label, "path": str(root), "type": "directory", "exists": False, "children": []}

    counter = {"count": 0, "truncated": False}

    def walk(path: Path, depth: int) -> dict[str, object]:
        try:
            stat = path.stat()
        except OSError:
            stat = None
        if path.is_file():
            counter["count"] += 1
            return {
                "name": path.name,
                "path": str(path),
                "relative": _relative_path(path, root),
                "type": "file",
                "bytes": stat.st_size if stat else 0,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds") if stat else "",
            }

        node: dict[str, object] = {
            "name": label if path == root else path.name,
            "path": str(path),
            "relative": "" if path == root else _relative_path(path, root),
            "type": "directory",
            "children": [],
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds") if stat else "",
        }
        if depth >= max_depth:
            node["truncated"] = True
            return node
        children: list[dict[str, object]] = []
        try:
            entries = [item for item in path.iterdir() if not _skip_tree_entry(item)]
        except OSError:
            entries = []
        entries.sort(key=lambda item: (item.is_file(), item.name.lower()))
        for item in entries:
            if counter["count"] >= max_files:
                counter["truncated"] = True
                break
            children.append(walk(item, depth + 1))
        node["children"] = children
        if counter["truncated"]:
            node["truncated"] = True
        return node

    tree = walk(root, 0)
    tree["exists"] = True
    tree["file_count"] = counter["count"]
    tree["truncated"] = bool(counter["truncated"])
    return tree


def _skip_tree_entry(path: Path) -> bool:
    name = path.name
    if name in {".git", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"}:
        return True
    if name.endswith((".pyc", ".pyo", ".tmp")):
        return True
    return False


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def _memory_doc(path: Path, *, title: str, include_content: bool = True, max_chars: int = 80000) -> dict[str, object]:
    path = path.expanduser().resolve()
    exists = path.exists() and path.is_file()
    data: dict[str, object] = {
        "title": title,
        "name": path.name,
        "path": str(path),
        "exists": exists,
        "editable": path.suffix.lower() in {".md", ".json", ".jsonl", ".txt"},
        "bytes": 0,
        "updated_at": "",
        "content": "",
        "truncated": False,
    }
    if not exists:
        return data
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace") if include_content else ""
    except OSError:
        return data
    data["bytes"] = stat.st_size
    data["updated_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    if include_content:
        data["truncated"] = len(text) > max_chars
        data["content"] = text[:max_chars]
    return data


def _memory_docs(root: Path, *, exclude_names: set[str] | None = None, limit: int = 200) -> list[dict[str, object]]:
    exclude_names = exclude_names or set()
    if not root.exists() or not root.is_dir():
        return []
    docs: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in exclude_names:
            continue
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".txt"}:
            continue
        docs.append(_memory_doc(path, title=_relative_path(path, root), include_content=False))
    docs.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return docs[:limit]


def _is_memory_path(path: Path, state_root: Path) -> bool:
    roots = [
        state_root / "project-memory",
        state_root / "memory",
        state_root / "agent-memory",
        state_root / "sessions",
    ]
    resolved = path.expanduser().resolve()
    if not any(_is_under(resolved, root.resolve()) for root in roots):
        return False
    if _is_under(resolved, (state_root / "sessions").resolve()):
        return "session-memory" in resolved.parts
    return True


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


def _apply_runtime_env_to_process(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key.startswith("SKILL_RUNTIME_ENV_") and len(key) > len("SKILL_RUNTIME_ENV_"):
            os.environ[key.removeprefix("SKILL_RUNTIME_ENV_")] = value
        elif key in {"SKILL_RUNTIME_CAPABILITIES_JSON", "CODEX_SKILL_RUNTIME_CAPABILITIES_JSON"}:
            os.environ[key] = value
        elif key.startswith("SKILL_RUNTIME_CAPABILITY_") or key.startswith("CODEX_SKILL_RUNTIME_CAPABILITY_"):
            os.environ[key] = value


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


def _runtime_env_paths(runtime_env: Path) -> dict[str, object]:
    values = _load_env(runtime_env)
    root = Path(values.get("SKILL_RUNTIME_ROOT") or WORKSPACE_ROOT).expanduser().resolve()
    target = Path(values.get("SKILL_RUNTIME_TARGET_WORKSPACE") or values.get("SKILL_RUNTIME_WORKSPACE") or root).expanduser().resolve()
    raw_repos = values.get("SKILL_RUNTIME_SKILL_REPOS") or values.get("SKILL_RUNTIME_SKILL_REPOSITORIES") or ""
    if raw_repos:
        repos = [Path(item).expanduser().resolve() for item in _split_list(raw_repos)]
    else:
        repos = [root]
    add_dirs = [Path(item).expanduser().resolve() for item in _split_list(values.get("SKILL_RUNTIME_ADD_DIRS") or values.get("SKILL_RUNTIME_ADD_DIR") or "")]
    return {"root": root, "target_workspace": target, "skill_repos": _unique_paths([*repos, *add_dirs])}


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


def _split_list(value: str) -> list[str]:
    stripped = str(value or "").strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return []
        return [str(item).strip() for item in parsed if str(item).strip()] if isinstance(parsed, list) else []
    separator = "||" if "||" in stripped else ";"
    return [item.strip() for item in stripped.split(separator) if item.strip()]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


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
