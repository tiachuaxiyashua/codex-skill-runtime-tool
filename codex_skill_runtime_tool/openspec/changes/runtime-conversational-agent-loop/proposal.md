# Proposal: runtime-conversational-agent-loop

## Background

The runtime already supports many Claude Code compatible mechanisms: skill and slash-command discovery, agents, hooks, MCP, structured tool execution, session state, memory, artifacts, and UI monitoring.

The remaining high-impact gap is that the natural-language chat path is not equivalent to the slash-command path. Slash commands can enter the strict runtime tool loop, but chat currently executes a single Codex prompt. This makes the product feel like a task launcher rather than a Claude Code style interactive agent where the model can decide when to call skills, use tools, ask the user, continue, or stop.

This change makes natural-language interaction the primary, generic entry point while preserving slash commands as explicit invocations.

## Goals

- Route natural-language chat through the same runtime-owned action loop semantics as slash commands.
- Allow the model to discover and invoke loaded skills automatically from chat.
- Preserve explicit slash-command execution, nested skill invocation, Task/Agent delegation, hooks, MCP, memory, and artifacts.
- Make `AskUserQuestion` the authoritative pause lifecycle; ordinary question-looking text must not pause execution by itself.
- Keep `max_steps` as a safety budget, not the semantic definition of completion.
- Separate user-facing conversation from runtime process monitoring in the UI without hiding evidence.
- Add regression tests that verify the generic mechanism rather than one game or one skill.
- Keep the runtime generic and free of game, CCGS, Godot, Forge, ComfyUI, absolute-path, or provider-specific hardcoding.

## Non-Goals

- Recreate Claude Code's private UI, private system prompt text, marketplace lifecycle, or hidden model-side cache.
- Build a game-specific runtime.
- Make external MCP services pass without actual configured credentials or servers.

## Acceptance

This change is complete only when all of these are true:

- `/api/chat` and CLI `chat` can run a natural-language request through a structured action loop.
- A chat request can cause a model-selected `skill` action and then continue with the loaded skill instructions.
- `AskUserQuestion` creates a durable pending question and pauses; answering resumes from transcript context.
- A normal assistant sentence containing a question mark does not create a pending question unless the structured action is `ask_user_question`.
- UI middle conversation shows only user messages, real assistant messages, and explicit questions/answers.
- Visible reasoning summaries, model stream events, jobs, tool calls/results, hooks, task tree, agents, artifacts, memory, and errors remain available in process/status panes.
- UI right-side process monitor still shows jobs, model start/finish, tool calls/results, hooks, task tree, agents, artifacts, memory, and errors.
- Stop, delete history, resume, and answer controls continue to target the underlying job/session state.
- Tests cover chat skill selection, assistant brief display, question pause, non-pausing question text, and UI row classification.
