from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .loaders import SkillRepositoryLoader
from .mcp_oauth import refresh_oauth_token, start_oauth_authorization, stored_oauth_headers, token_record_from_auth_output
from .secure_store import SecureTokenStore


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    aliases: tuple[str, ...]
    config: dict[str, Any]
    plugin_root: Path | None = None
    plugin_name: str | None = None


class MCPBridgeError(RuntimeError):
    pass


class MCPRemoteAuthError(MCPBridgeError):
    pass


def call_mcp_tool(
    *,
    project_root: Path,
    tool: str,
    arguments: dict[str, Any],
    timeout: int = 45,
    extra_servers: list[MCPServerConfig] | None = None,
    additional_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    server, tool_name = _resolve_tool(project_root=project_root, tool=tool, extra_servers=extra_servers, additional_dirs=additional_dirs)
    transport = _server_transport(server.config)
    if tool_name in {"authenticate", "auth", "mcp_authenticate"}:
        if transport in {"http", "sse", "websocket"}:
            url = _remote_url(
                server.config,
                "websocket" if transport == "websocket" else transport,
                project_root=project_root,
                plugin_root=server.plugin_root,
            )
        else:
            url = str(server.config.get("url") or server.config.get("uri") or "")
        return {
            "server": server.name,
            "transport": transport,
            "tool": tool_name,
            "result": start_oauth_authorization(
                project_root=project_root,
                server_name=server.name,
                config=server.config,
                plugin_root=server.plugin_root,
                server_url=url,
            ),
        }
    if transport == "stdio":
        return _call_stdio_mcp_tool(project_root=project_root, server=server, tool_name=tool_name, arguments=arguments, timeout=timeout)
    if transport == "http":
        return _call_http_mcp_tool(project_root=project_root, server=server, tool_name=tool_name, arguments=arguments, timeout=timeout)
    if transport == "sse":
        return _call_sse_mcp_tool(project_root=project_root, server=server, tool_name=tool_name, arguments=arguments, timeout=timeout)
    if transport == "websocket":
        return _call_websocket_mcp_tool(project_root=project_root, server=server, tool_name=tool_name, arguments=arguments, timeout=timeout)
    raise MCPBridgeError(f"MCP server `{server.name}` has unsupported transport `{transport}`.")


def _call_stdio_mcp_tool(
    *,
    project_root: Path,
    server: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    if "command" not in server.config:
        raise MCPBridgeError(f"MCP server `{server.name}` has no command.")

    command = _expand_env(str(server.config["command"]), project_root=project_root, plugin_root=server.plugin_root)
    args = [
        _expand_env(str(value), project_root=project_root, plugin_root=server.plugin_root)
        for value in server.config.get("args", [])
    ]
    env = dict(os.environ)
    if server.plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(server.plugin_root)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    for key, value in server.config.get("env", {}).items():
        env[str(key)] = _expand_env(str(value), project_root=project_root, plugin_root=server.plugin_root)

    process = subprocess.Popen(
        [command, *args],
        cwd=str(project_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )
    try:
        _send(process, 1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "codex-skill-runtime", "version": "0.1"}})
        initialize = _read_response(process, 1, timeout=timeout)
        _send_notification(process, "notifications/initialized", {})
        _send(process, 2, "tools/call", {"name": tool_name, "arguments": arguments})
        call = _read_response(process, 2, timeout=timeout)
        return {"server": server.name, "tool": tool_name, "initialize": initialize, "result": call}
    finally:
        _terminate(process)


def _call_http_mcp_tool(
    *,
    project_root: Path,
    server: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    url = _remote_url(server.config, "http", project_root=project_root, plugin_root=server.plugin_root)
    headers = _remote_headers(server, project_root=project_root)
    session_id: str | None = None
    initialize_payload = _jsonrpc_request(
        1,
        "initialize",
        {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "codex-skill-runtime", "version": "0.1"}},
    )
    try:
        initialize, response_headers = _post_json_rpc(url, initialize_payload, headers=headers, timeout=timeout, session_id=session_id)
    except MCPRemoteAuthError:
        refreshed = refresh_oauth_token(
            project_root=project_root,
            server_name=server.name,
            config=server.config,
            plugin_root=server.plugin_root,
            server_url=url,
        )
        if refreshed is None:
            raise
        headers = {**headers, "Authorization": refreshed.authorization_header()}
        initialize, response_headers = _post_json_rpc(url, initialize_payload, headers=headers, timeout=timeout, session_id=session_id)
    session_id = response_headers.get("mcp-session-id") or response_headers.get("Mcp-Session-Id")
    _post_json_rpc(url, _jsonrpc_notification("notifications/initialized", {}), headers=headers, timeout=timeout, session_id=session_id, expect_response=False)
    try:
        result, _ = _post_json_rpc(
            url,
            _jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}),
            headers=headers,
            timeout=timeout,
            session_id=session_id,
        )
    except MCPRemoteAuthError:
        refreshed = refresh_oauth_token(
            project_root=project_root,
            server_name=server.name,
            config=server.config,
            plugin_root=server.plugin_root,
            server_url=url,
        )
        if refreshed is None:
            raise
        headers = {**headers, "Authorization": refreshed.authorization_header()}
        result, _ = _post_json_rpc(
            url,
            _jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}),
            headers=headers,
            timeout=timeout,
            session_id=session_id,
        )
    return {"server": server.name, "transport": "http", "url": _sanitize_url(url), "tool": tool_name, "initialize": initialize, "result": result}


def _call_sse_mcp_tool(
    *,
    project_root: Path,
    server: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    url = _remote_url(server.config, "sse", project_root=project_root, plugin_root=server.plugin_root)
    headers = _remote_headers(server, project_root=project_root)

    def run_once(active_headers: dict[str, str]) -> dict[str, Any]:
        stream = _SSEClient(url, headers=active_headers, timeout=timeout)
        try:
            endpoint = stream.wait_endpoint()
            initialize_payload = _jsonrpc_request(
                1,
                "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "codex-skill-runtime", "version": "0.1"}},
            )
            _post_sse_message(endpoint, initialize_payload, headers=active_headers, timeout=timeout)
            initialize = stream.wait_response(1)
            _post_sse_message(endpoint, _jsonrpc_notification("notifications/initialized", {}), headers=active_headers, timeout=timeout)
            _post_sse_message(endpoint, _jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}), headers=active_headers, timeout=timeout)
            result = stream.wait_response(2)
            return {"server": server.name, "transport": "sse", "url": _sanitize_url(url), "tool": tool_name, "initialize": initialize, "result": result}
        finally:
            stream.close()

    try:
        return run_once(headers)
    except MCPRemoteAuthError:
        refreshed = refresh_oauth_token(
            project_root=project_root,
            server_name=server.name,
            config=server.config,
            plugin_root=server.plugin_root,
            server_url=url,
        )
        if refreshed is None:
            raise
        headers = {**headers, "Authorization": refreshed.authorization_header()}
        return run_once(headers)


def _call_websocket_mcp_tool(
    *,
    project_root: Path,
    server: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    try:
        import websocket  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MCPBridgeError(
            f"MCP server `{server.name}` uses WebSocket, but Python package `websocket-client` is not installed. "
            "Install it or use HTTP/SSE/stdout MCP for this plugin."
        ) from exc

    url = _remote_url(server.config, "websocket", project_root=project_root, plugin_root=server.plugin_root)
    headers = _remote_headers(server, project_root=project_root)

    def run_once(active_headers: dict[str, str]) -> dict[str, Any]:
        header_lines = [f"{key}: {value}" for key, value in active_headers.items()]
        ws = websocket.create_connection(url, header=header_lines, timeout=timeout)
        try:
            ws.send(json.dumps(_jsonrpc_request(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "codex-skill-runtime", "version": "0.1"}}), ensure_ascii=False))
            initialize = _websocket_wait_response(ws, 1, timeout=timeout)
            ws.send(json.dumps(_jsonrpc_notification("notifications/initialized", {}), ensure_ascii=False))
            ws.send(json.dumps(_jsonrpc_request(2, "tools/call", {"name": tool_name, "arguments": arguments}), ensure_ascii=False))
            result = _websocket_wait_response(ws, 2, timeout=timeout)
            return {"server": server.name, "transport": "websocket", "url": _sanitize_url(url), "tool": tool_name, "initialize": initialize, "result": result}
        finally:
            ws.close()

    try:
        return run_once(headers)
    except Exception as exc:
        if not _websocket_auth_error(exc):
            raise
        refreshed = refresh_oauth_token(
            project_root=project_root,
            server_name=server.name,
            config=server.config,
            plugin_root=server.plugin_root,
            server_url=url,
        )
        if refreshed is None:
            raise MCPRemoteAuthError(
                f"Remote WebSocket MCP server {_sanitize_url(url)} requires authentication or authorization."
            ) from exc
        headers = {**headers, "Authorization": refreshed.authorization_header()}
        return run_once(headers)


def discover_mcp_servers(
    project_root: Path,
    *,
    extra_servers: list[MCPServerConfig] | None = None,
    additional_dirs: list[Path] | None = None,
) -> list[MCPServerConfig]:
    root = project_root.resolve()
    servers: list[MCPServerConfig] = []
    root_config = root / ".mcp.json"
    if root_config.exists():
        servers.extend(_servers_from_config(root_config, root, plugin_root=None, plugin_name=None))

    loader = SkillRepositoryLoader(root, additional_dirs=additional_dirs)
    for plugin in loader.plugin_layouts():
        plugin_config = plugin.root / ".mcp.json"
        if plugin_config.exists():
            servers.extend(_servers_from_config(plugin_config, plugin.root, plugin_root=plugin.root, plugin_name=plugin.name))
        mcp_servers = plugin.manifest.get("mcpServers")
        if isinstance(mcp_servers, dict):
            if isinstance(mcp_servers.get("mcpServers"), dict):
                servers.extend(
                    _servers_from_mapping(
                        mcp_servers["mcpServers"],
                        plugin.root,
                        plugin_root=plugin.root,
                        plugin_name=plugin.name,
                    )
                )
            else:
                servers.extend(_servers_from_mapping(mcp_servers, plugin.root, plugin_root=plugin.root, plugin_name=plugin.name))
        elif isinstance(mcp_servers, list):
            for value in mcp_servers:
                config_path = _resolve_plugin_path(plugin.root, value)
                if config_path.exists():
                    servers.extend(_servers_from_config(config_path, plugin.root, plugin_root=plugin.root, plugin_name=plugin.name))
        elif mcp_servers:
            config_path = _resolve_plugin_path(plugin.root, mcp_servers)
            if config_path.exists():
                servers.extend(_servers_from_config(config_path, plugin.root, plugin_root=plugin.root, plugin_name=plugin.name))
    if extra_servers:
        servers.extend(extra_servers)
    return servers


def mcp_instructions_context(
    project_root: Path,
    *,
    extra_servers: list[MCPServerConfig] | None = None,
    additional_dirs: list[Path] | None = None,
    max_chars_per_server: int = 12000,
) -> str:
    blocks: list[str] = []
    for server in discover_mcp_servers(project_root, extra_servers=extra_servers, additional_dirs=additional_dirs):
        raw = server.config.get("instructions") or server.config.get("instruction")
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        if len(text) > max_chars_per_server:
            text = text[:max_chars_per_server] + "\n\n[TRUNCATED BY RUNTIME]\n"
        label = server.name if server.plugin_name is None else f"{server.plugin_name}:{server.name}"
        blocks.append(f"### {label}\n\n{text}")
    if not blocks:
        return ""
    return (
        "## Runtime MCP Server Instructions\n\n"
        "The following instructions came from configured MCP servers. Use them as server-specific tool guidance while keeping user, skill, agent, and runtime safety instructions higher priority.\n\n"
        + "\n\n".join(blocks)
    )


def servers_from_agent_mcp_specs(
    specs: Any,
    *,
    project_root: Path,
    plugin_root: Path | None = None,
    additional_dirs: list[Path] | None = None,
) -> list[MCPServerConfig]:
    if not isinstance(specs, list):
        return []
    servers: list[MCPServerConfig] = []
    existing = {server.name: server for server in discover_mcp_servers(project_root, additional_dirs=additional_dirs)}
    for item in specs:
        if isinstance(item, str):
            server = existing.get(item)
            if server is not None:
                servers.append(server)
            continue
        if isinstance(item, dict):
            servers.extend(_servers_from_mapping(item, project_root, plugin_root=plugin_root, plugin_name=None))
    return servers


def _servers_from_config(path: Path, project_root: Path, *, plugin_root: Path | None, plugin_name: str | None) -> list[MCPServerConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("mcpServers")
    if mapping is None and all(isinstance(value, dict) for value in data.values()):
        mapping = data
    if mapping is None:
        mapping = {}
    if not isinstance(mapping, dict):
        return []
    return _servers_from_mapping(mapping, project_root, plugin_root=plugin_root, plugin_name=plugin_name)


def _servers_from_mapping(
    mapping: dict[str, Any],
    project_root: Path,
    *,
    plugin_root: Path | None,
    plugin_name: str | None,
) -> list[MCPServerConfig]:
    servers: list[MCPServerConfig] = []
    for name, config in mapping.items():
        if not isinstance(config, dict):
            continue
        aliases = _server_aliases(str(name), plugin_name=plugin_name)
        servers.append(MCPServerConfig(name=str(name), aliases=aliases, config=config, plugin_root=plugin_root, plugin_name=plugin_name))
    return servers


def _server_aliases(name: str, *, plugin_name: str | None) -> tuple[str, ...]:
    aliases = {name, name.replace("-", "_")}
    if plugin_name:
        plugin_alias = plugin_name.replace("-", "_")
        server_alias = name.replace("-", "_")
        aliases.add(f"plugin_{plugin_alias}_{server_alias}")
    return tuple(sorted(aliases))


def _resolve_tool(
    *,
    project_root: Path,
    tool: str,
    extra_servers: list[MCPServerConfig] | None = None,
    additional_dirs: list[Path] | None = None,
) -> tuple[MCPServerConfig, str]:
    clean = tool.strip()
    if clean.startswith("mcp__"):
        clean = clean.removeprefix("mcp__")
    if "__" not in clean:
        raise MCPBridgeError(f"Invalid MCP tool name: {tool}")
    server_alias, tool_name = clean.split("__", 1)
    servers = discover_mcp_servers(project_root, extra_servers=extra_servers, additional_dirs=additional_dirs)
    for server in servers:
        if server_alias in server.aliases:
            return server, tool_name
    available = sorted(alias for server in servers for alias in server.aliases)
    raise MCPBridgeError(f"No MCP server configured for `{server_alias}`. Available server aliases: {available}")


def _is_remote_server(config: dict[str, Any]) -> bool:
    return any(key in config for key in ["url", "uri", "sseUrl", "httpUrl", "websocketUrl"])


def _server_transport(config: dict[str, Any]) -> str:
    declared = str(config.get("type") or config.get("transport") or "").strip().lower()
    if declared in {"stdio", "http", "sse", "websocket"}:
        return declared
    if declared in {"ws", "wss"}:
        return "websocket"
    if "command" in config:
        return "stdio"
    if "websocketUrl" in config:
        return "websocket"
    if "sseUrl" in config:
        return "sse"
    if "httpUrl" in config:
        return "http"
    raw_url = str(config.get("url") or config.get("uri") or "")
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme in {"ws", "wss"}:
        return "websocket"
    if parsed.path.rstrip("/").endswith("/sse"):
        return "sse"
    if raw_url:
        return "http"
    return declared or "stdio"


def _remote_url(
    config: dict[str, Any],
    expected: str,
    *,
    project_root: Path,
    plugin_root: Path | None,
) -> str:
    key_order = {
        "http": ["httpUrl", "url", "uri"],
        "sse": ["sseUrl", "url", "uri"],
        "websocket": ["websocketUrl", "wsUrl", "url", "uri"],
    }.get(expected, ["url", "uri"])
    for key in key_order:
        value = config.get(key)
        if value:
            url = _expand_env(str(value), project_root=project_root, plugin_root=plugin_root)
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in {"http", "https", "ws", "wss"}:
                raise MCPBridgeError(f"Remote MCP server URL must use http/https/ws/wss: {_sanitize_url(url)}")
            return url
    raise MCPBridgeError(f"Remote MCP server has no URL for transport `{expected}`.")


def _remote_headers(server: MCPServerConfig, *, project_root: Path) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = server.config.get("headers", {})
    if raw_headers is not None and not isinstance(raw_headers, dict):
        raise MCPBridgeError(f"MCP server `{server.name}` headers must be a JSON object.")
    for key, value in (raw_headers or {}).items():
        headers[str(key)] = _expand_env(str(value), project_root=project_root, plugin_root=server.plugin_root)

    stored = stored_oauth_headers(project_root=project_root, server_name=server.name, config=server.config)
    for key, value in stored.items():
        headers.setdefault(key, value)

    helper = server.config.get("headersHelper")
    if helper:
        helper_headers = _headers_from_helper(server, project_root=project_root)
        headers.update(helper_headers)

    auth_command = (
        server.config.get("authCommand")
        or server.config.get("oauthRefreshCommand")
        or server.config.get("tokenCommand")
    )
    if auth_command and "Authorization" not in headers:
        headers.update(_headers_from_auth_command(server, project_root=project_root, command=str(auth_command)))

    bearer = server.config.get("accessToken") or server.config.get("token")
    if bearer and "Authorization" not in headers:
        token = _expand_env(str(bearer), project_root=project_root, plugin_root=server.plugin_root)
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return headers


def _headers_from_auth_command(server: MCPServerConfig, *, project_root: Path, command: str) -> dict[str, str]:
    command = _expand_env(command, project_root=project_root, plugin_root=server.plugin_root)
    url = _remote_url(
        server.config,
        _server_transport(server.config),
        project_root=project_root,
        plugin_root=server.plugin_root,
    )
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    env["CLAUDE_CODE_MCP_SERVER_NAME"] = server.name
    env["CLAUDE_CODE_MCP_SERVER_URL"] = url
    if server.plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(server.plugin_root)
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        env=env,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        stderr = _redact_secret_text(completed.stderr or completed.stdout or "")
        raise MCPBridgeError(f"MCP server `{server.name}` auth command failed with exit {completed.returncode}: {stderr[-1000:]}")
    output = completed.stdout.strip()
    try:
        record = token_record_from_auth_output(server_name=server.name, config=server.config, output=output)
        SecureTokenStore(project_root).write(record)
    except Exception:
        pass
    try:
        data = json.loads(output)
    except ValueError:
        token = output
        return {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"}
    if isinstance(data, str):
        return {"Authorization": data if data.lower().startswith("bearer ") else f"Bearer {data}"}
    if not isinstance(data, dict):
        raise MCPBridgeError(f"MCP server `{server.name}` auth command must return a token string or JSON object.")
    if "headers" in data and isinstance(data["headers"], dict):
        return {str(key): str(value) for key, value in data["headers"].items()}
    if "accessToken" in data:
        token = str(data["accessToken"])
        return {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"}
    if "token" in data:
        token = str(data["token"])
        return {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}


def _headers_from_helper(server: MCPServerConfig, *, project_root: Path) -> dict[str, str]:
    command = _expand_env(str(server.config["headersHelper"]), project_root=project_root, plugin_root=server.plugin_root)
    url = _remote_url(
        server.config,
        _server_transport(server.config),
        project_root=project_root,
        plugin_root=server.plugin_root,
    )
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    env["CLAUDE_CODE_MCP_SERVER_NAME"] = server.name
    env["CLAUDE_CODE_MCP_SERVER_URL"] = url
    if server.plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(server.plugin_root)
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=10,
        env=env,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        stderr = _redact_secret_text(completed.stderr or completed.stdout or "")
        raise MCPBridgeError(f"MCP server `{server.name}` headersHelper failed with exit {completed.returncode}: {stderr[-1000:]}")
    try:
        data = json.loads(completed.stdout.strip())
    except ValueError as exc:
        raise MCPBridgeError(f"MCP server `{server.name}` headersHelper did not return valid JSON.") from exc
    if not isinstance(data, dict):
        raise MCPBridgeError(f"MCP server `{server.name}` headersHelper must return a JSON object.")
    headers: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            raise MCPBridgeError(f"MCP server `{server.name}` headersHelper returned non-string value for `{key}`.")
        headers[str(key)] = value
    return headers


def _post_json_rpc(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: int,
    session_id: str | None,
    expect_response: bool = True,
) -> tuple[dict[str, Any], Any]:
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2024-11-05",
        **headers,
    }
    if session_id:
        request_headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(16 * 1024 * 1024)
            response_headers = response.headers
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read(8192).decode("utf-8", errors="replace")
        _raise_remote_http_error(url, exc.code, detail)
    except urllib.error.URLError as exc:
        raise MCPBridgeError(f"Remote MCP request failed for {_sanitize_url(url)}: {exc.reason}") from exc

    if not body and not expect_response:
        return {}, response_headers
    if not body and status in {202, 204}:
        return {}, response_headers
    if not body:
        raise MCPBridgeError(f"Remote MCP server {_sanitize_url(url)} returned an empty response.")

    text = body.decode("utf-8", errors="replace")
    data = _decode_remote_response(text, request_id=payload.get("id"))
    return data, response_headers


def _decode_remote_response(text: str, *, request_id: Any) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("event:") or stripped.startswith("data:"):
        for event, data in _parse_sse_text(stripped):
            parsed = _loads_json_or_none(data)
            if parsed is None:
                continue
            if request_id is None or parsed.get("id") == request_id:
                return _jsonrpc_result(parsed, request_id=request_id)
        raise MCPBridgeError(f"Remote MCP SSE response did not contain JSON-RPC id {request_id}.")
    try:
        parsed = json.loads(stripped)
    except ValueError as exc:
        raise MCPBridgeError(f"Remote MCP response was not JSON: {stripped[:500]}") from exc
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and (request_id is None or item.get("id") == request_id):
                return _jsonrpc_result(item, request_id=request_id)
        raise MCPBridgeError(f"Remote MCP JSON batch did not contain JSON-RPC id {request_id}.")
    if not isinstance(parsed, dict):
        return {"value": parsed}
    return _jsonrpc_result(parsed, request_id=request_id)


def _jsonrpc_request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _jsonrpc_notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def _jsonrpc_result(message: dict[str, Any], *, request_id: Any) -> dict[str, Any]:
    if "error" in message:
        raise MCPBridgeError(f"MCP error for request {request_id}: {message['error']}")
    result = message.get("result", {})
    return result if isinstance(result, dict) else {"value": result}


class _SSEClient:
    def __init__(self, url: str, *, headers: dict[str, str], timeout: int) -> None:
        self.url = url
        self.timeout = timeout
        self._events: queue.Queue[tuple[str, str] | Exception | None] = queue.Queue()
        request_headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", **headers}
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            self._response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode("utf-8", errors="replace")
            _raise_remote_http_error(url, exc.code, detail)
        except urllib.error.URLError as exc:
            raise MCPBridgeError(f"Remote SSE MCP connection failed for {_sanitize_url(url)}: {exc.reason}") from exc
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        event = "message"
        data_lines: list[str] = []
        try:
            for raw in self._response:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        self._events.put((event, "\n".join(data_lines)))
                    event = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip() or "message"
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
        except Exception as exc:
            self._events.put(exc)
        finally:
            if data_lines:
                self._events.put((event, "\n".join(data_lines)))
            self._events.put(None)

    def wait_endpoint(self) -> str:
        deadline = time.monotonic() + self.timeout
        while True:
            event = self._next_event(deadline)
            if event[0] == "endpoint":
                return urllib.parse.urljoin(self.url, event[1])

    def wait_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            event_name, data = self._next_event(deadline)
            parsed = _loads_json_or_none(data)
            if parsed is None:
                continue
            if parsed.get("id") == request_id:
                return _jsonrpc_result(parsed, request_id=request_id)
            if event_name == "error":
                raise MCPBridgeError(f"SSE MCP server returned error event: {data[:1000]}")

    def _next_event(self, deadline: float) -> tuple[str, str]:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            raise MCPBridgeError(f"Timed out waiting for SSE MCP event from {_sanitize_url(self.url)}")
        try:
            event = self._events.get(timeout=remaining)
        except queue.Empty as exc:
            raise MCPBridgeError(f"Timed out waiting for SSE MCP event from {_sanitize_url(self.url)}") from exc
        if event is None:
            raise MCPBridgeError(f"SSE MCP stream closed before expected response from {_sanitize_url(self.url)}")
        if isinstance(event, Exception):
            raise MCPBridgeError(f"SSE MCP stream failed: {event}") from event
        return event

    def close(self) -> None:
        try:
            self._response.close()
        except Exception:
            pass


def _post_sse_message(endpoint: str, payload: dict[str, Any], *, headers: dict[str, str], timeout: int) -> None:
    request_headers = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(8192).decode("utf-8", errors="replace")
        _raise_remote_http_error(endpoint, exc.code, detail)
    except urllib.error.URLError as exc:
        raise MCPBridgeError(f"Remote SSE MCP POST failed for {_sanitize_url(endpoint)}: {exc.reason}") from exc


def _websocket_wait_response(ws: Any, request_id: int, *, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPBridgeError(f"Timed out waiting for WebSocket MCP response {request_id}")
        try:
            ws.settimeout(remaining)
        except Exception:
            pass
        message = ws.recv()
        parsed = _loads_json_or_none(str(message))
        if parsed is None:
            continue
        if parsed.get("id") == request_id:
            return _jsonrpc_result(parsed, request_id=request_id)


def _websocket_auth_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status_code in {401, 403}:
        return True
    text = str(exc)
    lowered = text.lower()
    return bool(re.search(r"\b(401|403)\b", text)) or "unauthorized" in lowered or "forbidden" in lowered


def _parse_sse_text(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    event = "message"
    data_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                events.append((event, "\n".join(data_lines)))
            event = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        events.append((event, "\n".join(data_lines)))
    return events


def _loads_json_or_none(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _raise_remote_http_error(url: str, status: int, detail: str) -> None:
    clean_detail = _redact_secret_text(detail.strip())
    if status in {401, 403}:
        raise MCPRemoteAuthError(
            f"Remote MCP server {_sanitize_url(url)} requires authentication or authorization (HTTP {status}). "
            "Configure `headers`, `headersHelper`, `authCommand`, `oauthRefreshCommand`, `tokenCommand`, or a stored OAuth/access token before calling this tool."
        )
    raise MCPBridgeError(f"Remote MCP server {_sanitize_url(url)} returned HTTP {status}: {clean_detail[:1000]}")


def _sanitize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = urllib.parse.urlencode(
        [
            (key, "[REDACTED]" if _looks_secret_key(key) else value)
            for key, value in query
        ]
    )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, ""))


def _looks_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ["token", "key", "secret", "password", "auth", "credential"])


def _redact_secret_text(text: str) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+", r"\1[REDACTED]", text)
    redacted = re.sub(r"(?i)(api[-_ ]?key['\"\s:=]+)[A-Za-z0-9._\-+/=]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(token['\"\s:=]+)[A-Za-z0-9._\-+/=]+", r"\1[REDACTED]", redacted)
    return redacted


def _send(process: subprocess.Popen[str], request_id: int, method: str, params: dict[str, Any]) -> None:
    if process.stdin is None:
        raise MCPBridgeError("MCP server stdin is unavailable")
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _send_notification(process: subprocess.Popen[str], method: str, params: dict[str, Any]) -> None:
    if process.stdin is None:
        raise MCPBridgeError("MCP server stdin is unavailable")
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _read_response(process: subprocess.Popen[str], request_id: int, *, timeout: int) -> dict[str, Any]:
    if process.stdout is None:
        raise MCPBridgeError("MCP server stdout is unavailable")
    output: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            output.put(process.stdout.readline())
        except Exception:
            output.put(None)

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            line = output.get(timeout=remaining)
        except queue.Empty:
            break
        if line is None or line == "":
            stderr = _collect_stderr(process)
            raise MCPBridgeError(f"MCP server exited before response {request_id}. stderr={stderr[-2000:]}")
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("id") == request_id:
            if "error" in data:
                raise MCPBridgeError(f"MCP error for request {request_id}: {data['error']}")
            result = data.get("result", {})
            return result if isinstance(result, dict) else {"value": result}
    raise MCPBridgeError(f"Timed out waiting for MCP response {request_id}")


def _collect_stderr(process: subprocess.Popen[str]) -> str:
    try:
        if process.stderr is None:
            return ""
        return process.stderr.read() or ""
    except Exception:
        return ""


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _resolve_plugin_path(plugin_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else plugin_root / path


def _expand_env(value: str, *, project_root: Path, plugin_root: Path | None) -> str:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    rendered = value.replace("${CLAUDE_PROJECT_DIR}", str(project_root)).replace("$CLAUDE_PROJECT_DIR", str(project_root))
    if plugin_root is not None:
        rendered = rendered.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    rendered = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: env.get(match.group(1), ""), rendered)
    rendered = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", lambda match: env.get(match.group(1), ""), rendered)
    return rendered
