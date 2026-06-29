from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Capability:
    name: str
    provider: str = ""
    namespace: str = ""
    kind: str = "generic"
    status: str = "configured"
    endpoint: str = ""
    description: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_capabilities(project_root: Path, *, additional_dirs: Iterable[Path] = ()) -> list[Capability]:
    """Discover generic external/runtime capabilities without domain hardcoding."""

    roots = _unique_paths([project_root, *additional_dirs])
    records: list[Capability] = []
    has_additional = len(roots) > 1
    for index, root in enumerate(roots):
        records.extend(_from_capability_files(root))
        records.extend(_from_plugin_manifests(root, recursive=not (has_additional and index == 0)))
    records.extend(_from_env())
    return _dedupe(records)


def capability_context(project_root: Path, *, additional_dirs: Iterable[Path] = ()) -> str:
    capabilities = discover_capabilities(project_root, additional_dirs=additional_dirs)
    if not capabilities:
        return ""
    lines = [
        "## Runtime Capability Registry",
        "",
        "These capabilities are discovered from loaded skill/plugin manifests, runtime capability files, and runtime environment variables. "
        "They describe external services or local tools that skills may use through normal runtime tools such as MCP, bash, web_fetch, or skill-specific scripts. "
        "The runtime core does not assume domain-specific behavior from capability names.",
        "",
    ]
    for item in capabilities:
        detail = []
        if item.namespace:
            detail.append(f"namespace={item.namespace}")
        if item.provider:
            detail.append(f"provider={item.provider}")
        if item.kind:
            detail.append(f"kind={item.kind}")
        if item.status:
            detail.append(f"status={item.status}")
        if item.endpoint:
            detail.append(f"endpoint={item.endpoint}")
        suffix = f" ({', '.join(detail)})" if detail else ""
        description = f": {item.description}" if item.description else ""
        lines.append(f"- `{item.name}`{suffix}{description}")
    return "\n".join(lines)


def _from_capability_files(root: Path) -> list[Capability]:
    candidates = [
        root / ".codex-skill-runtime" / "capabilities.json",
        root / ".skill-runtime" / "capabilities.json",
        root / "capabilities.json",
    ]
    records: list[Capability] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        records.extend(_parse_capability_payload(_read_json(path), source=str(path), root=root))
    return records


def _from_plugin_manifests(root: Path, *, recursive: bool = True) -> list[Capability]:
    records: list[Capability] = []
    for manifest_path in _plugin_manifest_paths(root, recursive=recursive):
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        plugin_root = manifest_path.parent.parent
        plugin_name = str(manifest.get("name") or plugin_root.name)
        payload = manifest.get("capabilities")
        for record in _parse_capability_payload(payload, source=str(manifest_path), root=plugin_root):
            metadata = {"plugin": plugin_name, **record.metadata}
            records.append(
                Capability(
                    name=record.name,
                    provider=record.provider or plugin_name,
                    namespace=record.namespace or plugin_name,
                    kind=record.kind,
                    status=record.status,
                    endpoint=record.endpoint,
                    description=record.description,
                    source=record.source,
                    metadata=metadata,
                )
            )
    return records


def _from_env() -> list[Capability]:
    records: list[Capability] = []
    raw_json = os.environ.get("SKILL_RUNTIME_CAPABILITIES_JSON") or os.environ.get("CODEX_SKILL_RUNTIME_CAPABILITIES_JSON")
    if raw_json:
        try:
            records.extend(_parse_capability_payload(json.loads(raw_json), source="env:SKILL_RUNTIME_CAPABILITIES_JSON", root=None))
        except ValueError:
            records.append(
                Capability(
                    name="invalid-env-capabilities",
                    kind="error",
                    status="error",
                    source="env:SKILL_RUNTIME_CAPABILITIES_JSON",
                    description="SKILL_RUNTIME_CAPABILITIES_JSON is not valid JSON.",
                )
            )

    raw_services = os.environ.get("SKILL_RUNTIME_SERVICES_JSON") or os.environ.get("CODEX_SKILL_RUNTIME_SERVICES_JSON")
    if raw_services:
        try:
            records.extend(_capabilities_from_services(json.loads(raw_services)))
        except ValueError:
            records.append(
                Capability(
                    name="invalid-env-services",
                    kind="error",
                    status="error",
                    source="env:SKILL_RUNTIME_SERVICES_JSON",
                    description="SKILL_RUNTIME_SERVICES_JSON is not valid JSON.",
                )
            )

    grouped: dict[str, dict[str, str]] = {}
    prefixes = ("SKILL_RUNTIME_CAPABILITY_", "CODEX_SKILL_RUNTIME_CAPABILITY_")
    for key, value in os.environ.items():
        prefix = next((candidate for candidate in prefixes if key.startswith(candidate)), None)
        if prefix is None:
            continue
        rest = key.removeprefix(prefix)
        if "_" not in rest:
            continue
        name, field = rest.rsplit("_", 1)
        clean_name = _clean_name(name)
        if not clean_name:
            continue
        grouped.setdefault(clean_name, {})[field.lower()] = value
    for name, values in grouped.items():
        records.append(
            Capability(
                name=name,
                provider=values.get("provider", ""),
                namespace=values.get("namespace", ""),
                kind=values.get("kind") or values.get("type") or "external-service",
                status=values.get("status", "configured"),
                endpoint=values.get("endpoint") or values.get("url") or values.get("baseurl") or "",
                description=values.get("description", ""),
                source="env",
                metadata={key: value for key, value in values.items() if key not in {"provider", "namespace", "kind", "type", "status", "endpoint", "url", "baseurl", "description"}},
            )
        )
    return records


def _capabilities_from_services(payload: Any) -> list[Capability]:
    services = payload.get("services", []) if isinstance(payload, dict) else payload
    if not isinstance(services, list):
        return []
    records: list[Capability] = []
    for item in services:
        if not isinstance(item, dict):
            continue
        name = _clean_name(str(item.get("id") or item.get("name") or item.get("label") or "service"))
        endpoint = str(item.get("endpoint") or item.get("url") or "")
        records.append(
            Capability(
                name=name,
                provider=str(item.get("provider") or ""),
                namespace=str(item.get("namespace") or ""),
                kind=str(item.get("kind") or item.get("type") or "external-service"),
                status=str(item.get("status") or "configured"),
                endpoint=endpoint,
                description=str(item.get("description") or ""),
                source="env:SKILL_RUNTIME_SERVICES_JSON",
                metadata={key: value for key, value in item.items() if key not in {"id", "name", "label", "provider", "namespace", "kind", "type", "status", "endpoint", "url", "description", "start_cmd", "start", "command"}},
            )
        )
    return records


def _parse_capability_payload(payload: Any, *, source: str, root: Path | None) -> list[Capability]:
    if payload is None or payload == "":
        return []
    if isinstance(payload, dict):
        if isinstance(payload.get("capabilities"), list):
            return _parse_capability_payload(payload.get("capabilities"), source=source, root=root)
        if payload.get("name"):
            return [_capability_from_dict(payload, source=source, root=root)]
        records = []
        for name, value in payload.items():
            if isinstance(value, dict):
                records.append(_capability_from_dict({"name": name, **value}, source=source, root=root))
            else:
                records.append(Capability(name=_clean_name(str(name)), description=str(value), source=source))
        return records
    if isinstance(payload, list):
        records = []
        for item in payload:
            if isinstance(item, dict):
                records.append(_capability_from_dict(item, source=source, root=root))
            else:
                text = str(item).strip()
                if text:
                    records.append(Capability(name=_clean_name(text), description=text, source=source))
        return records
    text = str(payload).strip()
    return [Capability(name=_clean_name(text), description=text, source=source)] if text else []


def _capability_from_dict(data: dict[str, Any], *, source: str, root: Path | None) -> Capability:
    endpoint = str(data.get("endpoint") or data.get("url") or data.get("base_url") or data.get("baseUrl") or "")
    if endpoint and root is not None and endpoint.startswith("./"):
        endpoint = str((root / endpoint).resolve())
    reserved = {"name", "provider", "namespace", "kind", "type", "status", "endpoint", "url", "base_url", "baseUrl", "description", "whenToUse", "source"}
    metadata = {str(key): value for key, value in data.items() if key not in reserved}
    return Capability(
        name=_clean_name(str(data.get("name") or data.get("id") or "capability")),
        provider=str(data.get("provider") or ""),
        namespace=str(data.get("namespace") or ""),
        kind=str(data.get("kind") or data.get("type") or "generic"),
        status=str(data.get("status") or "configured"),
        endpoint=endpoint,
        description=str(data.get("description") or data.get("whenToUse") or ""),
        source=str(data.get("source") or source),
        metadata=metadata,
    )


def _plugin_manifest_paths(root: Path, *, recursive: bool = True) -> list[Path]:
    if not root.exists():
        return []
    candidates = []
    direct = root / ".claude-plugin" / "plugin.json"
    if direct.exists():
        candidates.append(direct)
    if not recursive:
        return _unique_paths(candidates)
    try:
        for path in root.rglob("plugin.json"):
            if path.parent.name == ".claude-plugin":
                candidates.append(path)
    except OSError:
        return candidates
    return _unique_paths(candidates)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _dedupe(records: Iterable[Capability]) -> list[Capability]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Capability] = []
    for item in records:
        key = (item.namespace, item.name, item.endpoint, item.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(key=lambda item: (item.namespace, item.name, item.source))
    return result


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _clean_name(value: str) -> str:
    text = value.strip().lower().replace("_", "-")
    return "".join(ch if ch.isalnum() or ch in "-:." else "-" for ch in text).strip("-") or "capability"
