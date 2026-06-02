from __future__ import annotations

import os
from dataclasses import dataclass

from .session_memory import estimate_tokens


DEFAULT_AUTOCOMPACT_BUFFER_TOKENS = 13000
DEFAULT_MIN_PRESERVED_CONTEXT_TOKENS = 10000


@dataclass(frozen=True)
class ContextSection:
    name: str
    text: str
    priority: int = 100
    required: bool = False


@dataclass(frozen=True)
class ContextBudgetResult:
    sections: list[ContextSection]
    report: str
    estimated_tokens_before: int
    estimated_tokens_after: int
    target_tokens: int | None
    omitted: list[str]


def context_window_tokens() -> int | None:
    for key in ("SKILL_RUNTIME_MODEL_CONTEXT_WINDOW", "CODEX_MODEL_CONTEXT_WINDOW", "MODEL_CONTEXT_WINDOW"):
        value = os.environ.get(key)
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return None


def apply_context_budget(
    sections: list[ContextSection],
    *,
    context_window: int | None = None,
    reserve_tokens: int = DEFAULT_AUTOCOMPACT_BUFFER_TOKENS,
    min_preserved_tokens: int = DEFAULT_MIN_PRESERVED_CONTEXT_TOKENS,
) -> ContextBudgetResult:
    context_window = context_window if context_window is not None else context_window_tokens()
    before = sum(estimate_tokens(section.text) for section in sections)
    if context_window is None or context_window <= 0:
        return ContextBudgetResult(
            sections=sections,
            report=_budget_report(
                context_window=None,
                reserve_tokens=reserve_tokens,
                before=before,
                after=before,
                target=None,
                omitted=[],
            ),
            estimated_tokens_before=before,
            estimated_tokens_after=before,
            target_tokens=None,
            omitted=[],
        )

    target = max(min_preserved_tokens, context_window - reserve_tokens)
    kept_rows: list[tuple[int, ContextSection]] = []
    omitted: list[str] = []
    used = 0
    ordered = sorted(enumerate(sections), key=lambda pair: (0 if pair[1].required else 1, pair[1].priority, pair[0]))
    for index, section in ordered:
        tokens = estimate_tokens(section.text)
        if section.required or used + tokens <= target or not kept_rows:
            kept_rows.append((index, section))
            used += tokens
            continue
        remaining_tokens = target - used
        if remaining_tokens >= 1000:
            truncated = _truncate_to_tokens(section.text, remaining_tokens)
            kept_rows.append(
                (
                    index,
                    ContextSection(
                        name=section.name,
                        text=truncated + "\n[TRUNCATED BY CONTEXT BUDGET]\n",
                        priority=section.priority,
                        required=section.required,
                    ),
                )
            )
            used += estimate_tokens(truncated)
        else:
            omitted.append(section.name)
    kept = [section for _, section in sorted(kept_rows, key=lambda row: row[0])]
    after = sum(estimate_tokens(section.text) for section in kept)
    return ContextBudgetResult(
        sections=kept,
        report=_budget_report(
            context_window=context_window,
            reserve_tokens=reserve_tokens,
            before=before,
            after=after,
            target=target,
            omitted=omitted,
        ),
        estimated_tokens_before=before,
        estimated_tokens_after=after,
        target_tokens=target,
        omitted=omitted,
    )


def budget_context_for_prompt(result: ContextBudgetResult) -> str:
    return result.report


def _budget_report(
    *,
    context_window: int | None,
    reserve_tokens: int,
    before: int,
    after: int,
    target: int | None,
    omitted: list[str],
) -> str:
    lines = [
        "## Runtime Context Budget",
        "",
        f"- Context window tokens: {context_window if context_window is not None else 'unknown'}",
        f"- Reserved compact/output buffer tokens: {reserve_tokens}",
        f"- Estimated context tokens before budgeting: {before}",
        f"- Estimated context tokens after budgeting: {after}",
    ]
    if target is not None:
        lines.append(f"- Target context tokens: {target}")
    if omitted:
        lines.append(f"- Omitted sections: {', '.join(omitted)}")
    else:
        lines.append("- Omitted sections: none")
    lines.extend(
        [
            "",
            "Use this report to understand why some runtime context may be summarized or absent. "
            "Request explicit reads or skill actions when missing context is required.",
        ]
    )
    return "\n".join(lines)


def _truncate_to_tokens(text: str, tokens: int) -> str:
    if tokens <= 0:
        return ""
    return text[: tokens * 4]
