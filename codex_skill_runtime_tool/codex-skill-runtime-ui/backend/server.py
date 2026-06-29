#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


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
from ui_config import (  # noqa: E402
    RuntimePaths,
    apply_runtime_env_to_process as _apply_runtime_env_to_process,
    configured_services,
    expand_env_value,
    load_env,
    model_config_from_env,
    model_config_updates_from_payload as _model_config_updates_from_payload,
    portable_path,
    runtime_env_exports,
    runtime_env_paths,
    service_by_id,
    state_root_from_env,
    write_env_updates as _write_env_updates,
)


RUNTIME_PATHS = RuntimePaths(tool_root=TOOL_ROOT, workspace_root=WORKSPACE_ROOT)
_load_env = partial(load_env, paths=RUNTIME_PATHS)
_model_config_from_env = partial(model_config_from_env, paths=RUNTIME_PATHS)
_runtime_env_exports = partial(runtime_env_exports, paths=RUNTIME_PATHS)
_configured_services = partial(configured_services, paths=RUNTIME_PATHS)
_service_by_id = partial(service_by_id, paths=RUNTIME_PATHS)
_expand_env_value = partial(expand_env_value, paths=RUNTIME_PATHS)
_portable_path = partial(portable_path, paths=RUNTIME_PATHS)
_state_root_from_env = partial(state_root_from_env, paths=RUNTIME_PATHS)
_runtime_env_paths = partial(runtime_env_paths, paths=RUNTIME_PATHS)


class ServerState:
    def __init__(self, *, runtime_env: Path, state_root: Path) -> None:
        self.runtime_env = runtime_env
        self.state_root = state_root
        self.server: ThreadingHTTPServer | None = None
        self.shutdown_requested = False
        self.processes: dict[str, dict[str, object]] = {}
        self.jobs = JobRegistry(state_root)
        self.lock = threading.RLock()
        self.skills_cache: dict[str, object] | None = None
        self.skills_cache_at = 0.0
        self.ui_heartbeat_at = 0.0
        self.ui_heartbeat_wall_at = ""
        self.ui_heartbeat_count = 0
        self.ui_heartbeat_page_id = ""
        self.ui_heartbeat_closing_at = 0.0
        self.ui_heartbeat_closing_wall_at = ""
        self.ui_heartbeat_closing_page_id = ""
        self.services: dict[str, dict[str, object]] = {}

    def stop_managed_services(self) -> None:
        with self.lock:
            service_ids = list(self.services.keys())
        for service_id in service_ids:
            with self.lock:
                data = self.services.get(service_id)
                process = data.get("process") if isinstance(data, dict) else None
            if isinstance(process, subprocess.Popen) and process.poll() is None:
                _terminate_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process.pid, force=True)
            with self.lock:
                self.services.pop(service_id, None)

    def stop_managed_runtime_jobs(self) -> None:
        with self.lock:
            jobs = list(self.processes.items())
        terminal_updates: list[tuple[dict[str, object], str, str]] = []
        for job_id, data in jobs:
            process = data.get("process") if isinstance(data, dict) else None
            if not isinstance(process, subprocess.Popen):
                with self.lock:
                    self.processes.pop(job_id, None)
                continue
            returncode = process.poll()
            if returncode is None:
                self.jobs.mark_cancel_requested(job_id)
                _terminate_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process.pid, force=True)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                returncode = process.poll()
                self.jobs.update(
                    job_id,
                    status="cancelled",
                    returncode=returncode,
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                updated = self.jobs.get(job_id)
                if updated:
                    terminal_updates.append((updated, "cancelled", "runtime shutdown cancelled managed job"))
            else:
                updated = self.jobs.mark_finished(job_id, returncode=returncode)
                if updated:
                    terminal_updates.append(
                        (updated, "done" if returncode == 0 else "failed", "managed job process exited")
                    )
            with self.lock:
                self.processes.pop(job_id, None)
        for job, status, reason in terminal_updates:
            _sync_terminal_job_to_sessions(self.state_root / "sessions", job, status=status, reason=reason)

    def stop_all_managed(self) -> None:
        self.stop_managed_runtime_jobs()
        self.stop_managed_services()

    def request_shutdown(self) -> None:
        with self.lock:
            if self.shutdown_requested:
                return
            self.shutdown_requested = True
            server = self.server
        if server is None:
            return

        def _shutdown() -> None:
            time.sleep(0.25)
            server.shutdown()

        threading.Thread(target=_shutdown, daemon=True).start()

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
        if parsed.path == "/api/ui/heartbeat":
            return self._json(self._ui_heartbeat_status())
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
        if parsed.path == "/api/services":
            return self._json({"ok": True, "services": self._services()})
        if parsed.path == "/api/model-config":
            return self._json(self._model_config())
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
            if parsed.path == "/api/chat":
                return self._json(self._start_runtime(payload, "chat"))
            if parsed.path == "/api/run":
                return self._json(self._start_runtime(payload, "run"))
            if parsed.path == "/api/ui/heartbeat":
                result = self._record_ui_heartbeat(payload)
                if bool(payload.get("closing")):
                    self._stop_all_managed()
                return self._json(result)
            if parsed.path == "/api/ui/shutdown":
                result = self._shutdown_runtime_ui(payload)
                return self._json(result)
            if parsed.path == "/api/ui/heartbeat/reset":
                return self._json(self._reset_ui_heartbeat())
            if parsed.path == "/api/resume":
                return self._json(self._start_runtime(payload, "resume"))
            if parsed.path == "/api/answer":
                return self._json(self._start_runtime(payload, "answer"))
            if parsed.path == "/api/plugin":
                return self._json(self._set_plugin(payload))
            if parsed.path == "/api/model-config":
                return self._json(self._save_model_config(payload))
            if parsed.path.startswith("/api/services/"):
                return self._handle_service_action(parsed.path, payload)
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/stop"):
                session_id = unquote(parsed.path.removeprefix("/api/sessions/").removesuffix("/stop").strip("/"))
                return self._json(self._stop_session(session_id))
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/delete"):
                session_id = unquote(parsed.path.removeprefix("/api/sessions/").removesuffix("/delete").strip("/"))
                return self._json(self._delete_session(session_id))
            if parsed.path == "/api/diagnostics/clear":
                return self._json(self._clear_diagnostics())
            if parsed.path == "/api/history/clear":
                return self._json(self._clear_history(payload))
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
        path = _portable_path(raw_path)
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
            "services": self._services(),
            "ui_heartbeat": self._ui_heartbeat_status(),
        }

    def _model_config(self) -> dict[str, object]:
        config = _load_env(self.state.runtime_env)
        model_config = _model_config_from_env(config, self.state.runtime_env)
        return {"ok": True, **model_config}

    def _save_model_config(self, payload: dict[str, object]) -> dict[str, object]:
        current = _load_env(self.state.runtime_env)
        updates = _model_config_updates_from_payload(payload, current)
        _write_env_updates(self.state.runtime_env, updates, delete_when_empty={"SKILL_RUNTIME_CODEX_LOCAL_PROVIDER"})
        config = _load_env(self.state.runtime_env)
        return {"ok": True, **_model_config_from_env(config, self.state.runtime_env)}

    def _record_ui_heartbeat(self, payload: dict[str, object]) -> dict[str, object]:
        now = time.monotonic()
        wall = datetime.now().isoformat(timespec="seconds")
        closing = bool(payload.get("closing"))
        source = str(payload.get("source") or "").strip()
        page_id = str(payload.get("page_id") or "").strip()
        with self.state.lock:
            previous_page_id = self.state.ui_heartbeat_page_id
            if page_id and not closing:
                self.state.ui_heartbeat_page_id = page_id
            self.state.ui_heartbeat_at = now
            self.state.ui_heartbeat_wall_at = wall
            self.state.ui_heartbeat_count += 1
            if closing:
                browser_close = source in {"", "browser-close"}
                active_page_id = self.state.ui_heartbeat_page_id or previous_page_id
                if browser_close and page_id and active_page_id and page_id != active_page_id:
                    pass
                else:
                    if page_id and not self.state.ui_heartbeat_page_id:
                        self.state.ui_heartbeat_page_id = page_id
                    self.state.ui_heartbeat_closing_at = now
                    self.state.ui_heartbeat_closing_wall_at = wall
                    self.state.ui_heartbeat_closing_page_id = page_id
            else:
                self.state.ui_heartbeat_closing_at = 0.0
                self.state.ui_heartbeat_closing_wall_at = ""
                self.state.ui_heartbeat_closing_page_id = ""
        return self._ui_heartbeat_status()

    def _reset_ui_heartbeat(self) -> dict[str, object]:
        with self.state.lock:
            self.state.ui_heartbeat_at = 0.0
            self.state.ui_heartbeat_wall_at = ""
            self.state.ui_heartbeat_count = 0
            self.state.ui_heartbeat_page_id = ""
            self.state.ui_heartbeat_closing_at = 0.0
            self.state.ui_heartbeat_closing_wall_at = ""
            self.state.ui_heartbeat_closing_page_id = ""
        return self._ui_heartbeat_status()

    def _ui_heartbeat_status(self) -> dict[str, object]:
        now = time.monotonic()
        with self.state.lock:
            last_seen = self.state.ui_heartbeat_at
            last_closing = self.state.ui_heartbeat_closing_at
            return {
                "ok": True,
                "seen": self.state.ui_heartbeat_count > 0,
                "count": self.state.ui_heartbeat_count,
                "last_seen_at": self.state.ui_heartbeat_wall_at,
                "last_seen_age_seconds": round(now - last_seen, 3) if last_seen else None,
                "page_id": self.state.ui_heartbeat_page_id,
                "closing_pending": bool(last_closing),
                "closing_at": self.state.ui_heartbeat_closing_wall_at,
                "closing_page_id": self.state.ui_heartbeat_closing_page_id,
                "closing_age_seconds": round(now - last_closing, 3) if last_closing else None,
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

    def _handle_service_action(self, path: str, payload: dict[str, object]) -> None:
        tail = path.removeprefix("/api/services/").strip("/")
        parts = tail.split("/")
        if len(parts) != 2:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        service_id, action = (unquote(parts[0]), parts[1])
        if action == "start":
            return self._json(self._start_service(service_id, payload))
        if action == "stop":
            return self._json(self._stop_service(service_id))
        self.send_error(HTTPStatus.NOT_FOUND)

    def _services(self) -> list[dict[str, object]]:
        config = _load_env(self.state.runtime_env)
        _apply_runtime_env_to_process(config)
        rows = []
        for service in _configured_services(config):
            row = dict(service)
            running = self._service_running_process(service["id"])
            if running is not None:
                row["managed"] = True
                row["pid"] = running.pid
            else:
                row["managed"] = False
                row["pid"] = None
            row["reachable"] = _url_ok(str(service.get("health_url") or service.get("endpoint") or ""), timeout=2)
            if row["reachable"]:
                row["status"] = "running"
            elif row["managed"]:
                row["status"] = "starting"
            else:
                row["status"] = "stopped"
            rows.append(row)
        return rows

    def _start_service(self, service_id: str, payload: dict[str, object]) -> dict[str, object]:
        service = _service_by_id(_load_env(self.state.runtime_env), service_id)
        if service is None:
            return {"ok": False, "error": f"service not configured: {service_id}"}
        if _url_ok(str(service.get("health_url") or service.get("endpoint") or ""), timeout=2):
            return {"ok": True, "service": service_id, "status": "running", "already_running": True}
        process = self._service_running_process(service_id)
        if process is not None and process.poll() is None:
            return {"ok": True, "service": service_id, "status": "starting", "pid": process.pid}
        command = str(service.get("start_cmd") or "").strip()
        if not command:
            return {"ok": False, "error": f"service has no start command: {service_id}"}
        log_root = self.state.state_root / "ui-services"
        log_root.mkdir(parents=True, exist_ok=True)
        stdout = log_root / f"{service_id}.stdout.log"
        stderr = log_root / f"{service_id}.stderr.log"
        out_handle = stdout.open("ab")
        err_handle = stderr.open("ab")
        env = os.environ.copy()
        env.update(_runtime_env_exports(_load_env(self.state.runtime_env)))
        process = subprocess.Popen(
            ["bash", "-lc", command] if os.name != "nt" else ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            cwd=str(WORKSPACE_ROOT),
            stdout=out_handle,
            stderr=err_handle,
            env=env,
        )
        out_handle.close()
        err_handle.close()
        with self.state.lock:
            self.state.services[service_id] = {
                "process": process,
                "pid": process.pid,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "stdout": str(stdout),
                "stderr": str(stderr),
            }
        return {"ok": True, "service": service_id, "status": "starting", "pid": process.pid, "stdout": str(stdout), "stderr": str(stderr)}

    def _stop_service(self, service_id: str) -> dict[str, object]:
        process = self._service_running_process(service_id)
        if process is None:
            return {"ok": True, "service": service_id, "status": "stopped", "already_stopped": True}
        _terminate_process_tree(process.pid)
        with self.state.lock:
            self.state.services.pop(service_id, None)
        return {"ok": True, "service": service_id, "status": "stopping", "pid": process.pid}

    def _service_running_process(self, service_id: str) -> subprocess.Popen | None:
        with self.state.lock:
            data = self.state.services.get(service_id)
            process = data.get("process") if isinstance(data, dict) else None
            if not isinstance(process, subprocess.Popen):
                return None
            if process.poll() is None:
                return process
            self.state.services.pop(service_id, None)
            return None

    def _stop_managed_services(self) -> None:
        self.state.stop_managed_services()

    def _stop_all_managed(self) -> None:
        self.state.stop_all_managed()

    def _shutdown_runtime_ui(self, payload: dict[str, object]) -> dict[str, object]:
        source = str(payload.get("source") or "").strip()
        page_id = str(payload.get("page_id") or "").strip()
        with self.state.lock:
            active_page_id = self.state.ui_heartbeat_page_id
        browser_close = source in {"", "browser-close"}
        if browser_close and page_id and active_page_id and page_id != active_page_id:
            return {
                "ok": True,
                "shutdown_requested": False,
                "ignored": True,
                "reason": "stale_page",
                "source": source,
                "page_id": page_id,
                "active_page_id": active_page_id,
            }
        self.state.stop_all_managed()
        self.state.request_shutdown()
        return {
            "ok": True,
            "shutdown_requested": True,
            "source": source,
            "page_id": page_id,
            "active_page_id": active_page_id,
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
            save_root = _portable_path(save_root_raw, fallback=_projects_root(self.state.runtime_env, self.state.state_root) / project_id)
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
        session_dirs = []
        for path in sessions_root.iterdir():
            try:
                if path.is_dir():
                    session_dirs.append((path, path.stat().st_mtime))
            except FileNotFoundError:
                continue
        for session_dir, mtime in sorted(session_dirs, key=lambda item: item[1], reverse=True):
            state = _read_json(session_dir / "session-state.json")
            summary = _read_json(session_dir / "summary.json")
            workspace = state.get("root", "") if isinstance(state, dict) else ""
            session_jobs = self._jobs_for_session(session_dir, state, _path_or_none(workspace) if workspace else None)
            active_job = _active_job(session_jobs)
            status = _effective_session_status(state, summary, session_dir, session_jobs)
            diagnostic = _is_diagnostic_session(session_dir, state, summary, self.state.state_root)
            belongs = _session_belongs_to_project(state, summary, project)
            if diagnostic and not include_diagnostics:
                continue
            if project and not belongs and not (include_diagnostics and diagnostic):
                continue
            row = {
                "id": session_dir.name,
                "path": str(session_dir),
                "updated_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                "status": status,
                "label": _first_text(state.get("label") if isinstance(state, dict) else "", summary.get("label") if isinstance(summary, dict) else "", session_dir.name),
                "current_skill": _active_skill(state, active_job),
                "current_agents": _active_agents(state, active_job),
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
        workspace = _path_or_none(state.get("root")) if isinstance(state, dict) and state.get("root") else None
        jobs = self._jobs_for_session(session_dir, state, workspace)
        active_job = _active_job(jobs)
        summary = _read_json(session_dir / "summary.json")
        return {
            "ok": True,
            "id": session_dir.name,
            "path": str(session_dir),
            "state": _effective_state_for_ui(state, summary, session_dir, jobs),
            "jobs": jobs,
            "active_job": active_job,
            "history": _session_history(session_dir, state, jobs),
            "conversation_events": _conversation_events_for_ui(session_dir, state, summary, jobs),
            "tree": _tree_for_ui(session_dir, active_job),
            "artifacts": _artifacts(session_dir),
            "events": _read_jsonl(session_dir / "events.jsonl", limit=300),
            "transcript": _read_jsonl(session_dir / "transcript.jsonl", limit=160),
            "files": _session_files(session_dir),
            "file_tree": _build_file_tree("运行记录", session_dir, max_files=900),
            "workspace_file_tree": _build_file_tree("任务文件", workspace, max_files=1200) if workspace else {},
            "workspace_path": str(workspace) if workspace else "",
            "pending_question": _read_json(session_dir / "pending-question.json"),
            "pending_answer": _read_json(session_dir / "pending-question-answer.json"),
            "summary": summary,
        }

    def _jobs_for_session(self, session_dir: Path, state: object, workspace: Path | None) -> list[dict[str, object]]:
        workspace_text = str(workspace) if workspace else ""
        candidates = []
        for job in self._jobs():
            metadata = job.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("session_id") == session_dir.name:
                candidates.append(job)
                continue
            if workspace_text and metadata.get("target_workspace") == workspace_text:
                candidates.append(job)
                continue
            if workspace_text and metadata.get("task_workspace") == workspace_text:
                candidates.append(job)
                continue
        return candidates

    def _stop_session(self, session_id: str) -> dict[str, object]:
        session_dir = _find_session_dir(self.state.sessions_root, session_id)
        if session_dir is None:
            return {"ok": False, "error": "session not found", "session_id": session_id}
        state = _read_json(session_dir / "session-state.json")
        workspace = _path_or_none(state.get("root")) if isinstance(state, dict) and state.get("root") else None
        jobs = self._jobs_for_session(session_dir, state, workspace)
        active = _active_job(jobs)
        if not active or not active.get("id"):
            return {"ok": True, "session_id": session_id, "stopped": False, "message": "no active job"}
        result = self._cancel_job(str(active["id"]))
        return {"ok": bool(result.get("ok")), "session_id": session_id, "stopped": bool(result.get("ok")), "job_result": result}

    def _delete_session(self, session_id: str) -> dict[str, object]:
        session_dir = _find_session_dir(self.state.sessions_root, session_id)
        if session_dir is None:
            return {"ok": False, "error": "session not found", "session_id": session_id}
        result = self._delete_session_dir(session_dir, reason="manual-delete")
        result["ok"] = True
        return result

    def _delete_session_dir(self, session_dir: Path, *, reason: str) -> dict[str, object]:
        session_id = session_dir.name
        state = _read_json(session_dir / "session-state.json")
        workspace = _path_or_none(state.get("root")) if isinstance(state, dict) and state.get("root") else None
        jobs = self._jobs_for_session(session_dir, state, workspace)
        active = _active_job(jobs)
        if active and active.get("id"):
            self._cancel_job(str(active["id"]))
        trash_root = self.state.state_root / "trash" / "deleted-sessions" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{reason}-{_slug(session_id) or 'session'}"
        trash_root.mkdir(parents=True, exist_ok=True)
        moved: list[dict[str, str]] = []
        session_target = _unique_trash_path(trash_root / "session")
        shutil.move(str(session_dir), str(session_target))
        moved.append({"kind": "session", "from": str(session_dir), "to": str(session_target)})
        if workspace and workspace.exists() and _deletable_workspace_path(workspace, self.state.runtime_env, self.state.state_root):
            workspace_target = _unique_trash_path(trash_root / "workspace")
            shutil.move(str(workspace), str(workspace_target))
            moved.append({"kind": "workspace", "from": str(workspace), "to": str(workspace_target)})
        job_ids = {str(job.get("id") or "") for job in jobs if job.get("id")}
        removed_jobs = self.state.jobs.delete_many(job_ids)
        with self.state.lock:
            for job_id in job_ids:
                self.state.processes.pop(job_id, None)
        return {
            "session_id": session_id,
            "trash": str(trash_root),
            "moved": moved,
            "removed_jobs": removed_jobs,
        }

    def _clear_diagnostics(self) -> dict[str, object]:
        cleared = []
        sessions_root = self.state.sessions_root
        if not sessions_root.exists():
            return {"ok": True, "cleared": [], "count": 0}
        for session_dir in sorted(_existing_session_dirs(sessions_root), key=lambda p: p.name):
            state = _read_json(session_dir / "session-state.json")
            summary = _read_json(session_dir / "summary.json")
            if not _is_diagnostic_session(session_dir, state, summary, self.state.state_root):
                continue
            if not session_dir.exists():
                continue
            cleared.append(self._delete_session_dir(session_dir, reason="diagnostic-clear"))
        return {"ok": True, "cleared": cleared, "count": len(cleared)}

    def _clear_history(self, payload: dict[str, object]) -> dict[str, object]:
        project_id = str(payload.get("project_id") or "").strip()
        include_diagnostics = bool(payload.get("include_diagnostics"))
        project = _project_by_id_exact(self.state.runtime_env, self.state.state_root, project_id) if project_id else _project_by_id(self.state.runtime_env, self.state.state_root, "")
        if project_id and project is None:
            return {"ok": False, "error": f"project not found: {project_id}", "project_id": project_id}
        cleared = []
        sessions_root = self.state.sessions_root
        if not sessions_root.exists():
            return {"ok": True, "cleared": [], "count": 0}
        for session_dir in sorted(_existing_session_dirs(sessions_root), key=lambda p: p.name):
            state = _read_json(session_dir / "session-state.json")
            summary = _read_json(session_dir / "summary.json")
            diagnostic = _is_diagnostic_session(session_dir, state, summary, self.state.state_root)
            if diagnostic and not include_diagnostics:
                continue
            if project and not _session_belongs_to_project(state, summary, project):
                continue
            if not session_dir.exists():
                continue
            cleared.append(self._delete_session_dir(session_dir, reason="history-clear"))
        return {"ok": True, "project_id": str((project or {}).get("id") or ""), "cleared": cleared, "count": len(cleared)}

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
        if operation == "chat":
            message = str(payload.get("message") or payload.get("prompt") or payload.get("arguments") or "").strip()
            if not message:
                raise ValueError("message is required")
            project = _project_by_id(self.state.runtime_env, self.state.state_root, str(payload.get("project_id") or ""))
            save_root = _resolve_save_root(payload, self.state.runtime_env, self.state.state_root, project=project)
            task_workspace_path = _create_task_workspace(save_root, invocation="chat", arguments=message, project=project)
            task_workspace = str(task_workspace_path)
            target_workspace = task_workspace
            command.extend(["--target-workspace", target_workspace])
            command.extend(["chat", message])
            invocation = "chat"
            arguments = message
        elif operation == "run":
            invocation = str(payload.get("invocation") or payload.get("command") or "").strip()
            arguments = str(payload.get("arguments") or "")
            if not invocation:
                raise ValueError("run requires an explicit slash command; natural language must use /api/chat")
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
                command.extend(["--max-steps", str(max(8, min(80, int(max_steps))))])
            command.append("--strict-schema")
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
                "invocation": invocation if operation in {"run", "chat"} else "",
                "arguments": arguments if operation in {"run", "chat"} else "",
                "ui_project_id": (project or {}).get("id", "") if operation in {"run", "chat"} else "",
                "ui_project_name": (project or {}).get("name", "") if operation in {"run", "chat"} else "",
                "ui_project_save_root": (project or {}).get("save_root", "") if operation in {"run", "chat"} else "",
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
            "source_session": str(payload.get("session") or "") if operation in {"resume", "answer"} else "",
            "parent_session_id": str(payload.get("session") or "") if operation in {"resume", "answer"} else "",
            "continuation_workspace": target_workspace if operation in {"resume", "answer"} else "",
            "triggered_by": operation,
            "invocation": invocation if operation in {"run", "chat"} else "",
            "arguments": arguments if operation in {"run", "chat"} else "",
        }

    def _process_snapshot(self) -> list[dict[str, object]]:
        rows = []
        terminal_updates: list[tuple[dict[str, object], str, str]] = []
        with self.state.lock:
            for process_id, data in list(self.state.processes.items()):
                process = data.get("process")
                returncode = process.poll() if isinstance(process, subprocess.Popen) else None
                if returncode is not None:
                    updated = self.state.jobs.mark_finished(process_id, returncode=returncode)
                    if updated:
                        terminal_updates.append(
                            (updated, "done" if returncode == 0 else "failed", "runtime job process exited")
                        )
                    self.state.processes.pop(process_id, None)
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
        for job, status, reason in terminal_updates:
            _sync_terminal_job_to_sessions(self.state.sessions_root, job, status=status, reason=reason)
        return rows

    def _jobs(self) -> list[dict[str, object]]:
        self._process_snapshot()
        jobs = self.state.jobs.list(limit=300)
        for item in jobs:
            if item.get("status") in {"starting", "running", "cancel_requested"} and item.get("id") not in self.state.processes:
                updated = self.state.jobs.mark_unknown_if_orphaned(str(item.get("id")))
                if updated and updated.get("status") == "unknown":
                    _sync_terminal_job_to_sessions(
                        self.state.sessions_root,
                        updated,
                        status="unknown",
                        reason="runtime job process is no longer tracked",
                    )
        return self.state.jobs.list(limit=300)

    def _cancel_job(self, job_id: str) -> dict[str, object]:
        with self.state.lock:
            data = self.state.processes.get(job_id)
            process = data.get("process") if isinstance(data, dict) else None
            if isinstance(process, subprocess.Popen) and process.poll() is None:
                self.state.jobs.mark_cancel_requested(job_id)
                _terminate_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process.pid, force=True)
                with self.state.lock:
                    self.state.processes.pop(job_id, None)
                updated = self.state.jobs.update(
                    job_id,
                    status="cancelled",
                    returncode=process.poll(),
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                )
                if updated:
                    _sync_terminal_job_to_sessions(
                        self.state.sessions_root,
                        updated,
                        status="cancelled",
                        reason="user cancelled runtime job from UI",
                    )
                return {"ok": True, "job": updated}
        result = self.state.jobs.cancel(job_id)
        job = result.get("job") if isinstance(result, dict) else None
        if isinstance(job, dict) and result.get("ok"):
            _sync_terminal_job_to_sessions(
                self.state.sessions_root,
                job,
                status="cancelled",
                reason="user cancelled runtime job from UI",
            )
        return result

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
                roots.append(_portable_path(str(state["root"])))
        for job in self.state.jobs.list(limit=1000):
            metadata = job.get("metadata")
            if isinstance(metadata, dict):
                for key in ("target_workspace", "task_workspace"):
                    value = str(metadata.get(key) or "").strip()
                    if value:
                        roots.append(_portable_path(value))
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
        path = _portable_path(raw_path)
        if not _is_memory_path(path, self.state.state_root):
            return {"ok": False, "error": "path is outside runtime memory roots"}
        return {"ok": True, "file": _memory_doc(path, title=path.name, include_content=True)}

    def _write_memory_file(self, payload: dict[str, object]) -> dict[str, object]:
        raw_path = str(payload.get("path") or "").strip()
        content = str(payload.get("content") or "")
        if not raw_path:
            raise ValueError("path is required")
        path = _portable_path(raw_path)
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
        return _summary_status_for_ui(str(summary["status"]))
    if not (session_dir / "summary.json").exists():
        return "unknown"
    return "done"


def _active_job(jobs: list[dict[str, object]]) -> dict[str, object] | None:
    return next((job for job in jobs if job.get("status") in {"starting", "running", "cancel_requested"}), None)


def _effective_session_status(state: object, summary: object, session_dir: Path, jobs: list[dict[str, object]]) -> str:
    active = _active_job(jobs)
    if active:
        return str(active.get("status") or "running")
    state_status = str(state.get("status") or "").strip() if isinstance(state, dict) else ""
    if state_status == "waiting_user":
        return state_status
    summary_status = summary.get("status") if isinstance(summary, dict) else ""
    if summary_status:
        return _summary_status_for_ui(str(summary_status))
    if state_status in {"starting", "running", "cancel_requested"}:
        return "stale"
    if state_status:
        return state_status
    if (session_dir / "summary.json").exists():
        return "done"
    return "unknown"


def _summary_status_for_ui(status: str) -> str:
    normalized = str(status or "").strip().upper().replace("-", "_")
    if normalized in {"PASS", "DONE", "OK", "SUCCESS"}:
        return "done"
    if normalized in {"ANSWERED", "RESUMED", "CONTINUED"}:
        return "answered"
    if normalized in {"WAITING_USER", "WAITING_FOR_USER", "NEEDS_USER", "QUESTION"}:
        return "waiting_user"
    if normalized in {"BLOCKED", "CANCELLED", "CANCELED"}:
        return normalized.lower()
    if normalized in {"FAIL", "FAILED", "ERROR"}:
        return "failed"
    return normalized.lower() or "unknown"


def _active_skill(state: object, active_job: dict[str, object] | None) -> str:
    if active_job:
        metadata = active_job.get("metadata") if isinstance(active_job.get("metadata"), dict) else {}
        invocation = str(metadata.get("invocation") or "").strip()
        if invocation:
            return invocation
    return str(state.get("current_skill") or "") if isinstance(state, dict) else ""


def _active_agents(state: object, active_job: dict[str, object] | None) -> list[dict[str, object]]:
    if active_job:
        return [{
            "name": "runtime-job",
            "status": str(active_job.get("status") or "running"),
            "current_action": str(active_job.get("operation") or ""),
        }]
    return []


def _effective_state_for_ui(state: object, summary: object, session_dir: Path, jobs: list[dict[str, object]]) -> dict[str, object]:
    result = dict(state) if isinstance(state, dict) else {}
    active = _active_job(jobs)
    result["status"] = _effective_session_status(state, summary, session_dir, jobs)
    if active:
        result["current_skill"] = _active_skill(state, active)
        result["current_agents"] = _active_agents(state, active)
    else:
        if str(state.get("status") or "") in {"starting", "running", "cancel_requested"} if isinstance(state, dict) else False:
            result["stale_status"] = state.get("status")
        result["current_agents"] = []
    return result


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


def _session_history(session_dir: Path, state: object, jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    metadata = state.get("metadata") if isinstance(state, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    initial_text = "\n".join(part for part in [
        str(metadata.get("invocation") or "").strip(),
        str(metadata.get("arguments") or "").strip(),
    ] if part)
    if initial_text:
        history.append({
            "role": "user",
            "title": "初始对话",
            "text": initial_text,
            "at": str(state.get("created_at") or state.get("started_at") or "") if isinstance(state, dict) else "",
        })
    for job in jobs:
        history.append({
            "role": "runtime",
            "title": f"任务 {job.get('operation') or job.get('id')}",
            "text": f"状态：{job.get('status') or 'unknown'}",
            "at": str(job.get("started_at") or ""),
        })
    pending = _read_json(session_dir / "pending-question.json")
    if isinstance(pending, dict) and pending.get("question"):
        history.append({
            "role": "runtime",
            "title": "等待确认",
            "text": str(pending.get("question") or ""),
            "at": str(pending.get("created_at") or ""),
        })
    answer = _read_json(session_dir / "pending-question-answer.json")
    if isinstance(answer, dict) and answer.get("answer"):
        history.append({
            "role": "user",
            "title": "你的回答",
            "text": str(answer.get("answer") or ""),
            "at": str(answer.get("created_at") or ""),
        })
    summary = _read_json(session_dir / "summary.json")
    if isinstance(summary, dict) and summary.get("notes"):
        history.append({
            "role": "runtime",
            "title": "任务总结",
            "text": str(summary.get("notes") or ""),
            "at": str(summary.get("created_at") or ""),
        })
    return history[-40:]


def _conversation_events_for_ui(
    session_dir: Path,
    state: object,
    summary: object,
    jobs: list[dict[str, object]],
    *,
    max_events: int = 900,
) -> list[dict[str, object]]:
    """Build a single user-facing event stream from persisted runtime evidence.

    This is intentionally a UI projection. It does not change the runtime's core
    execution semantics, and it does not invent hidden model reasoning that the
    provider did not expose.
    """

    rows: list[dict[str, object]] = []
    seq = 0
    state_data = state if isinstance(state, dict) else {}
    summary_data = summary if isinstance(summary, dict) else {}

    def add(
        *,
        timestamp: str = "",
        role: str = "runtime",
        kind: str = "event",
        title: str = "",
        text: str = "",
        status: str = "",
        source: str = "",
        data: dict[str, object] | None = None,
        priority: int = 50,
    ) -> None:
        nonlocal seq
        clean_text = _bounded_text(text)
        clean_data = _bounded_json(data or {})
        if not clean_text and not clean_data:
            return
        seq += 1
        rows.append(
            {
                "id": f"{_slug(source or kind or 'event')}-{seq:05d}",
                "timestamp": timestamp,
                "role": role,
                "kind": kind,
                "title": title or _conversation_title(kind),
                "text": clean_text,
                "status": status,
                "source": source,
                "data": clean_data,
                "priority": priority,
                "_seq": seq,
            }
        )

    metadata = state_data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    initial_text = "\n".join(
        part
        for part in [
            str(metadata.get("invocation") or "").strip(),
            str(metadata.get("arguments") or "").strip(),
        ]
        if part
    )
    if not initial_text:
        initial_text = "\n".join(
            part
            for part in [
                str(summary_data.get("command") or "").strip(),
                str(summary_data.get("arguments") or "").strip(),
            ]
            if part
        )
    if initial_text:
        add(
            timestamp=str(state_data.get("created_at") or summary_data.get("created_at") or ""),
            role="user",
            kind="user_message",
            title="你",
            text=initial_text,
            source="session.metadata",
            priority=5,
        )

    for job in jobs:
        text = f"operation={job.get('operation') or ''}\nstatus={job.get('status') or 'unknown'}"
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        if metadata:
            text += "\n" + json.dumps(_compact_runtime_data(metadata), ensure_ascii=False, indent=2)
        add(
            timestamp=str(job.get("started_at") or ""),
            role="runtime",
            kind="job",
            title="运行任务",
            text=text,
            status=str(job.get("status") or ""),
            source=str(job.get("id") or "job"),
            data={"job_id": job.get("id"), "pid": job.get("pid")},
            priority=10,
        )

    for event in _read_jsonl(session_dir / "events.jsonl", limit=2000):
        event_type = str(event.get("type") or "")
        timestamp = str(event.get("timestamp") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        message = str(event.get("message") or "")
        if event_type == "transcript.user":
            continue
        if event_type == "transcript.assistant":
            preview = str(data.get("preview") or "")
            assistant_kind = _assistant_message_kind(preview)
            add(
                timestamp=timestamp,
                role="assistant" if assistant_kind == "assistant_message" else "runtime",
                kind=assistant_kind,
                title=f"模型回复 {data.get('label') or ''}".strip() if assistant_kind == "assistant_message" else "模型动作",
                text=_assistant_preview_text(preview),
                status=_returncode_status(data.get("returncode")),
                source=str(data.get("last_message_path") or event_type),
                data={
                    "label": data.get("label"),
                    "returncode": data.get("returncode"),
                    "terminal_event": data.get("terminal_event"),
                    "failure_reason": data.get("failure_reason"),
                    "stdout_path": data.get("stdout_path"),
                    "stderr_path": data.get("stderr_path"),
                    "last_message_path": data.get("last_message_path"),
                },
                priority=35 if assistant_kind == "assistant_message" else 47,
            )
            continue
        if event_type == "assistant.brief":
            add(
                timestamp=timestamp,
                role="assistant",
                kind="assistant_message",
                title=str(data.get("title") or "模型进度"),
                text=message,
                source=str(data.get("path") or event_type),
                data=_compact_runtime_data(data),
                priority=34,
            )
            continue
        if event_type == "codex.prepare":
            add(
                timestamp=timestamp,
                role="runtime",
                kind="model_start",
                title="模型调用开始",
                text=message,
                source=event_type,
                data={"command": _command_preview(data.get("command"))},
                priority=25,
            )
            continue
        if event_type in {"codex.finish", "codex.failure"}:
            failure = str(data.get("failure_reason") or "").strip()
            text = message
            if failure:
                text += f"\n{failure}"
            add(
                timestamp=timestamp,
                role="runtime",
                kind="model_finish" if event_type == "codex.finish" else "error",
                title="模型调用结束" if event_type == "codex.finish" else "模型调用失败",
                text=text,
                status=_returncode_status(data.get("returncode", data.get("effective_returncode"))),
                source=str(data.get("stdout") or event_type),
                data={
                    "returncode": data.get("returncode", data.get("effective_returncode")),
                    "raw_returncode": data.get("raw_returncode"),
                    "terminal_event": data.get("terminal_event"),
                    "stdout": data.get("stdout"),
                    "stderr": data.get("stderr"),
                    "last_message": data.get("last_message"),
                },
                priority=45,
            )
            _add_codex_stdout_events(add, timestamp, data, base_priority=46)
            continue
        if event_type == "tool.start":
            params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
            tool = _tool_name_from_event(message, params)
            add(
                timestamp=timestamp,
                role="tool",
                kind="tool_call",
                title=f"调用工具 {tool}",
                text=_tool_call_text(tool, params),
                source=event_type,
                data={"tool": tool, "parameters": _compact_runtime_data(params)},
                priority=55,
            )
            continue
        if event_type == "tool.finish":
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            tool = str(result.get("tool") or _tool_name_from_event(message, {}))
            add(
                timestamp=timestamp,
                role="tool",
                kind="tool_result",
                title=f"工具结果 {tool}",
                text=_tool_result_text(result, message),
                status=str(result.get("status") or ""),
                source=event_type,
                data=_compact_runtime_data(result),
                priority=56,
            )
            continue
        if event_type == "hook":
            add(
                timestamp=timestamp,
                role="runtime",
                kind="hook",
                title="Hook",
                text=_hook_text(message, data),
                status=_returncode_status(data.get("returncode")),
                source=str(data.get("source") or event_type),
                data=_compact_runtime_data(data),
                priority=60,
            )
            continue
        if event_type == "question.pending":
            add(
                timestamp=timestamp,
                role="runtime",
                kind="question",
                title="需要你回答",
                text=str(data.get("question") or message),
                status="waiting_user",
                source=event_type,
                data=_compact_runtime_data(data),
                priority=20,
            )
            continue
        if event_type.startswith(("session.", "skill.", "memory.", "plan.", "bridge.", "voice.", "ide.", "mcp.", "microcompact.", "tool_transcript.")):
            add(
                timestamp=timestamp,
                role="runtime",
                kind=_event_kind(event_type),
                title=_conversation_title(_event_kind(event_type), event_type),
                text=message,
                status=str(data.get("status") or ""),
                source=event_type,
                data=_compact_runtime_data(data),
                priority=65,
            )

    for item in _read_jsonl(session_dir / "tool-transcript.jsonl", limit=2000):
        event = str(item.get("event") or "")
        tool = str(item.get("tool") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if event == "tool_use":
            add(
                timestamp=str(item.get("timestamp") or ""),
                role="tool",
                kind="tool_call",
                title=f"调用工具 {tool}",
                text=_tool_call_text(tool, payload.get("parameters") if isinstance(payload.get("parameters"), dict) else payload),
                source=f"tool-transcript:{item.get('tool_id') or ''}",
                data={"tool": tool, "tool_id": item.get("tool_id"), "payload": _compact_runtime_data(payload)},
                priority=57,
            )
        elif event == "tool_result":
            add(
                timestamp=str(item.get("timestamp") or ""),
                role="tool",
                kind="tool_result",
                title=f"工具结果 {tool}",
                text=_tool_result_text(payload, str(payload.get("summary") or "")),
                status=str(payload.get("status") or ""),
                source=f"tool-transcript:{item.get('tool_id') or ''}",
                data={"tool": tool, "tool_id": item.get("tool_id"), "payload": _compact_runtime_data(payload)},
                priority=58,
            )

    pending = _read_json(session_dir / "pending-question.json")
    answer = _read_json(session_dir / "pending-question-answer.json")
    if isinstance(pending, dict) and pending.get("question") and not (isinstance(answer, dict) and answer.get("status") == "answered"):
        add(
            timestamp=str(pending.get("created_at") or ""),
            role="runtime",
            kind="question",
            title="需要你回答",
            text=str(pending.get("question") or ""),
            status="waiting_user",
            source="pending-question.json",
            data=_compact_runtime_data(pending),
            priority=15,
        )
    if isinstance(answer, dict) and answer.get("answer"):
        add(
            timestamp=str(answer.get("answered_at") or answer.get("created_at") or ""),
            role="user",
            kind="answer",
            title="你的回答",
            text=str(answer.get("answer") or ""),
            source="pending-question-answer.json",
            data=_compact_runtime_data(answer),
            priority=16,
        )

    if summary_data.get("notes"):
        add(
            timestamp=str(summary_data.get("updated_at") or summary_data.get("created_at") or ""),
            role="runtime",
            kind="summary",
            title="任务总结",
            text=str(summary_data.get("notes") or ""),
            status=str(summary_data.get("status") or ""),
            source="summary.json",
            data=_compact_runtime_data(_compact_summary(summary_data)),
            priority=90,
        )

    return _dedupe_conversation_rows(rows, max_events=max_events)


def _add_codex_stdout_events(add, fallback_timestamp: str, data: dict[str, object], *, base_priority: int) -> None:
    stdout_path = _path_or_none(data.get("stdout"))
    if stdout_path is None or not stdout_path.exists():
        return
    for index, item in enumerate(_read_jsonl(stdout_path, limit=400), start=1):
        event_type = str(item.get("type") or "")
        timestamp = str(item.get("timestamp") or "") or fallback_timestamp
        if event_type == "item.completed":
            item_data = item.get("item") if isinstance(item.get("item"), dict) else {}
            item_type = str(item_data.get("type") or "")
            text = _extract_codex_item_text(item_data)
            if not text:
                continue
            parsed = _try_parse_json_object(text)
            if item_type in {"agent_message", "message"}:
                assistant_kind = _assistant_message_kind(text)
                add(
                    timestamp=timestamp,
                    role="assistant" if assistant_kind == "assistant_message" else "runtime",
                    kind=assistant_kind,
                    title="模型消息" if assistant_kind == "assistant_message" else "模型动作",
                    text=_assistant_preview_text(text),
                    source=str(stdout_path),
                    data={"stdout": str(stdout_path), "item_id": item_data.get("id"), "parsed": _compact_runtime_data(parsed or {})},
                    priority=base_priority + index,
                )
            elif item_type in {"reasoning", "reasoning_summary", "summary"}:
                add(
                    timestamp=timestamp,
                    role="assistant",
                    kind="reasoning",
                    title="可见推理摘要",
                    text=text,
                    source=str(stdout_path),
                    data={"stdout": str(stdout_path), "item_id": item_data.get("id"), "item_type": item_type},
                    priority=base_priority + index,
                )
        elif event_type in {"turn.started", "turn.completed", "turn.failed", "turn.cancelled", "response.failed"}:
            text = event_type
            if event_type == "turn.completed" and isinstance(item.get("usage"), dict):
                usage = item["usage"]
                text = (
                    f"{event_type}\n"
                    f"input_tokens={usage.get('input_tokens', '-')}, "
                    f"cached_input_tokens={usage.get('cached_input_tokens', '-')}, "
                    f"output_tokens={usage.get('output_tokens', '-')}, "
                    f"reasoning_output_tokens={usage.get('reasoning_output_tokens', '-')}"
                )
            elif event_type in {"turn.failed", "response.failed"}:
                text = _failure_text_from_stream_event(item)
            add(
                timestamp=timestamp,
                role="runtime",
                kind="model_stream",
                title=_conversation_title("model_stream", event_type),
                text=text,
                status="failed" if event_type.endswith("failed") else ("done" if event_type == "turn.completed" else "running"),
                source=str(stdout_path),
                data=_compact_runtime_data(item),
                priority=base_priority + index,
            )


def _dedupe_conversation_rows(rows: list[dict[str, object]], *, max_events: int) -> list[dict[str, object]]:
    def sort_key(row: dict[str, object]) -> tuple[str, int, int]:
        return (str(row.get("timestamp") or ""), int(row.get("priority") or 50), int(row.get("_seq") or 0))

    selected = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in sorted(rows, key=sort_key):
        key = (
            str(row.get("timestamp") or ""),
            str(row.get("kind") or ""),
            str(row.get("title") or ""),
            str(row.get("text") or "")[:1200],
        )
        if key in seen:
            continue
        seen.add(key)
        clean = {key_: value for key_, value in row.items() if not key_.startswith("_")}
        selected.append(clean)
    return selected[-max_events:]


def _conversation_title(kind: str, detail: str = "") -> str:
    labels = {
        "user_message": "你",
        "answer": "你的回答",
        "assistant_message": "模型回复",
        "reasoning": "可见推理摘要",
        "model_start": "模型调用开始",
        "model_finish": "模型调用结束",
        "model_stream": "模型流事件",
        "tool_call": "工具调用",
        "tool_result": "工具结果",
        "hook": "Hook",
        "question": "需要你回答",
        "job": "运行任务",
        "summary": "任务总结",
        "session": "会话状态",
        "memory": "记忆",
        "skill": "Skill",
        "plan": "计划",
        "bridge": "Bridge",
        "voice": "Voice",
        "ide": "IDE",
        "mcp": "MCP",
        "error": "错误",
        "event": "事件",
    }
    if detail and kind == "model_stream":
        if detail == "turn.started":
            return "模型开始思考"
        if detail == "turn.completed":
            return "模型完成"
        if detail in {"turn.failed", "response.failed"}:
            return "模型失败"
    return labels.get(kind, detail or kind or "事件")


def _event_kind(event_type: str) -> str:
    head = event_type.split(".", 1)[0]
    if head in {"session", "memory", "skill", "plan", "bridge", "voice", "ide", "mcp"}:
        return head
    if head == "microcompact":
        return "memory"
    return "event"


def _bounded_text(value: object, *, max_chars: int = 20000) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[TRUNCATED FOR UI]\n"


def _bounded_json(value: dict[str, object], *, max_chars: int = 30000) -> dict[str, object]:
    if not value:
        return {}
    compact = _compact_runtime_data(value)
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return compact if isinstance(compact, dict) else {"value": compact}
    return {"truncated": True, "preview": encoded[:max_chars]}


def _compact_runtime_data(value: object, *, max_string: int = 5000, max_items: int = 60) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["__truncated_items__"] = len(value) - max_items
                break
            lowered = str(key).lower()
            if lowered in {"content", "prompt", "body", "rendered_body", "html"}:
                result[str(key)] = _bounded_text(item, max_chars=min(max_string, 2000))
            else:
                result[str(key)] = _compact_runtime_data(item, max_string=max_string, max_items=max_items)
        return result
    if isinstance(value, list):
        rows = [_compact_runtime_data(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            rows.append({"__truncated_items__": len(value) - max_items})
        return rows
    if isinstance(value, str):
        return _bounded_text(value, max_chars=max_string)
    return value


def _command_preview(command: object) -> list[str]:
    if not isinstance(command, list):
        return []
    result = []
    for item in command:
        text = str(item)
        result.append("[REDACTED]" if _looks_secret_text(text) else text)
    return result[:120]


def _looks_secret_text(value: str) -> bool:
    lowered = value.lower()
    return any(part in lowered for part in ("api_key=", "apikey=", "token=", "secret=", "password=", "authorization="))


def _assistant_preview_text(value: str) -> str:
    text = str(value or "").strip()
    parsed = _try_parse_json_object(text)
    if not parsed:
        return text
    if parsed.get("status") == "final" and parsed.get("final"):
        return str(parsed.get("final") or "")
    parts = []
    if parsed.get("summary"):
        parts.append(str(parsed.get("summary") or ""))
    actions = parsed.get("actions")
    if isinstance(actions, list) and actions:
        lines = ["请求执行："]
        for action in actions[:12]:
            if not isinstance(action, dict):
                continue
            tool = str(action.get("tool") or action.get("type") or "tool")
            parameters = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
            lines.append(f"- {tool}: {_short_parameters(parameters)}")
        if len(actions) > 12:
            lines.append(f"- ... 还有 {len(actions) - 12} 个动作")
        parts.append("\n".join(lines))
    if parsed.get("final"):
        parts.append(str(parsed.get("final") or ""))
    if not parts:
        parts.append(json.dumps(parsed, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


def _assistant_message_kind(value: str) -> str:
    parsed = _try_parse_json_object(str(value or ""))
    if not parsed:
        return "assistant_message"
    status = str(parsed.get("status") or "")
    if status == "final":
        return "assistant_message"
    return "model_stream"


def _short_parameters(parameters: dict[str, object]) -> str:
    if not parameters:
        return ""
    preferred = []
    for key in ("path", "pattern", "command", "question", "agent", "purpose", "name", "url", "tool"):
        if key in parameters:
            preferred.append(f"{key}={_bounded_text(parameters.get(key), max_chars=180)!r}")
    if preferred:
        return ", ".join(preferred)
    return json.dumps(_compact_runtime_data(parameters, max_string=160, max_items=6), ensure_ascii=False)


def _tool_call_text(tool: str, parameters: object) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    if tool == "bash" and "command" in params:
        return str(params.get("command") or "")
    if tool in {"write_file", "edit_file", "read_file"} and "path" in params:
        return f"{tool} {params.get('path')}"
    if tool == "ask_user_question" and "question" in params:
        return str(params.get("question") or "")
    return _short_parameters(params)


def _tool_result_text(result: dict[str, object], fallback: str = "") -> str:
    summary = str(result.get("summary") or fallback or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    lines = [summary] if summary else []
    if "stdout" in data and data.get("stdout"):
        lines.extend(["", "stdout:", str(data.get("stdout") or "")[-12000:]])
    if "stderr" in data and data.get("stderr"):
        lines.extend(["", "stderr:", str(data.get("stderr") or "")[-12000:]])
    if "path" in data and data.get("path"):
        lines.append(f"path: {data.get('path')}")
    if "bytes" in data:
        lines.append(f"bytes: {data.get('bytes')}")
    return "\n".join(lines).strip()


def _hook_text(message: str, data: dict[str, object]) -> str:
    lines = [message]
    for key in ("stdout", "stderr", "decision", "permission_decision", "updated_input"):
        value = data.get(key)
        if value:
            lines.extend(["", f"{key}:", str(value)])
    return "\n".join(lines).strip()


def _tool_name_from_event(message: str, parameters: dict[str, object]) -> str:
    if ":" in message:
        return message.split(":", 1)[0].strip() or "tool"
    return str(parameters.get("tool") or "tool")


def _returncode_status(value: object) -> str:
    try:
        return "done" if int(value) == 0 else "failed"
    except (TypeError, ValueError):
        return ""


def _try_parse_json_object(value: str) -> dict[str, object] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_codex_item_text(item: dict[str, object]) -> str:
    for key in ("text", "content", "message", "summary"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = item.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                for key in ("text", "content", "summary"):
                    value = part.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value)
                        break
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts).strip()
    return ""


def _failure_text_from_stream_event(item: dict[str, object]) -> str:
    for key in ("message", "reason", "summary", "detail", "details"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    error = item.get("error")
    if isinstance(error, dict):
        for key in ("message", "reason", "summary", "detail", "details"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    response = item.get("response")
    if isinstance(response, dict) and isinstance(response.get("error"), dict):
        value = response["error"].get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(_compact_runtime_data(item), ensure_ascii=False, indent=2)


def _tree_or_replay(session_dir: Path) -> dict[str, object]:
    tree = _read_json(session_dir / "task-tree.json")
    if isinstance(tree, dict) and isinstance(tree.get("nodes"), list):
        return tree
    return _derive_tree_from_events(session_dir)


def _tree_for_ui(session_dir: Path, active_job: dict[str, object] | None) -> dict[str, object]:
    tree = _tree_or_replay(session_dir)
    if active_job:
        return tree
    nodes = tree.get("nodes") if isinstance(tree, dict) else None
    if not isinstance(nodes, list):
        return tree
    result = dict(tree)
    normalized_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            normalized_nodes.append(node)
            continue
        normalized = dict(node)
        if normalized.get("status") in {"starting", "running", "cancel_requested"}:
            normalized["stale_status"] = normalized.get("status")
            normalized["status"] = "stale"
        normalized_nodes.append(normalized)
    result["nodes"] = normalized_nodes
    return result


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
        return _portable_path(configured, fallback=state_root / "task-workspaces")
    return state_root / "task-workspaces"


def _projects_root(runtime_env: Path, state_root: Path) -> Path:
    values = _load_env(runtime_env)
    configured = values.get("SKILL_RUNTIME_PROJECTS_ROOT") or values.get("SKILL_RUNTIME_PROJECT_ROOT") or ""
    if configured:
        return _portable_path(configured, fallback=WORKSPACE_ROOT / "runtime-projects")
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
        fallback_root = _projects_root(runtime_env, state_root)
        resolved_save_root = _portable_path(save_root, fallback=fallback_root if project_id == "default" else fallback_root / project_id)
        normalized.append(
            {
                "id": project_id,
                "name": str(item.get("name") or project_id),
                "save_root": str(resolved_save_root),
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


def _project_by_id_exact(runtime_env: Path, state_root: Path, project_id: str) -> dict[str, object] | None:
    wanted = str(project_id or "").strip()
    if not wanted:
        return None
    config = _load_projects_config(runtime_env, state_root)
    for item in config.get("projects", []):
        if isinstance(item, dict) and str(item.get("id") or "") == wanted:
            return item
    return None


def _project_save_roots(runtime_env: Path, state_root: Path) -> list[Path]:
    config = _load_projects_config(runtime_env, state_root)
    roots: list[Path] = []
    for item in config.get("projects", []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("save_root") or "").strip()
        if value:
            roots.append(_portable_path(value, fallback=_projects_root(runtime_env, state_root)))
    return _unique_paths(roots)


def _deletable_workspace_path(path: Path, runtime_env: Path, state_root: Path) -> bool:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        return False
    marker = resolved / ".skill-runtime-task.json"
    managed_roots = [
        _default_save_root(runtime_env, state_root),
        _projects_root(runtime_env, state_root),
        *_project_save_roots(runtime_env, state_root),
    ]
    if marker.exists() and any(_is_under(resolved, root) for root in managed_roots):
        return True
    return any(_is_under(resolved, root / "") and resolved.parent == root for root in managed_roots)


def _unique_trash_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.name}-{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def _plain_text(value: object) -> str:
    if isinstance(value, list):
        return " ".join(_plain_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {_plain_text(item)}" for key, item in value.items())
    return str(value or "")


def _unique_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _session_belongs_to_project(state: object, summary: object, project: dict[str, object] | None) -> bool:
    if not project:
        return True
    project_id = str(project.get("id") or "")
    save_root = _path_or_none(project.get("save_root")) if project.get("save_root") else None
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
        return _portable_path(text)
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
        return _portable_path(raw, fallback=_default_save_root(runtime_env, state_root))
    if project and project.get("save_root"):
        return _portable_path(str(project["save_root"]), fallback=_projects_root(runtime_env, state_root) / str(project.get("id") or "default"))
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
    candidates = []
    for path in sessions_root.glob(f"*{session_id}*"):
        try:
            if path.is_dir():
                candidates.append((path, path.stat().st_mtime))
        except FileNotFoundError:
            continue
    matches = [path for path, _ in sorted(candidates, key=lambda item: item[1], reverse=True)]
    return matches[0] if matches else None


def _existing_session_dirs(sessions_root: Path) -> list[Path]:
    result: list[Path] = []
    if not sessions_root.exists():
        return result
    for path in sessions_root.iterdir():
        try:
            if path.is_dir():
                result.append(path)
        except FileNotFoundError:
            continue
    return result


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_session_event(session_dir: Path, type_: str, message: str, **data: object) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    record = {"type": type_, "message": message, "data": data, "timestamp": now}
    for name in ("events.jsonl", "transcript.jsonl"):
        target = session_dir / name
        try:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            continue


def _sync_terminal_job_to_sessions(sessions_root: Path, job: dict[str, object], *, status: str, reason: str) -> None:
    for session_dir in _session_dirs_for_job(sessions_root, job):
        _sync_terminal_job_to_session(session_dir, job, status=status, reason=reason)


def _session_dirs_for_job(sessions_root: Path, job: dict[str, object]) -> list[Path]:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    session_id = str(metadata.get("session_id") or "").strip() if isinstance(metadata, dict) else ""
    matched: list[Path] = []
    if session_id:
        session_dir = _find_session_dir(sessions_root, session_id)
        if session_dir is not None:
            matched.append(session_dir)

    workspace_values: set[str] = set()
    if isinstance(metadata, dict):
        for key in ("target_workspace", "task_workspace"):
            value = str(metadata.get(key) or "").strip()
            if value:
                workspace_values.add(value)
    if not workspace_values:
        return matched

    for session_dir in _existing_session_dirs(sessions_root):
        if session_dir in matched:
            continue
        state = _read_json(session_dir / "session-state.json")
        if not isinstance(state, dict):
            continue
        values = {str(state.get("root") or "").strip()}
        state_metadata = state.get("metadata")
        if isinstance(state_metadata, dict):
            values.add(str(state_metadata.get("target_workspace") or "").strip())
        if any(value and value in workspace_values for value in values):
            matched.append(session_dir)
    return matched


def _sync_terminal_job_to_session(session_dir: Path, job: dict[str, object], *, status: str, reason: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    ui_status = status if status in {"done", "failed", "cancelled", "blocked", "unknown"} else "failed"
    state = _read_json(session_dir / "session-state.json")
    active_ids: set[str] = set()
    if isinstance(state, dict):
        active_ids = {str(item) for item in state.get("active_node_ids", []) if str(item)}
        state["status"] = ui_status
        state["current_agents"] = []
        state["active_node_ids"] = []
        state["waiting_question"] = None
        state["last_event"] = reason
        state["updated_at"] = now
        _write_json(session_dir / "session-state.json", state)

    tree = _read_json(session_dir / "task-tree.json")
    if isinstance(tree, dict) and isinstance(tree.get("nodes"), list):
        for node in tree["nodes"]:
            if not isinstance(node, dict):
                continue
            node_status = str(node.get("status") or "")
            if node_status in {"running", "starting", "cancel_requested", "queued", "waiting_user"} or str(node.get("id") or "") in active_ids:
                node["status"] = "cancelled" if ui_status == "cancelled" else ui_status
                node["finished_at"] = node.get("finished_at") or now
                evidence = node.get("evidence") if isinstance(node.get("evidence"), dict) else {}
                evidence.update({"job_status": ui_status, "reason": reason, "job_id": str(job.get("id") or "")})
                node["evidence"] = evidence
        tree["updated_at"] = now
        _write_json(session_dir / "task-tree.json", tree)

    _append_session_event(
        session_dir,
        "job.terminal",
        reason,
        job_id=str(job.get("id") or ""),
        status=ui_status,
        returncode=job.get("returncode"),
    )


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


def _url_ok(url: str, *, timeout: int = 2) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    try:
        request = Request(text, method="GET")
        with urlopen(request, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _terminate_process_tree(pid: int, *, force: bool = False) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False)
        return
    try:
        children = subprocess.run(["pgrep", "-P", str(pid)], text=True, capture_output=True, check=False)
        for line in children.stdout.splitlines():
            if line.strip().isdigit():
                _terminate_process_tree(int(line.strip()), force=force)
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        return


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
    state.server = server
    print(f"Codex Skill Runtime UI: http://{args.host}:{args.port}")
    print(f"Runtime env: {runtime_env}")
    print(f"State root: {state.state_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        state.stop_all_managed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
