from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .frontmatter import MarkdownDocument


def skill_prompt(
    *,
    command: str,
    arguments: str,
    skill: MarkdownDocument,
    agent: MarkdownDocument,
    context_bundle: str,
    skill_support: str = "",
    project_root: Path,
    assume_yes: bool,
    qa_mode: str,
    runtime_profile: str = "",
) -> str:
    rendered_skill_body = render_markdown_body(document=skill, arguments=arguments, project_root=project_root)
    runtime_contract = _runtime_contract(assume_yes=assume_yes, qa_mode=qa_mode)
    rendered_agent_body = _maybe_lean_agent_body(agent.body)
    return f"""# Claude Skill Codex Runtime Invocation

You are Codex running inside a clean-room lightweight runtime for Claude Code skills.
The original skill files are the source of truth. Do not rewrite or patch original skill source files.

{runtime_contract}

{runtime_profile}

## Invocation

- Project root: `{project_root}`
- Command: `/{command}`
- Arguments: `{arguments}`

## Skill Frontmatter

```json
{skill.metadata}
```

## Skill Body

{rendered_skill_body}

## Skill Supporting Files

{skill_support}

## Agent Frontmatter

```json
{agent.metadata}
```

## Agent Body

{rendered_agent_body}

## Repository Context

{context_bundle}
"""


def _runtime_contract(*, assume_yes: bool, qa_mode: str) -> str:
    approval = (
        "Proceed with necessary file changes inside the workspace."
        if assume_yes
        else "Ask before major decisions or file writes that require approval."
    )
    if _lean_context_enabled():
        return f"""## Runtime Contract

- Execute the loaded skill as far as inputs allow; use the routed agent as your responsibility boundary.
- Treat `Task` in the original skill as real delegation, not as prose. If a subagent is required, emit a line exactly like:
  `RUNTIME_TASK_REQUEST: agent=<agent-name>; purpose=<short purpose>; inputs=<paths or concise context>`
- Treat `AskUserQuestion` as a runtime pause. If a required answer is missing, ask one concise question and stop.
- If the skill tells you to invoke another skill, request a `skill` action with that skill name.
- Use runtime actions for file reads/writes, commands, user questions, and delegation.
- If supporting context is missing, request `read_file` instead of guessing.
- {approval}
- Produce actual files and evidence; do not claim unrun tests.
- QA mode requested by runtime: {qa_mode}"""
    return f"""## Runtime Contract

- Execute the workflow from the skill exactly as far as the available inputs allow.
- Use the agent definition as your role/persona and responsibility boundary.
- Treat `Task` in the original skill as real delegation, not as prose. If a subagent is required, emit a line exactly like:
  `RUNTIME_TASK_REQUEST: agent=<agent-name>; purpose=<short purpose>; inputs=<paths or concise context>`
- Treat `AskUserQuestion` as a runtime pause. If a required answer is missing, ask one concise question and stop.
- If the skill references supporting files by relative path, request a `read_file` action for the relevant file before relying on its contents.
- If the skill tells you to invoke another skill, request a `skill` action with that skill name.
- Use the Runtime SkillTool Registry in Repository Context to discover other loaded skills. Do not assume any domain-specific capability is built into the runtime core.
- Store cross-skill continuity such as global visual/audio style, asset inventory, and durable project decisions in runtime project memory through `project_memory_write` or `asset_register` when strict tools are available.
- {approval}
- For implementation work, produce actual files, commands run, and evidence. Do not claim testing unless you ran a command or clearly label it as not run.
- QA mode requested by runtime: {qa_mode}"""


def agent_task_prompt(
    *,
    parent_command: str,
    task_agent: MarkdownDocument,
    purpose: str,
    inputs: str,
    parent_result: str,
    context_bundle: str,
    project_root: Path,
    runtime_profile: str = "",
    preloaded_skills: str = "",
    agent_memory: str = "",
) -> str:
    return f"""# Claude Skill Codex Runtime Subagent Task

You are a spawned subagent. The runtime is emulating Claude Code's `Task`/`Agent` mechanism by starting a separate Codex session with the original agent definition or a synthetic generic agent.

## Runtime Contract

- Stay within your agent's responsibility.
- Return a concrete result with evidence, file paths inspected or changed, and blockers.
- If this is QA, do not fix bugs. Report them with reproduction steps.
- Use `VERDICT: PASS`, `VERDICT: PASS WITH WARNINGS`, `VERDICT: FAIL`, or `VERDICT: BLOCKED` when the task is a gate or validation task.

{runtime_profile}

## Parent Command

/{parent_command}

## Task Purpose

{purpose}

## Inputs

{inputs}

## Parent Result

{parent_result}

## Agent Frontmatter

```json
{task_agent.metadata}
```

## Agent Body

{task_agent.body}

## Preloaded Agent Skills

{preloaded_skills or "No agent-declared skills were preloaded."}

## Agent Memory

{agent_memory or "No agent memory was loaded."}

## Repository Context

{context_bundle}

Project root: `{project_root}`
"""


def render_markdown_body(*, document: MarkdownDocument, arguments: str, project_root: Path) -> str:
    plugin_root = _find_plugin_root(document.path)
    rendered = _expand_plugin_root(document.body, plugin_root)
    rendered = _render_file_references(rendered, arguments=arguments, project_root=project_root, plugin_root=plugin_root)
    rendered = _render_arguments(rendered, arguments)
    rendered = _render_dynamic_context(rendered, project_root=project_root, plugin_root=plugin_root)
    return _maybe_lean_skill_body(rendered, arguments=arguments)


def _maybe_lean_skill_body(body: str, *, arguments: str) -> str:
    if not _lean_context_enabled():
        return body
    try:
        max_chars = int(os.environ.get("SKILL_RUNTIME_LEAN_SKILL_BODY_CHARS", "8000"))
    except ValueError:
        max_chars = 8000
    try:
        section_max_chars = int(os.environ.get("SKILL_RUNTIME_LEAN_SECTION_MAX_CHARS", "1200"))
    except ValueError:
        section_max_chars = 1200
    if max_chars <= 0 or len(body) <= max_chars:
        return body

    sections = _markdown_h2_sections(body)
    if not sections:
        return body[:max_chars] + "\n\n[TRUNCATED BY LEAN SKILL BODY]\n"

    wanted = _lean_section_names(arguments)
    kept: list[str] = []
    omitted: list[str] = []
    for heading, text in sections:
        if heading in wanted or any(marker in heading.lower() for marker in wanted):
            kept.append(_truncate_lean_section(text, section_max_chars=section_max_chars))
        else:
            omitted.append(heading)

    if not kept:
        return body[:max_chars] + "\n\n[TRUNCATED BY LEAN SKILL BODY]\n"

    header = (
        "[LEAN SKILL BODY]\n"
        "Runtime context mode is lean, so this long skill body was reduced to the sections most relevant to the current invocation. "
        "Use `read_file` on the original SKILL.md if an omitted section becomes necessary.\n"
    )
    omitted_line = f"\nOmitted sections: {', '.join(omitted[:20])}" if omitted else ""
    rendered = header + omitted_line + "\n\n" + "\n\n---\n\n".join(kept)
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars] + "\n\n[TRUNCATED BY LEAN SKILL BODY]\n"
    return rendered


def _maybe_lean_agent_body(body: str) -> str:
    if not _lean_context_enabled():
        return body
    try:
        max_chars = int(os.environ.get("SKILL_RUNTIME_LEAN_AGENT_BODY_CHARS", "5000"))
    except ValueError:
        max_chars = 5000
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    sections = _markdown_h2_sections(body)
    wanted = {
        "two modes",
        "collaboration protocol",
        "prototype paths",
        "core philosophy: speed over quality (concept prototype)",
        "isolation requirements",
        "document what you learned, not what you built",
        "what this agent must not do",
        "delegation map",
    }
    kept = [
        _truncate_lean_section(text, section_max_chars=900)
        for heading, text in sections
        if heading in wanted
    ]
    if not kept:
        return body[:max_chars].rstrip() + "\n\n[TRUNCATED BY LEAN AGENT BODY]\n"
    rendered = (
        "[LEAN AGENT BODY]\n"
        "Long agent instructions were reduced to role, boundaries, and implementation-relevant rules.\n\n"
        + "\n\n---\n\n".join(kept)
    )
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars].rstrip() + "\n\n[TRUNCATED BY LEAN AGENT BODY]\n"
    return rendered


def _truncate_lean_section(text: str, *, section_max_chars: int) -> str:
    if section_max_chars <= 0 or len(text) <= section_max_chars:
        return text
    return text[:section_max_chars].rstrip() + "\n\n[TRUNCATED BY LEAN SECTION]\n"


def _lean_context_enabled() -> bool:
    value = os.environ.get("SKILL_RUNTIME_CONTEXT_MODE") or os.environ.get("CODEX_SKILL_RUNTIME_CONTEXT_MODE") or ""
    return value.strip().lower() in {"lean", "lite", "minimal", "local"}


def _lean_section_names(arguments: str) -> set[str]:
    lowered = arguments.lower()
    wanted = {
        "purpose",
        "phase 5: implement",
        "phase 7: generate prototype report",
        "phase 9: summary and next steps",
        "prototype paths",
        "core philosophy: speed over quality (concept prototype)",
        "isolation requirements",
        "document what you learned, not what you built",
        "what this agent must not do",
        "delegation map",
    }
    if "--spike" in lowered or "spike" in lowered:
        wanted.add("spike mode")
    if "--path html" in lowered or "html" in lowered:
        wanted.add("html")
        wanted.add("html path")
    if "--path engine" in lowered or "godot" in lowered or "engine" in lowered:
        wanted.add("engine")
        wanted.add("engine path")
    if "--path paper" in lowered or "paper" in lowered:
        wanted.add("paper")
        wanted.add("paper path")
    return wanted


def _markdown_h2_sections(body: str) -> list[tuple[str, str]]:
    lines = body.splitlines()
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_heading:
                sections.append((current_heading, current))
            elif current:
                preamble.extend(current)
            current_heading = line[3:].strip().lower()
            current = [line]
            continue
        current.append(line)
    if current_heading:
        sections.append((current_heading, current))
    elif current:
        preamble.extend(current)

    rendered: list[tuple[str, str]] = []
    if preamble:
        rendered.append(("preamble", "\n".join(preamble).strip()))
    rendered.extend((heading, "\n".join(content).strip()) for heading, content in sections)
    return [(heading, text) for heading, text in rendered if text]


def _split_arguments(arguments: str) -> list[str]:
    pattern = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
    values: list[str] = []
    for match in pattern.finditer(arguments):
        values.append(next(group for group in match.groups() if group is not None))
    return values


def _render_arguments(body: str, arguments: str) -> str:
    values = _split_arguments(arguments)
    rendered = body
    for index, value in enumerate(values):
        rendered = rendered.replace(f"$ARGUMENTS[{index}]", value)
    rendered = re.sub(r"\$(\d+)", lambda match: _positional_argument(match, values), rendered)
    rendered = rendered.replace("$ARGUMENTS", arguments)
    return rendered


def _positional_argument(match: re.Match[str], values: list[str]) -> str:
    index = int(match.group(1)) - 1
    if 0 <= index < len(values):
        return values[index]
    return ""


def _expand_plugin_root(value: str, plugin_root: Path | None) -> str:
    if plugin_root is None:
        return value
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))


def _render_file_references(
    body: str,
    *,
    arguments: str,
    project_root: Path,
    plugin_root: Path | None,
) -> str:
    pattern = re.compile(r"(?<![\w./-])@(?P<ref>[^\s`'\"<>]+)")
    values = _split_arguments(arguments)
    remaining = 24

    def replace(match: re.Match[str]) -> str:
        nonlocal remaining
        if remaining <= 0:
            return match.group(0)
        remaining -= 1
        raw_ref = match.group("ref")
        resolved_ref = _render_arguments(_expand_plugin_root(raw_ref, plugin_root), arguments)
        path = _resolve_file_reference(resolved_ref, project_root=project_root, plugin_root=plugin_root)
        if path is None and raw_ref in {"$ARGUMENTS", "${ARGUMENTS}"} and len(values) == 1:
            path = _resolve_file_reference(values[0], project_root=project_root, plugin_root=plugin_root)
        return _file_reference_block(raw_ref=raw_ref, resolved_ref=resolved_ref, path=path)

    return pattern.sub(replace, body)


def _resolve_file_reference(reference: str, *, project_root: Path, plugin_root: Path | None) -> Path | None:
    candidates = _reference_candidates(reference, project_root=project_root, plugin_root=plugin_root)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    stripped = reference.rstrip(".,;)]}")
    if stripped != reference:
        for candidate in _reference_candidates(stripped, project_root=project_root, plugin_root=plugin_root):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.exists() and resolved.is_file():
                return resolved
    return None


def _reference_candidates(reference: str, *, project_root: Path, plugin_root: Path | None) -> list[Path]:
    text = reference.strip()
    if not text:
        return []
    path = Path(text)
    if path.is_absolute():
        return [path]
    candidates = [project_root / path]
    if plugin_root is not None:
        candidates.append(plugin_root / path)
    return candidates


def _file_reference_block(*, raw_ref: str, resolved_ref: str, path: Path | None, max_chars: int = 30000) -> str:
    heading = f"@{raw_ref}"
    if path is None:
        return f"{heading}\n\n[FILE_REFERENCE_MISSING: {resolved_ref}]"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"{heading}\n\n[FILE_REFERENCE_ERROR: {path} - {exc}]"
    truncated = len(data) > max_chars
    text = data[:max_chars].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[TRUNCATED BY RUNTIME]\n"
    language = path.suffix.lstrip(".") or "text"
    return (
        f"{heading}\n\n"
        f"## File Reference: {path}\n\n"
        f"```{language}\n{text}\n```"
    )


def _render_dynamic_context(body: str, *, project_root: Path, plugin_root: Path | None) -> str:
    pattern = re.compile(r"!\`([^`]+)\`")

    def replace(match: re.Match[str]) -> str:
        command = match.group(1)
        completed = _run_dynamic_context_command(command, project_root=project_root, plugin_root=plugin_root)
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            return f"[DYNAMIC_CONTEXT_ERROR exit={completed.returncode}: {output[:1000]}]"
        return (completed.stdout or "").strip()

    return pattern.sub(replace, body)


def _run_dynamic_context_command(
    command: str,
    *,
    project_root: Path,
    plugin_root: Path | None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)).replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    if shutil.which("bash") is not None:
        try:
            return subprocess.run(
                ["bash", "-lc", command],
                cwd=str(project_root),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=20,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "dynamic context timed out")
    try:
        return subprocess.run(
            command,
            cwd=str(project_root),
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=20,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "dynamic context timed out")


def _find_plugin_root(path: Path) -> Path | None:
    for parent in [path.parent, *path.parents]:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return parent.resolve()
    return None


def qa_prompt(
    *,
    task_agent: MarkdownDocument,
    project_root: Path,
    target_path: str,
    parent_result: str,
    context_bundle: str,
) -> str:
    return f"""# Codex Skill Runtime Required QA Pass

You are the QA Tester subagent from the loaded skill repository's agent source.
This pass exists because static skill conversion previously missed obvious behavioral bugs.

## Hard Requirements

- Do not fix bugs.
- Inspect the target project and identify how to run its most relevant verification commands.
- Run available smoke, unit, integration, UI, end-to-end, or domain-specific tests if present.
- Use loaded skill/plugin instructions and the runtime capability registry to find project-specific verification tools. Do not assume a specific engine or framework.
- Specifically check intermediate state updates, not only terminal outcomes:
  - each user-visible action changes the expected state immediately
  - blocked or invalid actions do not mutate state unless the design explicitly says they should
  - counters, status displays, logs, panels, or other UI text update every time the underlying state changes
  - collection, inventory, score, resource, or progress values stay consistent after each step
  - restart, reset, undo, retry, or reload flows restore the expected baseline
  - success conditions are reachable and not falsely triggered
- If you cannot run the target, return `VERDICT: BLOCKED` and explain exactly why.
- Final answer must contain:
  - `VERDICT: PASS` / `PASS WITH WARNINGS` / `FAIL` / `BLOCKED`
  - `EVIDENCE MATRIX`
  - commands run
  - bugs found or "none"

## Target Path

{target_path}

## Parent Result

{parent_result}

## Agent Frontmatter

```json
{task_agent.metadata}
```

## Agent Body

{task_agent.body}

## Repository Context

{context_bundle}

Project root: `{project_root}`
"""
