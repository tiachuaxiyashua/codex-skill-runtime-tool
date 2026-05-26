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
    approval_policy = (
        "The runtime was started with --assume-yes. Proceed with necessary file changes inside the workspace."
        if assume_yes
        else "If the original skill requires user approval before a major decision or file write, stop and ask in your final message."
    )
    rendered_skill_body = render_markdown_body(document=skill, arguments=arguments, project_root=project_root)
    return f"""# Claude Skill Codex Runtime Invocation

You are Codex running inside a clean-room lightweight runtime for Claude Code skills.
The original skill files are the source of truth. Do not rewrite or patch original skill source files.

## Runtime Contract

- Execute the workflow from the skill exactly as far as the available inputs allow.
- Use the agent definition as your role/persona and responsibility boundary.
- Treat `Task` in the original skill as real delegation, not as prose. If a subagent is required, emit a line exactly like:
  `RUNTIME_TASK_REQUEST: agent=<agent-name>; purpose=<short purpose>; inputs=<paths or concise context>`
- Treat `AskUserQuestion` as a runtime pause. If a required answer is missing, ask one concise question and stop.
- If the skill references supporting files by relative path, request a `read_file` action for the relevant file before relying on its contents.
- If the skill tells you to invoke another skill, request a `skill` action with that skill name.
- Use the Runtime SkillTool Registry in Repository Context to discover other loaded skills. Do not assume CCGS, art, audio, Godot, or any domain is built into the runtime core.
- Store cross-skill continuity such as global visual/audio style, asset inventory, and durable project decisions in runtime project memory through `project_memory_write` or `asset_register` when strict tools are available.
- {approval_policy}
- For implementation work, produce actual files, commands run, and evidence. Do not claim testing unless you ran a command or clearly label it as not run.
- QA mode requested by runtime: {qa_mode}

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

{agent.body}

## Repository Context

{context_bundle}
"""


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
    return _render_dynamic_context(rendered, project_root=project_root, plugin_root=plugin_root)


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
This pass exists because static skill conversion previously missed obvious gameplay bugs.

## Hard Requirements

- Do not fix bugs.
- Inspect the target project and identify how to run it.
- Run available smoke/gameplay tests if present.
- For Godot projects, look for `project.godot`, `scripts/`, `scenes/`, and any test scripts.
- Specifically check intermediate state updates, not only terminal outcomes:
  - movement input changes the player position
  - blocked movement does not count as a valid move unless the game explicitly says it should
  - empty-tile movement updates move count immediately
  - HUD/UI text updates every time the underlying state changes
  - coin collection updates coin count and move count
  - restart resets player, coins, win state, and HUD
  - win condition is reachable and not falsely triggered
- If you cannot run the game, return `VERDICT: BLOCKED` and explain exactly why.
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
