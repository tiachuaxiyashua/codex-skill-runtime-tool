from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .secure_store import SecureTokenStore, TokenRecord, stable_server_key
from .state_paths import runtime_state_path


def stored_oauth_headers(
    *,
    project_root: Path,
    server_name: str,
    config: dict[str, Any],
) -> dict[str, str]:
    key = stable_server_key(server_name, config)
    record = SecureTokenStore(project_root).read(key)
    if record is None:
        return {}
    if record.expires_at is not None and record.expires_at <= time.time() + 30:
        refreshed = refresh_oauth_token(project_root=project_root, server_name=server_name, config=config)
        if refreshed is None:
            return {}
        record = refreshed
    return {"Authorization": record.authorization_header()}


def refresh_oauth_token(
    *,
    project_root: Path,
    server_name: str,
    config: dict[str, Any],
    plugin_root: Path | None = None,
    server_url: str | None = None,
) -> TokenRecord | None:
    refreshed = _refresh_with_stored_refresh_token(
        project_root=project_root,
        server_name=server_name,
        config=config,
    )
    if refreshed is not None:
        return refreshed

    command = config.get("oauthRefreshCommand") or config.get("tokenCommand") or config.get("authCommand")
    if not command:
        return None
    output = run_auth_command(
        command=str(command),
        project_root=project_root,
        server_name=server_name,
        server_url=server_url or str(config.get("url") or config.get("uri") or ""),
        plugin_root=plugin_root,
    )
    record = token_record_from_auth_output(server_name=server_name, config=config, output=output)
    SecureTokenStore(project_root).write(record)
    return record


def _refresh_with_stored_refresh_token(
    *,
    project_root: Path,
    server_name: str,
    config: dict[str, Any],
) -> TokenRecord | None:
    store = SecureTokenStore(project_root)
    key = stable_server_key(server_name, config)
    current = store.read(key)
    if current is None or not current.refresh_token:
        return None
    oauth = config.get("oauth")
    oauth = oauth if isinstance(oauth, dict) else {}
    token_endpoint = (
        oauth.get("tokenUrl")
        or oauth.get("token_url")
        or oauth.get("tokenEndpoint")
        or config.get("tokenUrl")
        or config.get("token_endpoint")
    )
    if not token_endpoint:
        return None
    client_id = oauth.get("clientId") or oauth.get("client_id") or config.get("clientId") or config.get("client_id")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": current.refresh_token,
    }
    if client_id:
        form["client_id"] = str(client_id)
    client_secret = oauth.get("clientSecret") or oauth.get("client_secret") or config.get("clientSecret") or config.get("client_secret")
    if client_secret:
        form["client_secret"] = str(client_secret)
    request = urllib.request.Request(
        str(token_endpoint),
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            output = response.read(1024 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return None
    record = token_record_from_auth_output(server_name=server_name, config=config, output=output)
    if not record.refresh_token:
        record = TokenRecord(
            key=record.key,
            access_token=record.access_token,
            refresh_token=current.refresh_token,
            expires_at=record.expires_at,
            token_type=record.token_type,
            scope=record.scope,
            metadata=record.metadata,
        )
    store.write(record)
    return record


def run_auth_command(
    *,
    command: str,
    project_root: Path,
    server_name: str,
    server_url: str,
    plugin_root: Path | None,
    timeout: int = 30,
) -> str:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    env["CLAUDE_CODE_MCP_SERVER_NAME"] = server_name
    env["CLAUDE_CODE_MCP_SERVER_URL"] = server_url
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError(f"auth command failed with exit {completed.returncode}: {(completed.stderr or completed.stdout)[-1000:]}")
    return completed.stdout.strip()


def token_record_from_auth_output(*, server_name: str, config: dict[str, Any], output: str) -> TokenRecord:
    key = stable_server_key(server_name, config)
    try:
        data = json.loads(output)
    except ValueError:
        return TokenRecord(key=key, access_token=output)
    if isinstance(data, str):
        return TokenRecord(key=key, access_token=data)
    if not isinstance(data, dict):
        raise ValueError("auth command must return token string or JSON object")
    token = data.get("accessToken") or data.get("access_token") or data.get("token")
    if not token and isinstance(data.get("headers"), dict):
        auth = data["headers"].get("Authorization") or data["headers"].get("authorization")
        if isinstance(auth, str):
            token = auth.removeprefix("Bearer ").removeprefix("bearer ")
    if not token:
        raise ValueError("auth command JSON did not include accessToken/access_token/token or headers.Authorization")
    expires_at = data.get("expiresAt") or data.get("expires_at")
    expires_in = data.get("expiresIn") or data.get("expires_in")
    if expires_at is None and expires_in is not None:
        try:
            expires_at = time.time() + float(expires_in)
        except (TypeError, ValueError):
            expires_at = None
    return TokenRecord(
        key=key,
        access_token=str(token),
        refresh_token=str(data.get("refreshToken") or data.get("refresh_token") or "") or None,
        expires_at=float(expires_at) if expires_at is not None else None,
        token_type=str(data.get("tokenType") or data.get("token_type") or "Bearer"),
        scope=str(data.get("scope")) if data.get("scope") else None,
        metadata={key: value for key, value in data.items() if key not in {"accessToken", "access_token", "token", "refreshToken", "refresh_token"}},
    )


def clear_oauth_token(*, project_root: Path, server_name: str, config: dict[str, Any]) -> None:
    SecureTokenStore(project_root).delete(stable_server_key(server_name, config))


def start_oauth_authorization(
    *,
    project_root: Path,
    server_name: str,
    config: dict[str, Any],
    plugin_root: Path | None = None,
    server_url: str | None = None,
) -> dict[str, Any]:
    """Start an execution-level MCP OAuth flow.

    This mirrors Claude Code's observable pseudo-tool behavior: return an auth
    URL when browser consent is required, or silently store credentials when an
    auth/token command can complete the flow non-interactively.
    """

    existing = stored_oauth_headers(project_root=project_root, server_name=server_name, config=config)
    if existing:
        return {
            "status": "authenticated",
            "message": f"MCP server `{server_name}` already has a stored OAuth/access token.",
        }

    command = config.get("authCommand") or config.get("oauthRefreshCommand") or config.get("tokenCommand")
    if command:
        try:
            record = refresh_oauth_token(
                project_root=project_root,
                server_name=server_name,
                config=config,
                plugin_root=plugin_root,
                server_url=server_url,
            )
        except Exception as exc:
            return {"status": "error", "message": f"Auth command failed for `{server_name}`: {exc}"}
        if record is not None:
            return {
                "status": "authenticated",
                "message": f"MCP server `{server_name}` authenticated through authCommand/tokenCommand.",
                "key": record.key,
                "expires_at": record.expires_at,
            }

    oauth = _oauth_config(config)
    remote_url = server_url or str(config.get("url") or config.get("uri") or "")
    transport = str(config.get("type") or config.get("transport") or "").lower()
    if transport not in {"http", "sse"} and not remote_url.startswith(("http://", "https://")):
        return {
            "status": "unsupported",
            "message": f"MCP server `{server_name}` does not use HTTP/SSE OAuth-compatible transport.",
        }

    metadata = _discover_metadata(oauth, remote_url)
    authorization_endpoint = (
        oauth.get("authorizationUrl")
        or oauth.get("authorization_url")
        or oauth.get("authorizationEndpoint")
        or metadata.get("authorization_endpoint")
    )
    token_endpoint = (
        oauth.get("tokenUrl")
        or oauth.get("token_url")
        or oauth.get("tokenEndpoint")
        or metadata.get("token_endpoint")
    )
    client_id = str(oauth.get("clientId") or oauth.get("client_id") or config.get("clientId") or "")
    if not authorization_endpoint or not token_endpoint:
        return {
            "status": "unsupported",
            "message": (
                f"MCP server `{server_name}` needs OAuth, but no authorization/token endpoint was discoverable. "
                "Configure oauth.authServerMetadataUrl, oauth.authorizationUrl/oauth.tokenUrl, authCommand, or tokenCommand."
            ),
        }
    if not client_id:
        return {
            "status": "unsupported",
            "message": (
                f"MCP server `{server_name}` needs OAuth, but no oauth.clientId is configured. "
                "Dynamic client registration is not implemented in this lightweight runtime."
            ),
        }

    callback_port = int(oauth.get("callbackPort") or oauth.get("callback_port") or 0)
    redirect_uri = str(oauth.get("redirectUri") or oauth.get("redirect_uri") or "")
    if not redirect_uri:
        redirect_uri = f"http://127.0.0.1:{callback_port or 8765}/callback"

    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _pkce_challenge(code_verifier)
    scope_value = oauth.get("scope") or oauth.get("scopes")
    if isinstance(scope_value, list):
        scope = " ".join(str(item) for item in scope_value)
    else:
        scope = str(scope_value or "")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    if remote_url:
        params["resource"] = remote_url
    auth_url = str(authorization_endpoint) + ("&" if "?" in str(authorization_endpoint) else "?") + urllib.parse.urlencode(params)

    pending = {
        "server_name": server_name,
        "server_url": remote_url,
        "state": state,
        "client_id": client_id,
        "client_secret": str(oauth.get("clientSecret") or oauth.get("client_secret") or ""),
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "token_endpoint": str(token_endpoint),
        "scope": scope,
        "created_at": time.time(),
        "config": config,
    }
    pending_path = _pending_path(project_root, server_name, config)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "auth_url",
        "message": (
            f"Open this URL in a browser to authorize MCP server `{server_name}`. "
            "After authorization, complete the flow with the returned code/callback URL."
        ),
        "authUrl": auth_url,
        "pending_path": str(pending_path),
        "redirect_uri": redirect_uri,
    }


def complete_oauth_authorization(
    *,
    project_root: Path,
    server_name: str,
    config: dict[str, Any],
    code: str | None = None,
    callback_url: str | None = None,
) -> TokenRecord:
    pending_path = _pending_path(project_root, server_name, config)
    if not pending_path.exists():
        raise RuntimeError(f"No pending OAuth flow found for `{server_name}`.")
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    if callback_url:
        parsed = urllib.parse.urlparse(callback_url)
        values = urllib.parse.parse_qs(parsed.query)
        code = values.get("code", [code or ""])[0]
        state = values.get("state", [""])[0]
        if state and state != pending.get("state"):
            raise RuntimeError(f"OAuth state mismatch for `{server_name}`.")
    if not code:
        raise RuntimeError("OAuth completion requires an authorization code or callback URL.")

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": pending["client_id"],
        "code_verifier": pending["code_verifier"],
    }
    if pending.get("client_secret"):
        form["client_secret"] = pending["client_secret"]
    request = urllib.request.Request(
        pending["token_endpoint"],
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(1024 * 1024).decode("utf-8", errors="replace")
    record = token_record_from_auth_output(server_name=server_name, config=config, output=body)
    SecureTokenStore(project_root).write(record)
    try:
        pending_path.unlink()
    except OSError:
        pass
    return record


def _oauth_config(config: dict[str, Any]) -> dict[str, Any]:
    oauth = config.get("oauth")
    return oauth if isinstance(oauth, dict) else {}


def _discover_metadata(oauth: dict[str, Any], server_url: str) -> dict[str, Any]:
    metadata_url = oauth.get("authServerMetadataUrl") or oauth.get("metadataUrl") or oauth.get("metadata_url")
    if metadata_url:
        return _fetch_json(str(metadata_url))
    if oauth.get("authorizationUrl") and oauth.get("tokenUrl"):
        return {}
    if not server_url:
        return {}
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for candidate in [
        f"{origin}/.well-known/oauth-protected-resource",
        f"{origin}/.well-known/oauth-authorization-server",
    ]:
        try:
            data = _fetch_json(candidate)
        except Exception:
            continue
        if isinstance(data.get("authorization_servers"), list) and data["authorization_servers"]:
            issuer = str(data["authorization_servers"][0]).rstrip("/")
            try:
                return _fetch_json(f"{issuer}/.well-known/oauth-authorization-server")
            except Exception:
                return {}
        if data.get("authorization_endpoint") or data.get("token_endpoint"):
            return data
    return {}


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read(1024 * 1024).decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _pending_path(project_root: Path, server_name: str, config: dict[str, Any]) -> Path:
    key = stable_server_key(server_name, config)
    return runtime_state_path(project_root, "mcp-oauth", f"{key}.pending.json")
