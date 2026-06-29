from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .state_paths import runtime_state_path


def plugin_state_path(project_root: Path) -> Path:
    return runtime_state_path(project_root, "plugins", "plugins.json")


def plugin_state(project_root: Path) -> dict[str, Any]:
    path = plugin_state_path(project_root)
    if not path.exists():
        return {"disabled": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {"disabled": []}
    return data if isinstance(data, dict) else {"disabled": []}


def set_plugin_enabled(project_root: Path, *, name: str, root: str | Path | None = None, enabled: bool) -> dict[str, Any]:
    state = plugin_state(project_root)
    disabled = _disabled_entries(state)
    clean_name = _clean_name(name)
    root_text = str(Path(root).expanduser().resolve()) if root else ""
    disabled = [
        item
        for item in disabled
        if not _same_plugin(item, name=clean_name, root=root_text)
    ]
    if not enabled:
        disabled.append(
            {
                "name": clean_name,
                "root": root_text,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    state["disabled"] = disabled
    path = plugin_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def is_plugin_enabled(project_root: Path, *, name: str, root: Path) -> bool:
    clean_name = _clean_name(name)
    root_text = str(root.expanduser().resolve())
    for item in _disabled_entries(plugin_state(project_root)):
        if _same_plugin(item, name=clean_name, root=root_text):
            return False
    return True


def plugin_status_rows(project_root: Path, plugin_layouts: Iterable[Any], *, include_disabled: bool = True) -> list[dict[str, Any]]:
    rows = []
    for plugin in plugin_layouts:
        name = str(getattr(plugin, "name", ""))
        root = Path(getattr(plugin, "root"))
        enabled = is_plugin_enabled(project_root, name=name, root=root)
        if not enabled and not include_disabled:
            continue
        rows.append(
            {
                "name": name,
                "root": str(root),
                "manifest": str(getattr(plugin, "manifest_path", "")),
                "enabled": enabled,
            }
        )
    rows.sort(key=lambda item: (not bool(item["enabled"]), str(item["name"])))
    return rows


def _disabled_entries(state: dict[str, Any]) -> list[dict[str, str]]:
    raw = state.get("disabled", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _same_plugin(item: dict[str, Any], *, name: str, root: str) -> bool:
    item_name = _clean_name(str(item.get("name") or ""))
    item_root = str(item.get("root") or "")
    if root and item_root:
        return item_root == root
    return item_name == name


def _clean_name(value: str) -> str:
    return value.strip().lower()
