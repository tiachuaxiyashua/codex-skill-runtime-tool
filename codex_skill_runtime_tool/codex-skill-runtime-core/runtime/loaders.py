from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .compat import bundled_skill_documents, matches_paths, model_invocable, user_invocable
from .frontmatter import MarkdownDocument, read_markdown_document
from .plugins import is_plugin_enabled, plugin_status_rows

SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
DEFAULT_SKILL_CHAR_BUDGET = 8000
MAX_LISTING_DESC_CHARS = 250
MIN_LISTING_DESC_CHARS = 20


@dataclass(frozen=True)
class PluginLayout:
    root: Path
    name: str
    manifest_path: Path
    manifest: dict[str, object]


@dataclass(frozen=True)
class SkillListing:
    name: str
    description: str
    path: Path
    source: str
    context: str | None
    agent: str | None
    user_invocable: bool
    model_invocable: bool


@dataclass(frozen=True)
class ProjectLayout:
    root: Path

    @property
    def claude_dir(self) -> Path:
        return self.root / ".claude"

    @property
    def skills_dir(self) -> Path:
        return self.claude_dir / "skills"

    @property
    def agents_dir(self) -> Path:
        return self.claude_dir / "agents"

    @property
    def docs_dir(self) -> Path:
        return self.claude_dir / "docs"

    @property
    def settings_path(self) -> Path:
        return self.claude_dir / "settings.json"

    @property
    def root_skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def root_agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def root_hooks_path(self) -> Path:
        return self.root / "hooks" / "hooks.json"

    @property
    def commands_dir(self) -> Path:
        return self.claude_dir / "commands"

    @property
    def root_commands_dir(self) -> Path:
        return self.root / "commands"


class SkillRepositoryLoader:
    def __init__(
        self,
        root: Path,
        *,
        additional_dirs: Iterable[Path] | None = None,
        bare: bool | None = None,
    ) -> None:
        self.layout = ProjectLayout(root=root.resolve())
        self.additional_dirs = _unique_paths([*(additional_dirs or []), *_env_paths("CODEX_SKILL_RUNTIME_ADD_DIRS"), *_env_paths("CLAUDE_CODE_ADD_DIRS")])
        self.bare = _env_truthy("CODEX_SKILL_RUNTIME_BARE") if bare is None else bare

    def assert_valid(self) -> None:
        if not self._skill_paths() and not self._bundled_skills():
            raise FileNotFoundError(
                "Missing Claude skill paths. Expected one of: "
                ".claude/skills/<name>/SKILL.md, skills/<name>/SKILL.md, "
                "or <name>/SKILL.md under the project root."
            )

    def list_skills(self) -> list[str]:
        self.assert_valid()
        names: set[str] = set()
        for path in self._skill_paths():
            names.add(self._display_name(path))
        for document in self._bundled_skills():
            names.add(str(document.metadata.get("name") or document.path.parent.name))
        return sorted(names)

    def list_model_invocable_skills(self, *, touched_paths: Iterable[str] = ()) -> list[str]:
        return [listing.name for listing in self.skill_listings(touched_paths=touched_paths, model_only=True)]

    def skill_listings(
        self,
        *,
        touched_paths: Iterable[str] = (),
        model_only: bool = False,
    ) -> list[SkillListing]:
        listings: list[SkillListing] = []
        for document in self._skill_documents():
            visible_to_model = model_invocable(document) and matches_paths(document, touched_paths, base=self.layout.root)
            if model_only and not visible_to_model:
                continue
            plugin = self._plugin_for(document.path)
            source = f"plugin:{plugin.name}" if plugin is not None else self._workflow_source(document.path)
            listings.append(
                SkillListing(
                    name=self._display_name_for_document(document.path, document),
                    description=_skill_description(document),
                    path=document.path,
                    source=source,
                    context=str(document.metadata.get("context") or "").strip() or None,
                    agent=str(document.metadata.get("agent") or "").strip() or None,
                    user_invocable=user_invocable(document),
                    model_invocable=visible_to_model,
                )
            )
        listings.sort(key=lambda item: item.name)
        return listings

    def skill_registry_context(
        self,
        *,
        touched_paths: Iterable[str] = (),
        context_window_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        listings = self.skill_listings(touched_paths=touched_paths, model_only=True)
        if not listings:
            return ""
        body = format_skill_listings_within_budget(
            listings,
            context_window_tokens=context_window_tokens,
            max_chars=max_chars,
        )
        return (
            "## Runtime SkillTool Registry\n\n"
            "These are model-invocable Claude Code skills visible to this runtime. "
            "This is a discovery list only; full SKILL.md content is loaded only through the `skill` action.\n\n"
            "Invocation contract:\n"
            "- If the current user task or a loaded skill matches an entry, call the `skill` action before improvising.\n"
            "- Invoke by fully qualified name when shown, for example `docs:write`, `ops:verify`, or `team:review`.\n"
            "- Do not invoke skills whose full content is already present in the current turn.\n"
            "- A loaded skill may invoke another skill through the same `skill` action; this is nested skill invocation.\n\n"
            f"Available skills:\n\n{body}"
        )

    def list_agents(self) -> list[str]:
        names: set[str] = set()
        for path in self._agent_paths():
            document = read_markdown_document(path)
            names.add(str(document.metadata.get("name") or path.stem))
        return sorted(names)

    def load_skill(self, name: str) -> MarkdownDocument:
        self.assert_valid()
        clean = name[1:] if name.startswith("/") else name
        for document in self._skill_documents():
            path = document.path
            if clean in self._aliases_for_workflow(path, document):
                return document
        raise FileNotFoundError(f"Skill not found: {clean} under {self.layout.root}")

    def load_agent(self, name: str) -> MarkdownDocument:
        for path in self._agent_paths():
            document = read_markdown_document(path)
            if name in self._aliases_for_agent(path, document):
                return document
        raise FileNotFoundError(f"Agent not found: {name} under {self.layout.root}")

    def load_skill_by_reference(self, reference: str) -> MarkdownDocument:
        clean = reference.strip().strip("/")
        if "@" in clean:
            clean = clean.split("@", 1)[1]
        if "/" in clean:
            clean = clean.rsplit("/", 1)[1]
        for document in self._skill_documents():
            path = document.path
            if path.name != "SKILL.md":
                continue
            if clean in self._aliases_for_workflow(path, document):
                return document
        return self.load_skill(clean)

    def optional_context_files(self) -> list[Path]:
        candidates = [
            self.layout.root / "CLAUDE.md",
            self.layout.root / "AGENTS.md",
            self.layout.root / "GEMINI.md",
            self.layout.root / "README.md",
            self.layout.root_hooks_path,
            self.layout.docs_dir / "coordination-rules.md",
            self.layout.docs_dir / "agent-coordination-map.md",
            self.layout.docs_dir / "agent-roster.md",
            self.layout.docs_dir / "technical-preferences.md",
            self.layout.docs_dir / "coding-standards.md",
            self.layout.docs_dir / "hooks-reference.md",
        ]
        return [path for path in candidates if path.exists()]

    def settings_candidates(self) -> list[Path]:
        candidates = []
        if not self.bare:
            candidates.extend([
                _claude_home() / "settings.json",
                _managed_claude_dir() / "settings.json",
            ])
        candidates.extend([
            self.layout.settings_path,
            self.layout.root_hooks_path,
        ])
        for root in self.additional_dirs:
            candidates.extend([root / ".claude" / "settings.json", root / "hooks" / "hooks.json"])
        for plugin in self.plugin_layouts():
            candidates.append(plugin.root / "hooks" / "hooks.json")
            hooks_config = plugin.manifest.get("hooks")
            if isinstance(hooks_config, dict):
                candidates.append(plugin.manifest_path)
            elif isinstance(hooks_config, list):
                candidates.extend(_resolve_plugin_path(plugin.root, value) for value in hooks_config)
            elif hooks_config:
                candidates.append(_resolve_plugin_path(plugin.root, hooks_config))
        return _unique_existing(path for path in candidates if path.exists())

    def primary_settings_path(self) -> Path:
        candidates = self.settings_candidates()
        if candidates:
            return candidates[0]
        return self.layout.settings_path

    def skill_support_files(self, skill: MarkdownDocument, limit: int = 80) -> list[Path]:
        root = skill.path.parent
        if not root.exists():
            return []
        files = [
            path
            for path in sorted(_safe_walk_files(root))
            if path.is_file()
            and path.name != "SKILL.md"
            and not any(part in {".git", "__pycache__", "node_modules"} for part in path.parts)
        ]
        return files[:limit]

    def read_context_bundle(self, max_chars_per_file: int = 12000) -> str:
        sections: list[str] = []
        for path in self.optional_context_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars_per_file:
                text = text[:max_chars_per_file] + "\n\n[TRUNCATED BY RUNTIME]\n"
            try:
                rel = path.relative_to(self.layout.root)
            except ValueError:
                rel = path
            sections.append(f"## {rel.as_posix()}\n\n{text}")
        return "\n\n---\n\n".join(sections)

    def describe_skill_support(self, skill: MarkdownDocument) -> str:
        files = self.skill_support_files(skill)
        if not files:
            return "No supporting files discovered next to this skill."
        lines = []
        for path in files:
            rel = path.relative_to(skill.path.parent)
            lines.append(f"- `{rel.as_posix()}`")
        return "\n".join(lines)

    def plugin_layouts(self) -> list[PluginLayout]:
        return self._plugin_layouts(include_disabled=False)

    def all_plugin_layouts(self) -> list[PluginLayout]:
        return self._plugin_layouts(include_disabled=True)

    def plugin_statuses(self) -> list[dict[str, object]]:
        return plugin_status_rows(self.layout.root, self.all_plugin_layouts(), include_disabled=True)

    def _plugin_layouts(self, *, include_disabled: bool) -> list[PluginLayout]:
        manifests = []
        for root in self._roots_for_discovery():
            direct = root / ".claude-plugin" / "plugin.json"
            if direct.exists():
                manifests.append(direct)
            if not (self.additional_dirs and root == self.layout.root):
                manifests.extend(
                    path
                    for path in _safe_walk_files(root)
                    if path.name == "plugin.json" and path.parent.name == ".claude-plugin"
                )

        layouts: list[PluginLayout] = []
        for manifest_path in _unique_existing(manifests):
            try:
                import json

                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
            root = manifest_path.parent.parent.resolve()
            name = str(manifest.get("name") or root.name)
            if not include_disabled and not is_plugin_enabled(self.layout.root, name=name, root=root):
                continue
            layouts.append(PluginLayout(root=root, name=name, manifest_path=manifest_path, manifest=manifest))
        return layouts

    def plugin_root_for(self, path: Path) -> Path | None:
        resolved = path.resolve()
        for plugin in self.plugin_layouts():
            try:
                resolved.relative_to(plugin.root)
            except ValueError:
                continue
            return plugin.root
        return None

    def _skill_paths(self) -> list[Path]:
        candidates: list[Path] = []
        if not self.bare:
            for root in self._project_roots_up_to_home():
                candidates.extend(self._command_paths(root / ".claude" / "commands"))
                candidates.extend(self._children_with_skill(root / ".claude" / "skills"))
            candidates.extend(self._command_paths(_claude_home() / "commands"))
            candidates.extend(self._children_with_skill(_claude_home() / "skills"))
            candidates.extend(self._children_with_skill(_managed_claude_dir() / "skills"))
        for root in self._roots_for_discovery():
            candidates.extend(self._command_paths(root / "commands"))
            candidates.extend(self._children_with_skill(root / "skills"))
            candidates.extend(self._command_paths(root / ".claude" / "commands"))
            candidates.extend(self._children_with_skill(root / ".claude" / "skills"))
        for plugin in self.plugin_layouts():
            candidates.extend(self._plugin_workflow_paths(plugin))
        if not self.bare and not self.additional_dirs and (
            not self.layout.skills_dir.exists()
            and not self.layout.root_skills_dir.exists()
            and not self.layout.commands_dir.exists()
            and not self.layout.root_commands_dir.exists()
            and not self.plugin_layouts()
        ):
            candidates.extend(self._recursive_skill_paths(self.layout.root))
        for root in self._roots_for_discovery():
            root_skill = root / "SKILL.md"
            if root_skill.exists():
                candidates.append(root_skill)
        return _unique_existing(candidates)

    def _agent_paths(self) -> list[Path]:
        candidates: list[Path] = []
        if not self.bare:
            for root in self._project_roots_up_to_home():
                directory = root / ".claude" / "agents"
                if directory.exists():
                    candidates.extend(path for path in _safe_walk_files(directory) if path.suffix.lower() == ".md")
            user_agents = _claude_home() / "agents"
            if user_agents.exists():
                candidates.extend(path for path in _safe_walk_files(user_agents) if path.suffix.lower() == ".md")
        for root in self._roots_for_discovery():
            for directory in [root / ".claude" / "agents", root / "agents"]:
                if directory.exists():
                    candidates.extend(path for path in _safe_walk_files(directory) if path.suffix.lower() == ".md")
        for plugin in self.plugin_layouts():
            for directory in self._plugin_component_dirs(plugin, "agents", "agents"):
                if directory.exists():
                    candidates.extend(path for path in _safe_walk_files(directory) if path.suffix.lower() == ".md")
        return _unique_existing(candidates)

    def _skill_documents(self) -> list[MarkdownDocument]:
        docs = [read_markdown_document(path) for path in self._skill_paths()]
        docs.extend(self._bundled_skills())
        return docs

    def _bundled_skills(self) -> list[MarkdownDocument]:
        if _env_truthy("CODEX_SKILL_RUNTIME_DISABLE_BUNDLED_SKILLS"):
            return []
        return bundled_skill_documents(self.layout.root)

    def _roots_for_discovery(self) -> list[Path]:
        return _unique_paths([self.layout.root, *self.additional_dirs])

    def _project_roots_up_to_home(self) -> list[Path]:
        roots: list[Path] = []
        home = Path.home().resolve()
        current = self.layout.root
        for root in [current, *current.parents]:
            roots.append(root)
            if root == home or root.parent == root:
                break
        return _unique_paths(roots)

    def _children_with_skill(self, directory: Path) -> Iterable[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        return [
            path
            for path in _safe_walk_files(directory)
            if path.name == "SKILL.md"
        ]

    def _recursive_skill_paths(self, directory: Path) -> Iterable[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        return [
            path
            for path in _safe_walk_files(directory)
            if path.name == "SKILL.md"
            if not any(part in {".git", "__pycache__", "node_modules"} for part in path.relative_to(directory).parts)
        ]

    def _command_paths(self, directory: Path) -> Iterable[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        return [
            path
            for path in _safe_walk_files(directory)
            if path.suffix.lower() == ".md"
            if not any(part in {".git", "__pycache__", "node_modules"} for part in path.parts)
        ]

    def _plugin_workflow_paths(self, plugin: PluginLayout) -> list[Path]:
        candidates: list[Path] = []
        for directory in self._plugin_component_dirs(plugin, "commands", "commands"):
            candidates.extend(self._command_paths(directory))
        for directory in self._plugin_component_dirs(plugin, "skills", "skills"):
            candidates.extend(self._children_with_skill(directory))
        root_skill = plugin.root / "SKILL.md"
        if root_skill.exists() and not any(directory.exists() for directory in self._plugin_component_dirs(plugin, "skills", "skills")):
            candidates.append(root_skill)
        return candidates

    def _plugin_component_dirs(self, plugin: PluginLayout, key: str, default_dir: str) -> list[Path]:
        configured = plugin.manifest.get(key)
        values: list[object] = [default_dir]
        if isinstance(configured, list):
            values.extend(configured)
        elif configured:
            values.append(configured)

        return _unique_paths(_resolve_plugin_path(plugin.root, value) for value in values)

    def _display_name(self, path: Path) -> str:
        document = read_markdown_document(path)
        return self._display_name_for_document(path, document)

    def _display_name_for_document(self, path: Path, document: MarkdownDocument) -> str:
        aliases = self._aliases_for_workflow(path, document)
        for alias in aliases:
            if ":" in alias:
                return alias
        return aliases[0]

    def _aliases_for_workflow(self, path: Path, document: MarkdownDocument) -> list[str]:
        metadata_name = str(document.metadata.get("name") or "").strip()
        bare = metadata_name or self._bare_workflow_name(path)
        aliases = [bare]
        command_namespace = self._command_namespace(path)
        if command_namespace:
            aliases.insert(0, command_namespace)
        plugin = self._plugin_for(path)
        if plugin is not None:
            aliases.insert(0, f"{plugin.name}:{bare}")
        elif root_namespace := self._namespace_for_path(path):
            aliases.insert(0, f"{root_namespace}:{bare}")
        return _unique_text(aliases)

    def _aliases_for_agent(self, path: Path, document: MarkdownDocument) -> list[str]:
        bare = str(document.metadata.get("name") or path.stem).strip()
        aliases = [bare, path.stem]
        plugin = self._plugin_for(path)
        if plugin is not None:
            aliases.insert(0, f"{plugin.name}:{bare}")
        elif root_namespace := self._namespace_for_path(path):
            aliases.insert(0, f"{root_namespace}:{bare}")
        return _unique_text(aliases)

    def _bare_workflow_name(self, path: Path) -> str:
        return path.parent.name if path.name == "SKILL.md" else path.stem

    def _command_namespace(self, path: Path) -> str | None:
        for directory in [self.layout.commands_dir, self.layout.root_commands_dir]:
            namespace = _relative_command_namespace(path, directory)
            if namespace:
                return namespace
        plugin = self._plugin_for(path)
        if plugin is None:
            return None
        for directory in self._plugin_component_dirs(plugin, "commands", "commands"):
            namespace = _relative_command_namespace(path, directory)
            if namespace:
                return namespace
        return None

    def _workflow_source(self, path: Path) -> str:
        for root in self._roots_for_discovery():
            try:
                rel = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if rel.startswith(".claude/commands/"):
                return "commands_DEPRECATED"
            if rel.startswith(".claude/skills/") or rel.startswith("skills/"):
                return "skills"
            if rel.startswith("commands/"):
                return "commands_DEPRECATED"
            if rel == "SKILL.md" or rel.endswith("/SKILL.md"):
                return "skills"
        return "skills"

    def _plugin_for(self, path: Path) -> PluginLayout | None:
        resolved = path.resolve()
        for plugin in self.plugin_layouts():
            try:
                resolved.relative_to(plugin.root)
            except ValueError:
                continue
            return plugin
        return None

    def _namespace_for_path(self, path: Path) -> str | None:
        resolved = path.resolve()
        for namespace, root in _configured_root_namespaces().items():
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return namespace
        return None


def _unique_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _resolve_plugin_path(plugin_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else plugin_root / path


def _unique_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _configured_root_namespaces() -> dict[str, Path]:
    raw = (
        os.environ.get("CODEX_SKILL_RUNTIME_NAMESPACES")
        or os.environ.get("SKILL_RUNTIME_NAMESPACES")
        or os.environ.get("CLAUDE_CODE_SKILL_NAMESPACES")
        or ""
    )
    result: dict[str, Path] = {}
    for entry in raw.replace("\n", ";").split(";"):
        item = entry.strip()
        if not item or "=" not in item:
            continue
        namespace, raw_path = item.split("=", 1)
        namespace = _clean_namespace(namespace)
        if not namespace:
            continue
        try:
            result[namespace] = Path(raw_path.strip().strip('"')).expanduser().resolve()
        except OSError:
            continue
    return result


def _clean_namespace(value: str) -> str:
    text = value.strip().lower()
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text).strip("-")


def format_skill_listings_within_budget(
    listings: list[SkillListing],
    *,
    context_window_tokens: int | None = None,
    max_chars: int | None = None,
) -> str:
    if not listings:
        return ""
    budget = max_chars if max_chars is not None else _skill_char_budget(context_window_tokens)
    entries = [_format_skill_listing(item) for item in listings]
    full = "\n".join(entries)
    if len(full) <= budget:
        return full

    overhead = sum(len(f"- `{item.name}`: ") + len(_listing_suffix(item)) + 1 for item in listings)
    available = max(0, budget - overhead)
    per_desc = available // max(1, len(listings))
    if per_desc < MIN_LISTING_DESC_CHARS:
        return "\n".join(f"- `{item.name}`{_listing_suffix(item)}" for item in listings)
    return "\n".join(
        f"- `{item.name}`: {_truncate(_listing_description(item), per_desc)}{_listing_suffix(item)}"
        for item in listings
    )


def _skill_char_budget(context_window_tokens: int | None) -> int:
    if context_window_tokens is None:
        return DEFAULT_SKILL_CHAR_BUDGET
    return max(1000, int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT))


def _format_skill_listing(item: SkillListing) -> str:
    return f"- `{item.name}`: {_listing_description(item)}{_listing_suffix(item)}"


def _listing_description(item: SkillListing) -> str:
    return _truncate(item.description, MAX_LISTING_DESC_CHARS)


def _listing_suffix(item: SkillListing) -> str:
    details = [item.source]
    if item.context:
        details.append(f"context={item.context}")
    if item.agent:
        details.append(f"agent={item.agent}")
    if not item.user_invocable:
        details.append("hidden-user")
    return f" ({', '.join(details)})"


def _skill_description(document: MarkdownDocument) -> str:
    description = str(document.metadata.get("description") or "").strip()
    when_to_use = str(document.metadata.get("when_to_use") or document.metadata.get("when-to-use") or "").strip()
    if description and when_to_use:
        return f"{description} - {when_to_use}"
    if description:
        return description
    for line in document.body.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text
    return "No description supplied."


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "..."
    return text[: max(0, limit - 1)].rstrip() + "…"


def _env_paths(name: str) -> list[Path]:
    value = os.environ.get(name, "")
    if not value:
        return []
    raw_parts: list[str] = []
    for chunk in value.split(os.pathsep):
        raw_parts.extend(part for part in chunk.split(";") if part)
    return [Path(part).expanduser().resolve() for part in raw_parts if part.strip()]


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_HOME")
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".claude").resolve()


def _managed_claude_dir() -> Path:
    configured = os.environ.get("CLAUDE_CODE_MANAGED_DIR") or os.environ.get("CODEX_SKILL_RUNTIME_MANAGED_DIR")
    return Path(configured).expanduser().resolve() if configured else (_claude_home() / "managed").resolve()


def _relative_command_namespace(path: Path, directory: Path) -> str | None:
    if not directory.exists():
        return None
    try:
        rel = path.resolve().relative_to(directory.resolve())
    except ValueError:
        return None
    if rel.suffix.lower() != ".md":
        return None
    return ":".join(rel.with_suffix("").parts)


def _safe_walk_files(directory: Path) -> Iterable[Path]:
    ignored = {".git", ".codex-skill-runtime", "__pycache__", "node_modules", ".next", "dist"}
    try:
        walker = os.walk(directory)
        for root, dirs, files in walker:
            dirs[:] = [dirname for dirname in dirs if dirname not in ignored]
            for filename in files:
                yield Path(root) / filename
    except OSError:
        return
