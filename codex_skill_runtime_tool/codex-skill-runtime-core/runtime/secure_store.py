from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_paths import runtime_state_path


@dataclass(frozen=True)
class TokenRecord:
    key: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    token_type: str = "Bearer"
    scope: str | None = None
    metadata: dict[str, Any] | None = None

    def authorization_header(self) -> str:
        token_type = self.token_type or "Bearer"
        if self.access_token.lower().startswith("bearer "):
            return self.access_token
        return f"{token_type} {self.access_token}"


class SecureTokenStore:
    """Small credential abstraction for MCP/bridge tokens.

    On Windows this store tries DPAPI through PowerShell ConvertFrom/To-SecureString.
    If DPAPI is unavailable, it falls back to a local JSON file. The fallback is
    intentionally explicit and deterministic so skills can still execute in CI or
    portable environments.
    """

    def __init__(self, project_root: Path, *, namespace: str = "mcp-oauth") -> None:
        self.project_root = project_root.resolve()
        self.namespace = namespace
        self.path = runtime_state_path(self.project_root, "secure-store", f"{namespace}.json")

    def read(self, key: str) -> TokenRecord | None:
        data = self._read_all().get(key)
        if not isinstance(data, dict):
            return None
        token = self._unprotect(str(data.get("access_token") or ""))
        if not token:
            return None
        refresh = data.get("refresh_token")
        return TokenRecord(
            key=key,
            access_token=token,
            refresh_token=self._unprotect(str(refresh)) if isinstance(refresh, str) and refresh else None,
            expires_at=float(data["expires_at"]) if data.get("expires_at") is not None else None,
            token_type=str(data.get("token_type") or "Bearer"),
            scope=str(data.get("scope")) if data.get("scope") else None,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )

    def write(self, record: TokenRecord) -> None:
        data = self._read_all()
        data[record.key] = {
            "access_token": self._protect(record.access_token),
            "refresh_token": self._protect(record.refresh_token) if record.refresh_token else None,
            "expires_at": record.expires_at,
            "token_type": record.token_type,
            "scope": record.scope,
            "metadata": record.metadata or {},
        }
        self._write_all(data)

    def delete(self, key: str) -> None:
        data = self._read_all()
        if key in data:
            del data[key]
            self._write_all(data)

    def list_keys(self) -> list[str]:
        return sorted(self._read_all().keys())

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_all(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _protect(self, value: str | None) -> str | None:
        if value is None:
            return None
        protected = _windows_dpapi_protect(value)
        if protected:
            return "dpapi:" + protected
        return "plain:" + value

    def _unprotect(self, value: str) -> str:
        if value.startswith("dpapi:"):
            return _windows_dpapi_unprotect(value.removeprefix("dpapi:")) or ""
        if value.startswith("plain:"):
            return value.removeprefix("plain:")
        return value


def stable_server_key(name: str, config: dict[str, Any]) -> str:
    public_config = {
        key: value
        for key, value in config.items()
        if key.lower() not in {"token", "accesstoken", "clientsecret", "refresh_token", "refreshToken"}
    }
    payload = json.dumps({"name": name, "config": public_config}, sort_keys=True, ensure_ascii=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)[:80] or "server"
    return f"{safe_name}-{digest}"


def _windows_dpapi_protect(value: str) -> str | None:
    if os.name != "nt":
        return None
    script = (
        "$s = [Console]::In.ReadToEnd(); "
        "$sec = ConvertTo-SecureString -String $s -AsPlainText -Force; "
        "ConvertFrom-SecureString -SecureString $sec"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            input=value,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return base64.b64encode(completed.stdout.strip().encode("utf-8")).decode("ascii")


def _windows_dpapi_unprotect(value: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        encrypted = base64.b64decode(value.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    script = (
        "$s = [Console]::In.ReadToEnd(); "
        "$sec = ConvertTo-SecureString -String $s; "
        "$b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec); "
        "try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } "
        "finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            input=encrypted,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.rstrip("\r\n")
