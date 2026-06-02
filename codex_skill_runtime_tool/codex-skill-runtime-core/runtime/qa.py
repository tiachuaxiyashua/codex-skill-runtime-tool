from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .capabilities import discover_capabilities
from .frontmatter import MarkdownDocument


DEFAULT_QA_AGENT = "qa-tester"


@dataclass(frozen=True)
class QAAgentResolution:
    agent_name: str
    source: str
    reason: str = ""


def resolve_qa_agent(
    project_root: Path,
    *,
    skill: MarkdownDocument | None = None,
    agent: MarkdownDocument | None = None,
    additional_dirs: Iterable[Path] = (),
    fallback: str = DEFAULT_QA_AGENT,
) -> QAAgentResolution:
    configured = _first_text(
        os.environ.get("SKILL_RUNTIME_QA_AGENT"),
        os.environ.get("CODEX_SKILL_RUNTIME_QA_AGENT"),
    )
    if configured:
        return QAAgentResolution(configured, "env", "Resolved from runtime QA agent environment configuration.")

    for source_name, document in [("skill-frontmatter", skill), ("agent-frontmatter", agent)]:
        value = _qa_agent_from_metadata(document.metadata if document is not None else None)
        if value:
            return QAAgentResolution(value, source_name, "Resolved from document metadata.")

    for capability in discover_capabilities(project_root, additional_dirs=additional_dirs):
        value = _qa_agent_from_metadata(capability.metadata)
        if not value and _is_qa_capability(capability):
            value = capability.name
        if value:
            return QAAgentResolution(value, f"capability:{capability.source}", "Resolved from runtime capability metadata.")

    return QAAgentResolution(fallback, "fallback", "No QA agent override was configured.")


def _qa_agent_from_metadata(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    for key in ("qa_agent", "qa-agent", "qaAgent", "required_qa_agent", "required-qa-agent"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    qa = metadata.get("qa")
    if isinstance(qa, str) and qa.strip():
        return qa.strip()
    if isinstance(qa, dict):
        for key in ("agent", "agent_name", "agentName", "name"):
            value = qa.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _is_qa_capability(capability: Any) -> bool:
    kind = str(getattr(capability, "kind", "") or "").lower()
    name = str(getattr(capability, "name", "") or "").lower()
    metadata = getattr(capability, "metadata", {})
    if isinstance(metadata, dict):
        role = str(metadata.get("role") or metadata.get("agent_role") or "").lower()
        if role in {"qa", "quality", "quality-assurance", "test"}:
            return True
    return kind in {"qa", "qa-agent", "quality-assurance", "test-agent"} or name in {"qa", "quality-assurance"}


def _first_text(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""
