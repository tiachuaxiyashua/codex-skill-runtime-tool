from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


@dataclass(frozen=True)
class RuntimePaths:
    tool_root: Path
    workspace_root: Path


MODEL_CONFIG_ENV_KEYS = {
    "SKILL_RUNTIME_MODEL",
    "CODEX_MODEL",
    "CODEX_PROVIDER",
    "CODEX_BASE_URL",
    "CODEX_WIRE_API",
    "CODEX_REQUIRES_OPENAI_AUTH",
    "CODEX_CONFIG",
    "SKILL_RUNTIME_CODEX_OSS",
    "SKILL_RUNTIME_CODEX_LOCAL_PROVIDER",
}


def load_env(path: Path, *, paths: RuntimePaths) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = expand_env_value(value.strip(), values, paths=paths)
    return values


def model_config_from_env(values: dict[str, str], runtime_env: Path, *, paths: RuntimePaths) -> dict[str, object]:
    codex_config = _split_env_list(values.get("CODEX_CONFIG") or values.get("SKILL_RUNTIME_CODEX_CONFIG") or "")
    config_map = _codex_config_map(codex_config)
    api_key_file = values.get("CODEX_API_KEY_FILE") or values.get("SKILL_RUNTIME_CODEX_API_KEY_FILE") or ""
    api_key_path = portable_path(api_key_file, paths=paths, fallback=runtime_env.parent) if api_key_file else None
    codex_oss = _bool_text(values.get("SKILL_RUNTIME_CODEX_OSS") or values.get("CODEX_OSS"), default=False)
    base_url = (
        values.get("CODEX_BASE_URL")
        or values.get("SKILL_RUNTIME_CODEX_BASE_URL")
        or str(config_map.get(f"model_providers.{values.get('CODEX_PROVIDER', '')}.base_url") or "")
    )
    provider = values.get("CODEX_PROVIDER") or values.get("SKILL_RUNTIME_CODEX_PROVIDER") or str(config_map.get("model_provider") or "")
    wire_api = values.get("CODEX_WIRE_API") or values.get("SKILL_RUNTIME_CODEX_WIRE_API") or "responses"
    requires_auth = _bool_text(values.get("CODEX_REQUIRES_OPENAI_AUTH") or values.get("SKILL_RUNTIME_CODEX_REQUIRES_OPENAI_AUTH"), default=True)
    active_profile = "ollama" if codex_oss and (values.get("SKILL_RUNTIME_CODEX_LOCAL_PROVIDER") or values.get("CODEX_LOCAL_PROVIDER")) == "ollama" else "custom"
    if not codex_oss and provider == "OpenAI" and base_url:
        active_profile = "current-proxy"
    return {
        "runtime_env": str(runtime_env),
        "active_profile": active_profile,
        "config": {
            "model": values.get("SKILL_RUNTIME_MODEL") or values.get("CODEX_MODEL") or "",
            "provider": provider,
            "base_url": base_url,
            "wire_api": wire_api,
            "requires_openai_auth": requires_auth,
            "api_key_file": str(api_key_path) if api_key_path else "",
            "api_key_file_exists": bool(api_key_path and api_key_path.exists() and api_key_path.is_file()),
            "codex_oss": codex_oss,
            "local_provider": values.get("SKILL_RUNTIME_CODEX_LOCAL_PROVIDER") or values.get("CODEX_LOCAL_PROVIDER") or "",
            "review_model": str(config_map.get("review_model") or ""),
            "reasoning_effort": str(config_map.get("model_reasoning_effort") or ""),
            "disable_response_storage": _bool_text(config_map.get("disable_response_storage"), default=True),
            "network_access": str(config_map.get("network_access") or ""),
            "context_window": _int_text(config_map.get("model_context_window"), default=0),
            "auto_compact_token_limit": _int_text(config_map.get("model_auto_compact_token_limit"), default=0),
            "codex_config": codex_config,
        },
        "presets": [
            {
                "id": "current-proxy",
                "label": "当前代理 / OpenAI-compatible",
                "description": "使用当前 skill-runtime.env 中的代理地址和 API key 文件。",
                "values": {
                    "model": values.get("SKILL_RUNTIME_MODEL") or values.get("CODEX_MODEL") or "gpt-5.4",
                    "provider": provider or "OpenAI",
                    "base_url": base_url,
                    "wire_api": wire_api or "responses",
                    "requires_openai_auth": requires_auth,
                    "codex_oss": False,
                    "local_provider": "",
                    "review_model": str(config_map.get("review_model") or values.get("SKILL_RUNTIME_MODEL") or "gpt-5.4"),
                    "reasoning_effort": str(config_map.get("model_reasoning_effort") or "low"),
                    "context_window": _int_text(config_map.get("model_context_window"), default=32768),
                    "auto_compact_token_limit": _int_text(config_map.get("model_auto_compact_token_limit"), default=28000),
                    "network_access": str(config_map.get("network_access") or "enabled"),
                    "disable_response_storage": _bool_text(config_map.get("disable_response_storage"), default=True),
                },
            },
            {
                "id": "ollama-gpt",
                "label": "Ollama GPT / 本地模型",
                "description": "通过 Codex CLI 的 --oss --local-provider ollama 运行本机 Ollama 模型。需要本机已启动 Ollama 并已拉取模型。",
                "values": {
                    "model": "gpt-oss:20b",
                    "provider": "",
                    "base_url": "",
                    "wire_api": "responses",
                    "requires_openai_auth": False,
                    "codex_oss": True,
                    "local_provider": "ollama",
                    "review_model": "gpt-oss:20b",
                    "reasoning_effort": "low",
                    "context_window": 32768,
                    "auto_compact_token_limit": 28000,
                    "network_access": "enabled",
                    "disable_response_storage": True,
                },
            },
        ],
    }


def model_config_updates_from_payload(payload: dict[str, object], current: dict[str, str]) -> dict[str, str]:
    model = _clean_text(payload.get("model")) or current.get("SKILL_RUNTIME_MODEL", "")
    provider = _clean_text(payload.get("provider"))
    base_url = _clean_text(payload.get("base_url"))
    wire_api = _clean_text(payload.get("wire_api")) or "responses"
    requires_auth = _bool_text(payload.get("requires_openai_auth"), default=True)
    codex_oss = _bool_text(payload.get("codex_oss"), default=False)
    local_provider = _clean_text(payload.get("local_provider"))
    review_model = _clean_text(payload.get("review_model")) or model
    reasoning_effort = _clean_text(payload.get("reasoning_effort")) or "low"
    network_access = _clean_text(payload.get("network_access")) or "enabled"
    context_window = _int_text(payload.get("context_window"), default=32768)
    auto_compact = _int_text(payload.get("auto_compact_token_limit"), default=28000)
    disable_storage = _bool_text(payload.get("disable_response_storage"), default=True)
    if codex_oss:
        provider = ""
        base_url = ""
        requires_auth = False
        if not local_provider:
            local_provider = "ollama"
    config_values = [
        f"review_model={json.dumps(review_model)}",
        f"model_reasoning_effort={json.dumps(reasoning_effort)}",
        f"disable_response_storage={'true' if disable_storage else 'false'}",
        f"network_access={json.dumps(network_access)}",
        "windows_wsl_setup_acknowledged=true",
        f"model_context_window={max(0, context_window)}",
        f"model_auto_compact_token_limit={max(0, auto_compact)}",
    ]
    return {
        "SKILL_RUNTIME_MODEL": model,
        "CODEX_PROVIDER": provider,
        "CODEX_BASE_URL": base_url,
        "CODEX_WIRE_API": wire_api,
        "CODEX_REQUIRES_OPENAI_AUTH": "true" if requires_auth else "false",
        "SKILL_RUNTIME_CODEX_OSS": "true" if codex_oss else "false",
        "SKILL_RUNTIME_CODEX_LOCAL_PROVIDER": local_provider,
        "CODEX_CONFIG": json.dumps(config_values, ensure_ascii=False, separators=(",", ":")),
    }


def write_env_updates(path: Path, updates: dict[str, str], *, delete_when_empty: set[str] | None = None) -> None:
    delete_when_empty = delete_when_empty or set()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        prefix = "export " if stripped.startswith("export ") else ""
        assignment = stripped.removeprefix("export ").strip() if prefix else stripped
        if not assignment or assignment.startswith("#") or "=" not in assignment:
            output.append(raw)
            continue
        key = assignment.split("=", 1)[0].strip()
        if key not in updates:
            output.append(raw)
            continue
        if key not in MODEL_CONFIG_ENV_KEYS:
            output.append(raw)
            continue
        value = str(updates[key])
        seen.add(key)
        if key in delete_when_empty and not value:
            continue
        output.append(f"{prefix}{key}={_env_value(value)}")
    missing = [
        key
        for key in updates
        if key in MODEL_CONFIG_ENV_KEYS and key not in seen and not (key in delete_when_empty and not str(updates[key]))
    ]
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.append("# Model configuration managed by the Web UI.")
        for key in missing:
            value = str(updates[key])
            if key in delete_when_empty and not value:
                continue
            output.append(f"{key}={_env_value(value)}")
    output = _strip_empty_managed_env_blocks(output)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def apply_runtime_env_to_process(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key.startswith("SKILL_RUNTIME_ENV_") and len(key) > len("SKILL_RUNTIME_ENV_"):
            os.environ[key.removeprefix("SKILL_RUNTIME_ENV_")] = value
        elif key in {"SKILL_RUNTIME_CAPABILITIES_JSON", "CODEX_SKILL_RUNTIME_CAPABILITIES_JSON"}:
            os.environ[key] = value
        elif key.startswith("SKILL_RUNTIME_CAPABILITY_") or key.startswith("CODEX_SKILL_RUNTIME_CAPABILITY_"):
            os.environ[key] = value


def runtime_env_exports(values: dict[str, str], *, paths: RuntimePaths) -> dict[str, str]:
    exports = {
        "SKILL_RUNTIME_TOOL_ROOT": str(paths.tool_root),
        "SKILL_RUNTIME_WORKSPACE_ROOT": str(paths.workspace_root),
    }
    for key, value in values.items():
        exports[key] = value
        if key.startswith("SKILL_RUNTIME_ENV_") and len(key) > len("SKILL_RUNTIME_ENV_"):
            exports[key.removeprefix("SKILL_RUNTIME_ENV_")] = value
    return exports


def configured_services(values: dict[str, str], *, paths: RuntimePaths) -> list[dict[str, object]]:
    services: dict[str, dict[str, object]] = {}
    raw_json = values.get("SKILL_RUNTIME_SERVICES_JSON", "")
    if raw_json:
        try:
            parsed: Any = json.loads(raw_json)
        except ValueError:
            parsed = []
        if isinstance(parsed, dict):
            parsed = parsed.get("services", [])
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    service = _normalize_service(item, values, paths=paths)
                    if service:
                        services[str(service["id"])] = service

    prefixes = ("SKILL_RUNTIME_SERVICE_", "CODEX_SKILL_RUNTIME_SERVICE_")
    grouped: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        prefix = next((candidate for candidate in prefixes if key.startswith(candidate)), None)
        if prefix is None:
            continue
        rest = key.removeprefix(prefix)
        if "_" not in rest:
            continue
        name, field = rest.rsplit("_", 1)
        service_id = _slug(name.lower().replace("_", "-")) or name.lower()
        grouped.setdefault(service_id, {})[field.lower()] = value
    for service_id, item in grouped.items():
        service = _normalize_service({"id": service_id, **item}, values, paths=paths)
        if service:
            services[str(service["id"])] = service
    return sorted(services.values(), key=lambda item: str(item.get("label") or item.get("id")))


def service_by_id(values: dict[str, str], service_id: str, *, paths: RuntimePaths) -> dict[str, object] | None:
    wanted = _slug(service_id).lower()
    for service in configured_services(values, paths=paths):
        if str(service.get("id") or "") == wanted:
            return service
    return None


def expand_env_value(value: str, current: dict[str, str] | None = None, *, paths: RuntimePaths) -> str:
    values = {
        "SKILL_RUNTIME_TOOL_ROOT": str(paths.tool_root),
        "SKILL_RUNTIME_WORKSPACE_ROOT": str(paths.workspace_root),
        **os.environ,
        **(current or {}),
    }
    result = value
    for key, item in values.items():
        result = result.replace("${" + key + "}", str(item))
    return result


def portable_path(value: str, *, paths: RuntimePaths, fallback: Path | None = None) -> Path:
    text = str(value or "").strip()
    if not text:
        return (fallback or paths.workspace_root).expanduser().resolve()
    fallback_path = (fallback or paths.workspace_root).expanduser().resolve()
    if _looks_like_foreign_windows_path(text):
        if os.name == "nt":
            return Path(text).expanduser().resolve()
        return fallback_path
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = paths.workspace_root / path
        return path.resolve()
    except OSError:
        return fallback_path


def state_root_from_env(runtime_env: Path, *, paths: RuntimePaths) -> Path:
    values = load_env(runtime_env, paths=paths)
    configured = values.get("SKILL_RUNTIME_STATE_ROOT", "")
    if configured:
        return portable_path(configured, paths=paths, fallback=paths.tool_root / ".skill-runtime" / "state")
    return paths.tool_root / ".skill-runtime" / "state"


def runtime_env_paths(runtime_env: Path, *, paths: RuntimePaths) -> dict[str, object]:
    values = load_env(runtime_env, paths=paths)
    root = portable_path(values.get("SKILL_RUNTIME_ROOT") or str(paths.workspace_root), paths=paths, fallback=paths.workspace_root)
    target = portable_path(values.get("SKILL_RUNTIME_TARGET_WORKSPACE") or values.get("SKILL_RUNTIME_WORKSPACE") or str(root), paths=paths, fallback=root)
    raw_repos = values.get("SKILL_RUNTIME_SKILL_REPOS") or values.get("SKILL_RUNTIME_SKILL_REPOSITORIES") or ""
    if raw_repos:
        repos = [portable_path(item, paths=paths, fallback=root) for item in _split_list(raw_repos)]
    else:
        repos = [root]
    add_dirs = [portable_path(item, paths=paths, fallback=root) for item in _split_list(values.get("SKILL_RUNTIME_ADD_DIRS") or values.get("SKILL_RUNTIME_ADD_DIR") or "")]
    return {"root": root, "target_workspace": target, "skill_repos": _unique_paths([*repos, *add_dirs])}


def _normalize_service(data: dict[str, object], values: dict[str, str], *, paths: RuntimePaths) -> dict[str, object]:
    service_id = _slug(str(data.get("id") or data.get("name") or "")).lower()
    if not service_id:
        return {}
    endpoint = expand_env_value(str(data.get("endpoint") or data.get("url") or ""), values, paths=paths)
    health_url = expand_env_value(str(data.get("health_url") or data.get("health") or ""), values, paths=paths)
    start_cmd = expand_env_value(str(data.get("start_cmd") or data.get("start") or data.get("command") or ""), values, paths=paths)
    return {
        "id": service_id,
        "label": str(data.get("label") or data.get("title") or service_id),
        "kind": str(data.get("kind") or data.get("type") or "external-service"),
        "description": str(data.get("description") or ""),
        "endpoint": endpoint,
        "health_url": health_url or endpoint,
        "start_cmd": start_cmd,
        "source": str(data.get("source") or "env"),
    }


def _strip_empty_managed_env_blocks(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    marker = "# Model configuration managed by the Web UI."
    while index < len(lines):
        if lines[index].strip() != marker:
            result.append(lines[index])
            index += 1
            continue
        block: list[str] = [lines[index]]
        index += 1
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                block.append(lines[index])
                index += 1
                continue
            if stripped.startswith("#") or "=" in stripped:
                if stripped.startswith("#") and stripped != marker:
                    break
                if "=" in stripped:
                    key = stripped.removeprefix("export ").split("=", 1)[0].strip()
                    if key in MODEL_CONFIG_ENV_KEYS:
                        block.append(lines[index])
                        index += 1
                        continue
                break
            break
        has_assignment = any(
            "=" in item and item.strip().removeprefix("export ").split("=", 1)[0].strip() in MODEL_CONFIG_ENV_KEYS
            for item in block
        )
        if has_assignment:
            result.extend(block)
            continue
        while result and not result[-1].strip():
            result.pop()
    return result


def _env_value(value: str) -> str:
    text = str(value)
    if "\n" in text:
        return json.dumps(text, ensure_ascii=False)
    return text


def _split_env_list(value: str) -> list[str]:
    stripped = str(value or "").strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return []
    separator = "||" if "||" in stripped else ";"
    return [item.strip() for item in stripped.split(separator) if item.strip()]


def _codex_config_map(values: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in values:
        if "=" not in item:
            continue
        key, raw = item.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        result[key] = _parse_toml_like_value(raw)
    return result


def _parse_toml_like_value(value: str) -> object:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        try:
            return json.loads(text) if text[0] == '"' else text[1:-1]
        except ValueError:
            return text[1:-1]
    return text


def _clean_text(value: object) -> str:
    return str(value or "").lstrip("\ufeff").strip()


def _bool_text(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _int_text(value: object, *, default: int) -> int:
    try:
        return int(str(value if value is not None else "").strip())
    except ValueError:
        return default


def _looks_like_foreign_windows_path(value: str) -> bool:
    text = value.strip()
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return bool(PureWindowsPath(text).anchor)
    return False


def _slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value).strip("-._")
    text = re.sub(r"-{2,}", "-", text)
    return text[:80]


def _split_list(value: str) -> list[str]:
    if not value:
        return []
    raw = value.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    separator = ";" if ";" in raw else os.pathsep
    return [item.strip() for item in raw.split(separator) if item.strip()]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
