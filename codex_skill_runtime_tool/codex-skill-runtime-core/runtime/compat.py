from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .frontmatter import MarkdownDocument
from .state_paths import runtime_state_path


EFFORT_VALUES = {"minimal", "low", "medium", "high", "xhigh"}


@dataclass(frozen=True)
class InvocationProfile:
    output_style: str
    permission_mode: str
    model: str | None
    effort: str | None
    coordinator: bool
    scratchpad_dir: Path | None

    def prompt_section(self) -> str:
        lines = [
            "## Claude Code Compatibility Profile",
            "",
            "This section is a clean-room compatibility layer for Claude Code runtime behavior. "
            "It is not copied from Claude Code's private system prompt.",
            "",
            f"- Output style: {self.output_style}",
            f"- Permission mode: {self.permission_mode}",
            f"- Coordinator mode: {'enabled' if self.coordinator else 'disabled'}",
            f"- Requested model: {self.model or 'inherit Codex default'}",
            f"- Requested reasoning effort: {self.effort or 'inherit Codex default'}",
        ]
        if self.scratchpad_dir is not None:
            lines.append(f"- Scratchpad directory: `{self.scratchpad_dir}`")
        lines.extend(
            [
                "",
                "Behavioral requirements:",
                "- Treat skill and agent frontmatter as runtime instructions, not decorative metadata.",
                "- If `paths` is present, treat the skill as specialized for matching files.",
                "- If `user-invocable` or `disable-model-invocation` is false/true, respect that visibility when deciding whether to invoke another skill.",
                "- If `context: fork` appears, use a Task/Agent action to run it in an isolated worker context.",
                "- If an agent declares `skills`, preload and use those skills before improvising.",
                "- If an agent declares `mcpServers`, those MCP servers are available to that agent's MCP calls.",
                "- If an agent declares `memory`, read or update the agent memory files through runtime tools instead of relying on hidden state.",
            ]
        )
        if self.output_style != "default":
            lines.extend(["", output_style_instruction(self.output_style)])
        if self.coordinator:
            lines.extend(["", coordinator_instruction(self.scratchpad_dir)])
        return "\n".join(lines)


def invocation_profile(
    *,
    skill: MarkdownDocument | None = None,
    agent: MarkdownDocument | None = None,
    project_root: Path,
    assume_yes: bool,
    explicit_output_style: str | None = None,
) -> InvocationProfile:
    output_style = (
        explicit_output_style
        or os.environ.get("CODEX_SKILL_RUNTIME_OUTPUT_STYLE")
        or os.environ.get("CLAUDE_CODE_OUTPUT_STYLE")
        or "default"
    )
    permission_mode = str(
        first_present(
            metadata_get(agent, "permissionMode"),
            metadata_get(skill, "permissionMode"),
            os.environ.get("CODEX_SKILL_RUNTIME_PERMISSION_MODE"),
            "acceptEdits" if assume_yes else "default",
        )
    )
    model = resolve_model_override(
        first_present(metadata_get(skill, "model"), metadata_get(agent, "model"))
    )
    effort = normalize_effort(
        first_present(metadata_get(skill, "effort"), metadata_get(agent, "effort"))
    )
    coordinator = env_truthy("CODEX_SKILL_RUNTIME_COORDINATOR") or env_truthy("CLAUDE_CODE_COORDINATOR_MODE")
    scratchpad = runtime_state_path(project_root, "scratchpad") if coordinator else None
    if scratchpad is not None:
        scratchpad.mkdir(parents=True, exist_ok=True)
    return InvocationProfile(
        output_style=output_style,
        permission_mode=permission_mode,
        model=model,
        effort=effort,
        coordinator=coordinator,
        scratchpad_dir=scratchpad,
    )


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def metadata_get(document: MarkdownDocument | None, key: str) -> Any:
    return None if document is None else document.metadata.get(key)


def as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(as_list(item))
        return result
    if isinstance(value, tuple):
        result = []
        for item in value:
            result.extend(as_list(item))
        return result
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [part.strip() for part in text.split() if part.strip()]


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def user_invocable(document: MarkdownDocument) -> bool:
    return parse_bool(document.metadata.get("user-invocable"), default=True)


def model_invocable(document: MarkdownDocument) -> bool:
    return not parse_bool(document.metadata.get("disable-model-invocation"), default=False)


def skill_paths(document: MarkdownDocument) -> list[str]:
    patterns = []
    for raw in as_list(document.metadata.get("paths")):
        normalized = raw.replace("\\", "/").strip()
        if normalized.endswith("/**"):
            normalized = normalized[:-3]
        if normalized and normalized != "**":
            patterns.append(normalized)
    return patterns


def matches_paths(document: MarkdownDocument, paths: Iterable[str], *, base: Path) -> bool:
    patterns = skill_paths(document)
    if not patterns:
        return True
    rels = []
    for value in paths:
        path = Path(value)
        if path.is_absolute():
            try:
                rels.append(path.resolve().relative_to(base.resolve()).as_posix())
            except ValueError:
                rels.append(path.as_posix())
        else:
            rels.append(path.as_posix())
    for rel in rels:
        rel = rel.replace("\\", "/")
        for pattern in patterns:
            if fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern.rstrip("/") + "/"):
                return True
    return False


def argument_names(document: MarkdownDocument) -> list[str]:
    return as_list(document.metadata.get("arguments"))


def resolve_model_override(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "inherit":
        return None
    lowered = text.lower()
    alias_env = {
        "sonnet": "CODEX_SKILL_RUNTIME_MODEL_SONNET",
        "opus": "CODEX_SKILL_RUNTIME_MODEL_OPUS",
        "haiku": "CODEX_SKILL_RUNTIME_MODEL_HAIKU",
    }.get(lowered)
    if alias_env:
        return os.environ.get(alias_env)
    return text


def normalize_effort(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text == "max":
        return "xhigh"
    if text in EFFORT_VALUES:
        return text
    if text.isdigit():
        number = int(text)
        if number <= 1:
            return "low"
        if number <= 3:
            return "medium"
        if number <= 6:
            return "high"
        return "xhigh"
    return None


def output_style_instruction(style: str) -> str:
    normalized = style.strip().lower()
    if normalized in {"concise", "short"}:
        return "Output style instruction: be concise, concrete, and avoid unnecessary explanation."
    if normalized in {"explanatory", "explain"}:
        return "Output style instruction: explain decisions and assumptions clearly for a less experienced reader."
    if normalized in {"review", "code-review"}:
        return "Output style instruction: lead with findings, risks, and file references before summaries."
    return f"Output style instruction: follow the project's `{style}` output style as closely as the skill defines it."


def coordinator_instruction(scratchpad_dir: Path | None) -> str:
    scratchpad = f"\nScratchpad directory: `{scratchpad_dir}`" if scratchpad_dir is not None else ""
    return (
        "Coordinator mode instruction: orchestrate workers instead of doing every task in the main turn. "
        "Use Agent/Task for independent work, SendMessage to continue an existing worker with context, "
        "and TaskStop to stop a worker that is no longer useful."
        f"{scratchpad}"
    )


def agent_skill_references(agent: MarkdownDocument) -> list[str]:
    return as_list(agent.metadata.get("skills"))


def agent_memory_scope(agent: MarkdownDocument) -> str | None:
    value = str(agent.metadata.get("memory") or "").strip().lower()
    return value if value in {"user", "project", "local"} else None


def env_truthy(name: str) -> bool:
    return parse_bool(os.environ.get(name), default=False)


def bundled_skill_documents(root: Path) -> list[MarkdownDocument]:
    bundled: dict[str, tuple[str, str]] = {
        "verify": (
            "Run verification for recent work",
            "Inspect the relevant project, run the most appropriate tests or checks, and report VERDICT plus evidence.",
        ),
        "remember": (
            "Record durable project memory",
            "Summarize the durable lesson and write it to runtime memory or an explicitly requested project memory file.",
        ),
        "skillify": (
            "Turn a workflow into a skill",
            "Create or update a SKILL.md-style workflow with clear trigger conditions, inputs, tools, and validation steps.",
        ),
        "simplify": (
            "Simplify a complex implementation",
            "Find avoidable complexity, propose the smallest safe change, and verify behavior stays equivalent.",
        ),
        "stuck": (
            "Recover from blocked work",
            "Identify the blocker, list concrete evidence, and propose the next smallest diagnostic or implementation step.",
        ),
        "batch": (
            "Split work into parallel units",
            "Decompose the request into independent worker tasks with clear ownership, paths, and validation criteria.",
        ),
        "loop": (
            "Iterate until a condition is met",
            "Repeat plan, act, verify, and adjust until the requested stop condition is satisfied or a blocker is explicit.",
        ),
        "claude-api": (
            "Help with Claude API usage",
            "Answer Claude API implementation questions using available local references and verify against current docs when possible.",
        ),
        "dream": (
            "Consolidate session memory",
            "Summarize recent sessions into durable project knowledge and identify follow-up tasks.",
        ),
    }
    docs: list[MarkdownDocument] = []
    base = Path(__file__).resolve().parent / "bundled-skills"
    for name, (description, body) in bundled.items():
        docs.append(
            MarkdownDocument(
                path=base / name / "SKILL.md",
                metadata={
                    "name": name,
                    "description": description,
                    "source": "bundled",
                    "allowed-tools": ["Read", "Grep", "Glob", "Bash", "Write", "Edit", "MultiEdit", "TodoWrite", "Task"],
                },
                body=body,
                raw="",
            )
        )
    return docs
