# Design: runtime-conversational-agent-loop

## Problem

The runtime currently has two different execution semantics:

- Slash command path: can use `StrictActionLoop`, `ToolExecutor`, `Skill`, `Task`, `AskUserQuestion`, hooks, memory, and structured evidence.
- Chat path: calls Codex once through `chat_turn()` and records the last message.

This split prevents the UI from behaving like Claude Code/Codex. A human expects to type a goal, then let the agent decide whether to answer, ask, use a skill, call tools, or continue. The runtime can already do most of this, but not from the primary chat entry point.

## Runtime Model

Add a generic conversational strict action loop.

The loop reuses the same JSON action contract as slash commands:

- `status: action_required`: execute one or more runtime-owned actions.
- `status: final`: stop because the workflow has enough evidence.
- `status: blocked`: stop because prerequisites are missing.

The loop has a chat-oriented prompt:

- includes the user message;
- includes `CLAUDE.md` / `AGENTS.md` / project context;
- includes the model-visible skill registry under the same budget rules;
- includes relevant session memory, memdir, transcript, compact state, MCP, bridge, voice, IDE context when available;
- instructs the model to call `skill` before improvising when a visible skill matches;
- instructs the model to use `brief` / `SendUserMessage` for human-visible progress summaries;
- instructs the model to use `ask_user_question` only when it truly needs the user;
- instructs the model to continue autonomously when the user gave enough direction or enabled assume-yes.

This is not a Claude Code private prompt copy. It is a clean-room behavior contract for public skill execution effects.

## UI Model

The UI has three distinct streams:

- Conversation stream: user messages, real model messages, explicit questions, and answers.
- Process stream: jobs, model start/finish, visible reasoning summaries, tool calls/results, hooks, task tree, worker state, MCP, memory, artifacts, errors.
- Project/file/memory panes: current project, file tree, artifacts, and durable memory.

The middle conversation pane must not render job cards, tool cards, or summary cards as assistant dialogue. Those belong to the process stream. Runtime-generated status text must not pretend to be assistant output.

## Question Lifecycle

Only structured `ask_user_question` creates a pending question.

The frontend may visually highlight a model question if it appears in an assistant message, but it must not set waiting state or force answer mode unless the backend exposes a durable pending question.

Answering a question records `pending-question-answer.json`, then resumes with the answer. When the prior session was a conversational session, the next conversational action loop must receive reconstructed prior context: transcript replay, pending-question answer context, rolling session memory, tool transcript, worker scratchpads, loaded skill records, and durable memory snippets. This preserves execution continuity after a user clarification instead of starting an isolated fresh chat.

## Step Budget

`max_steps` is a safety guard. It prevents infinite loops but does not define success. A normal successful run ends only when the model returns `status: final`, or when a tool/action creates an intentional pause.

When the budget is exhausted, the runtime reports a budget exhaustion blocker with evidence. It must not claim the task is complete.

## Genericity

All behavior must be expressed through:

- tool actions;
- skill and command metadata;
- loader discovery;
- session state;
- memory;
- hooks;
- MCP adapters;
- UI event classification.

No implementation may special-case CCGS, Godot, Forge, ComfyUI, any local absolute path, a particular skill name, a particular game, or a particular API provider.

## Verification Strategy

Use local fake Codex executables for deterministic runtime tests:

- chat request returns a `skill` action, then a `brief`, then `final`;
- chat request returns `ask_user_question` and pauses;
- chat request returns `final` text containing a question mark and does not pause;
- slash command path remains compatible.

Use UI classification tests or smoke checks to verify:

- process rows do not enter the middle conversation;
- brief/assistant messages do enter the middle conversation;
- question rows appear only from durable pending question or explicit question events.
