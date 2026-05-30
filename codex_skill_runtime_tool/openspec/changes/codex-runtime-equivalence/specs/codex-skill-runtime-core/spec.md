# Spec Delta: CCGS Codex Runtime

## ADDED Requirements

### Requirement: Runtime Reads Original CCGS Sources

Runtime SHALL read `.claude/skills`, `.claude/agents`, `.claude/docs`, and
`.claude/settings.json` as source inputs.

#### Scenario: Prototype skill routing

- **WHEN** `/prototype` is invoked
- **THEN** runtime loads `.claude/skills/prototype/SKILL.md`
- **AND** runtime routes the main agent to `.claude/agents/prototyper.md`.

#### Scenario: Team QA skill routing

- **WHEN** `/team-qa` is invoked
- **THEN** runtime loads `.claude/skills/team-qa/SKILL.md`
- **AND** runtime routes the main agent to `.claude/agents/qa-lead.md`.

### Requirement: Runtime Preserves Original Claude Files

Runtime SHALL treat the original `.claude/` tree as read-only.

#### Scenario: Tool write into `.claude`

- **WHEN** Codex requests `write_file` or `edit_file` for a path under `.claude`
- **THEN** runtime rejects the action
- **AND** records an ERROR tool result
- **AND** leaves `git diff -- .claude` empty.

### Requirement: Runtime Builds Workflow Plans

Runtime SHALL write a workflow plan for each slash command.

#### Scenario: Prototype engine workflow

- **WHEN** `/prototype ... --path engine` is invoked
- **THEN** runtime writes `workflow-plan.json`
- **AND** the plan includes load-skill-agent, resolve-prototype-question, strict-action-loop,
  required-qa, and evidence-finalization phases.

### Requirement: Runtime Executes Structured Tool Actions

Runtime SHALL provide a strict action loop where Codex requests structured actions and runtime
executes file actions, Bash, Task/Agent, AskUserQuestion, TodoWrite, Skill, WebFetch,
WebSearch, Godot smoke, and MCP actions.

#### Scenario: Strict read action

- **WHEN** Codex returns an `action_required` response with `read_file`
- **THEN** runtime reads the requested file
- **AND** writes a tool result under `.codex-skill-runtime/sessions/.../tools/`
- **AND** returns the observation to the next Codex step.

#### Scenario: Strict final response

- **WHEN** Codex returns `status: final`
- **THEN** runtime stops the action loop
- **AND** writes `strict-result.json`
- **AND** reports a PASS strict gate unless a tool/gate failed.

#### Scenario: Stdio MCP action

- **WHEN** Codex requests `mcp__server__tool`
- **AND** `.mcp.json` or plugin `mcpServers` defines a matching stdio server
- **THEN** runtime initializes the MCP server over stdio
- **AND** calls `tools/call`
- **AND** records the MCP result as tool evidence.

#### Scenario: Remote MCP action

- **WHEN** Codex requests a tool backed by SSE, HTTP, WebSocket, or URL MCP configuration
- **THEN** runtime selects the configured remote transport
- **AND** sends MCP initialize and initialized messages
- **AND** calls `tools/call`
- **AND** records the remote MCP result as tool evidence.

#### Scenario: Remote MCP authentication boundary

- **WHEN** a remote MCP server requires authentication
- **AND** the configuration does not provide usable `headers`, `headersHelper`, or token values
- **THEN** runtime returns BLOCKED with an explicit authentication setup reason
- **AND** does not claim the tool call succeeded.

### Requirement: Runtime Supports Schema Fallback

Runtime SHALL prefer `codex exec --output-schema` for strict action-loop responses and SHALL
fallback to prompt-only JSON when the provider fails schema execution.

#### Scenario: Provider schema failure

- **WHEN** the schema-constrained Codex run exits non-zero without valid JSON
- **THEN** runtime retries with prompt-only JSON instructions
- **AND** continues only if valid final JSON is returned.

### Requirement: Hooks Are Runtime-Enforced

Runtime SHALL dispatch configured hooks for session, subagent, and runtime-owned tool events.

#### Scenario: Subagent task

- **WHEN** a Task action starts `qa-tester`
- **THEN** SubagentStart hook runs with `agent_type: qa-tester`
- **AND** SubagentStop hook runs after the subagent returns.

#### Scenario: Bash action

- **WHEN** runtime executes a Bash action
- **THEN** it checks `.claude/settings.json` deny rules
- **AND** dispatches PreToolUse and PostToolUse hooks for Bash.

#### Scenario: Windows shell hook

- **WHEN** a configured `.sh` hook contains CRLF line endings on Windows
- **THEN** runtime executes a session-local LF shim
- **AND** does not rewrite the original hook file.

#### Scenario: Plugin skill hook

- **WHEN** `hooks/hooks.json` declares a matching SessionStart hook with `type: skill`
- **THEN** runtime records the hook
- **AND** injects the referenced skill body into prompt context when it exists.

### Requirement: Task Actions Spawn Independent Agents

Runtime SHALL simulate Claude Code Task delegation by starting independent Codex sessions with
the original `.claude/agents/<agent>.md` definition.

#### Scenario: Parsed task request

- **WHEN** the main agent outputs `RUNTIME_TASK_REQUEST: agent=qa-tester; purpose=...; inputs=...`
- **THEN** runtime runs the `qa-tester` agent as a subagent
- **AND** records the subagent prompt and output in the session.

#### Scenario: Concurrent task actions

- **WHEN** strict mode receives multiple Task actions in one step
- **THEN** runtime MAY execute them concurrently
- **AND** preserves each result in the session evidence.

### Requirement: AskUserQuestion Is A Runtime Pause

Runtime SHALL treat AskUserQuestion as a structured workflow pause.

#### Scenario: No automation approval

- **WHEN** Codex requests `ask_user_question` and `--assume-yes` is not set
- **THEN** runtime returns BLOCKED
- **AND** records the question and options.

#### Scenario: Assume yes automation

- **WHEN** Codex requests `ask_user_question` and `--assume-yes` is set
- **THEN** runtime records the question
- **AND** selects the first option or default answer
- **AND** continues the workflow.

### Requirement: QA Gates Require Evidence

Runtime SHALL block weak QA output.

#### Scenario: Weak QA pass

- **WHEN** QA returns `VERDICT: PASS` without `EVIDENCE MATRIX`
- **THEN** runtime gate status is `BLOCKED`.

#### Scenario: Missing verdict

- **WHEN** QA output does not contain a `VERDICT` line
- **THEN** runtime gate status is `BLOCKED`.

### Requirement: Godot Tests Are Actually Run

Runtime SHALL run Godot headless checks when a Godot project is supplied.

#### Scenario: Tilemap project

- **WHEN** runtime runs `godot-smoke` on the tilemap fixture
- **THEN** it executes Godot headless
- **AND** executes `scripts/gameplay_test.gd` if present
- **AND** returns PASS only when the process exits zero.

### Requirement: Engine Prototypes Require QA

Runtime SHALL force QA for Godot or engine prototype workflows unless QA is explicitly disabled.

#### Scenario: Prototype engine path

- **WHEN** `/prototype` is invoked with `--path engine`
- **THEN** runtime runs `qa-tester` after primary implementation
- **AND** evaluates the QA output with the QA gate.

### Requirement: Runtime Records Evidence

Runtime SHALL store prompts, command shapes, stdout/stderr, hook results, tool results, workflow
plans, gate decisions, and Godot outputs in `.codex-skill-runtime/sessions/`.

#### Scenario: Complete live selftest

- **WHEN** full live selftest completes
- **THEN** strict, Godot, QA, and hook evidence are present under `.codex-skill-runtime/sessions/`
- **AND** the test summary reports zero failures.

### Requirement: Runtime Maintains Compacted Session Memory

Runtime SHALL write deterministic session summaries and a bounded session index so later prompts
can receive continuity context without relying on Claude Code private cache internals.

#### Scenario: Session summary

- **WHEN** a runtime session finishes
- **THEN** runtime writes `.codex-skill-runtime/sessions/<session>/summary.json`
- **AND** updates `.codex-skill-runtime/sessions-index.json`
- **AND** includes recent events, recent tool summaries, command metadata, gate outcomes, and notes.

#### Scenario: Prompt memory injection

- **WHEN** a later runtime session builds a prompt context bundle
- **THEN** runtime injects a bounded "Runtime Memory / Compacted Session Context" section
- **AND** excludes the current session from that memory context.

### Requirement: Selftest Proves Runtime Equivalence

Runtime SHALL include a selftest command that exercises the critical CCGS equivalence mechanisms.

#### Scenario: Full live selftest

- **WHEN** selftest is run with `--live-strict-target` and `--live-qa-target`
- **THEN** loader discovery, frontmatter routing, Task parsing, QA gates, dry-run contract,
  strict dry-run, tool executor, hook shim, Godot, live strict, live QA, and `.claude` clean
  checks all pass.

### Requirement: Runtime Loads Common GitHub Claude Skill Layouts

Runtime SHALL load skill repos that use `.claude/skills`, repo-root `skills/`, or direct
`<skill>/SKILL.md` directories.

#### Scenario: External repository without `.claude/agents`

- **WHEN** an external skill repo has skills but no agent directory
- **THEN** runtime lists the skills
- **AND** uses a synthetic main agent when the selected skill has no separate agent file.

#### Scenario: Supporting files and skill invocation

- **WHEN** an external skill references sibling supporting files or another Skill tool call
- **THEN** runtime exposes supporting file paths
- **AND** the `skill` action can load another skill from the same runtime root.

### Requirement: Runtime Loads Commands And Plugin Components

Runtime SHALL discover Claude command markdown and plugin default component directories in
addition to skill directories.

#### Scenario: Programming repository command

- **WHEN** a repository exposes `commands/<name>.md`
- **THEN** runtime can invoke `/<name>` through the same strict command entry point
- **AND** command body and frontmatter are rendered as the selected workflow source.

#### Scenario: Plugin repository

- **WHEN** runtime finds `.claude-plugin/plugin.json`
- **THEN** it discovers that plugin's default `commands/`, `skills/`, `agents/`, and
  `hooks/hooks.json` components
- **AND** plugin namespaced command or skill references can be resolved.

### Requirement: Runtime Renders Claude Skill Prompt Preprocessing

Runtime SHALL expand supported prompt preprocessing used by Claude skills and commands.

#### Scenario: Dynamic command context

- **WHEN** skill or command body contains `` !`command` ``
- **THEN** runtime executes the command before prompt construction
- **AND** injects command output or an explicit diagnostic into the prompt.

#### Scenario: Plugin root environment

- **WHEN** plugin prompt or hook content references `${CLAUDE_PLUGIN_ROOT}`
- **THEN** runtime substitutes the discovered absolute plugin root before execution.

### Requirement: Runtime Models Skill Fork And Permissions

Runtime SHALL approximate observable Claude skill fork and permission behavior in strict mode.

#### Scenario: Forked Skill action

- **WHEN** a `skill` action loads a skill with `context: fork`
- **THEN** runtime starts an independent Codex task session for that skill
- **AND** returns the fork result to the caller as a tool observation.

#### Scenario: Skill allowed tools are preapproval

- **WHEN** a skill lists `allowed-tools`
- **THEN** runtime records them as preapproved tool patterns
- **AND** does not reject a tool solely because it is absent from that list.

#### Scenario: Explicit permission rule

- **WHEN** settings contain matching `deny`, `ask`, or `allow` permission rules
- **THEN** runtime applies the explicit decision before executing the runtime-owned action.

### Requirement: Runtime Supports Claude Command Preprocessing

Runtime SHALL preprocess common Claude command and skill body constructs before handing the
prompt to Codex.

#### Scenario: Positional arguments

- **WHEN** a command body contains `$1`, `$2`, or `$ARGUMENTS[index]`
- **THEN** runtime substitutes the corresponding invocation argument before prompt execution
- **AND** leaves `$ARGUMENTS` as the full invocation argument string.

#### Scenario: File references

- **WHEN** a command body contains `@path`, `@$1`, or `@${CLAUDE_PLUGIN_ROOT}/path`
- **THEN** runtime resolves the referenced project or plugin file
- **AND** injects bounded file content with a clear source marker
- **AND** injects an explicit missing-file diagnostic when the file cannot be resolved.

### Requirement: Plugin Manifest Custom Paths Supplement Defaults

Runtime SHALL treat plugin manifest component paths as additions to default plugin component
directories, not replacements.

#### Scenario: Custom command and default command directories

- **WHEN** a plugin manifest defines `"commands": "custom-commands"`
- **THEN** runtime discovers commands in both `commands/` and `custom-commands/`.

#### Scenario: Custom hook path and default hook path

- **WHEN** a plugin manifest defines `"hooks": "config/hooks.json"`
- **THEN** runtime loads both `hooks/hooks.json` and `config/hooks.json` when they exist.

#### Scenario: Plugin MCP sources

- **WHEN** a plugin contains `.mcp.json`
- **AND** the manifest defines `mcpServers` inline or as a path
- **THEN** runtime discovers both MCP server sources
- **AND** can bridge stdio, HTTP, SSE, and WebSocket MCP transports when the server is reachable and authenticated.

### Requirement: Hook Outputs Are Enforced

Runtime SHALL interpret command and prompt hook outputs using the public Claude Code hook
contract where that contract is observable outside the Claude Code client.

#### Scenario: PreToolUse permission decision

- **WHEN** a PreToolUse hook returns `hookSpecificOutput.permissionDecision: deny`
- **THEN** runtime blocks the runtime-owned tool action before execution.

#### Scenario: Hook updated input

- **WHEN** a PreToolUse hook returns `hookSpecificOutput.updatedInput`
- **THEN** runtime applies the updated tool input
- **AND** rechecks explicit permission rules before executing the tool.

#### Scenario: Hook exit code 2

- **WHEN** a hook exits with code `2`
- **THEN** runtime treats the hook as blocking
- **AND** records the hook output as the block reason when present.

#### Scenario: Prompt hook decision

- **WHEN** a supported hook uses `type: prompt`
- **THEN** runtime invokes a Codex-backed prompt hook runner
- **AND** enforces `decision: block` or `continue: false` returned by that hook.

#### Scenario: User prompt and session end events

- **WHEN** a runtime command starts
- **THEN** runtime dispatches `UserPromptSubmit` with `user_prompt`
- **AND** dispatches `SessionEnd` when the runtime command finishes.

### Requirement: Runtime Builds Claude-Code-Like System Prompt Layers

Runtime SHALL build a clean-room layered system prompt contract that preserves observable
Claude Code execution effects without copying private prompt text.

#### Scenario: Output style and custom prompt injection

- **WHEN** runtime receives `--output-style`, `--system-prompt`, or `--append-system-prompt`
- **THEN** it resolves project/user output-style files and prompt text or `@file` values
- **AND** injects those sections into the prompt passed to Codex
- **AND** caches stable prompt sections by metadata/style/file cache keys.

#### Scenario: Coordinator profile

- **WHEN** coordinator mode is enabled by environment
- **THEN** runtime includes coordinator/scratchpad instructions in the prompt profile
- **AND** preserves the scratchpad directory path as observable context.

### Requirement: Runtime Preserves Reference Prompt Behavioral Effects

Runtime SHALL encode behaviorally important prompt effects from the reference client as clean-room
runtime instructions, without dynamically loading or copying private prompt text.

#### Scenario: Engineering behavior contracts

- **WHEN** runtime builds a system prompt for a skill or agent
- **THEN** it includes clean-room contracts for read-before-edit, prompt-injection detection,
  denied-tool retry handling, hook feedback handling, risk confirmation, faithful outcome
  reporting, and verify-before-complete behavior.

#### Scenario: Tool and delegation behavior contracts

- **WHEN** runtime builds a system prompt for a skill or agent
- **THEN** it includes clean-room contracts for dedicated-tool preference, parallel independent
  tool calls, tool evidence, visible skill discovery, Task/Agent delegation ownership,
  AskUserQuestion pauses, and MCP instruction priority.

#### Scenario: Context lifecycle behavior contracts

- **WHEN** runtime builds a system prompt for a skill or agent
- **THEN** it includes clean-room contracts for environment awareness, scratchpad temporary files,
  compaction fact preservation, resume verification, explicit memory stores, output style, and
  language preference.

#### Scenario: Configured MCP instructions

- **WHEN** configured MCP servers provide `instructions` or `instruction` fields in project or
  plugin MCP configuration
- **THEN** runtime injects a bounded "Runtime MCP Server Instructions" context section
- **AND** treats those instructions as server-specific tool guidance below user, skill, agent,
  and runtime safety instructions.

### Requirement: Runtime Replays Recorded Transcripts

Runtime SHALL persist and replay runtime transcript evidence for resume flows.

#### Scenario: Transcript event recording

- **WHEN** Codex runs, tools execute, hooks fire, or session state changes
- **THEN** runtime writes both event evidence and transcript JSONL entries under
  `.codex-skill-runtime/sessions/<session>/`
- **AND** records read-state and large-result replacement manifests when applicable.

#### Scenario: Resume command

- **WHEN** `core_cli.py resume <session>` is invoked
- **THEN** runtime reconstructs bounded context from transcript events, summary, read-state,
  and content replacement manifests
- **AND** passes that replay context to Codex before the new user instruction.

### Requirement: Runtime Supports MCP OAuth Token Lifecycle

Runtime SHALL support execution-level MCP OAuth/auth token handling for remote MCP servers,
excluding the private Claude Code marketplace lifecycle.

#### Scenario: Auth command token storage

- **WHEN** an HTTP/SSE MCP server config contains `authCommand`, `oauthRefreshCommand`, or
  `tokenCommand`
- **THEN** runtime executes the command with Claude-compatible MCP environment variables
- **AND** stores the returned access token in a secure-token abstraction
- **AND** injects the stored Authorization header on subsequent MCP calls.

#### Scenario: Authenticate pseudo-tool

- **WHEN** Codex calls `mcp__<server>__authenticate`
- **THEN** runtime returns either an authenticated status, an authorization URL, or an explicit
  unsupported/error result
- **AND** does not claim remote MCP tools are available until credentials exist.

#### Scenario: OAuth authorization code completion

- **WHEN** a pending OAuth flow is completed with a callback URL or authorization code
- **THEN** runtime exchanges the code for tokens
- **AND** persists those tokens for future remote MCP calls.

#### Scenario: Token refresh and retry

- **WHEN** a stored token is expired or a remote MCP call receives HTTP 401/403
- **THEN** runtime attempts configured refresh/auth commands
- **AND** retries the MCP initialize or tool call when a fresh token is obtained
- **AND** returns BLOCKED when no usable authorization path exists.

#### Scenario: SSE and WebSocket authentication retry

- **WHEN** an SSE or WebSocket MCP connection receives an authentication failure
- **THEN** runtime attempts stored token refresh or configured auth/token commands
- **AND** retries the remote connection with the refreshed Authorization header when available.

#### Scenario: OAuth refresh token

- **WHEN** a stored MCP token is expired and contains a refresh token
- **AND** the MCP configuration exposes an OAuth token endpoint
- **THEN** runtime performs a standard refresh-token exchange
- **AND** stores the refreshed access token for later calls.

### Requirement: Runtime Microcompacts Old Tool Observations

Runtime SHALL prevent long strict action-loop sessions from repeatedly injecting bulky old tool
results while preserving full evidence on disk.

#### Scenario: Strict observations exceed context budget

- **WHEN** accumulated strict-loop observations exceed the runtime microcompact threshold
- **THEN** runtime persists old bulky tool observations under the session directory
- **AND** replaces old prompt-visible data with a stable cleared-result marker and file path
- **AND** keeps recent observations intact for immediate reasoning continuity.

### Requirement: Runtime Exposes Bridge Voice And IDE Execution Context

Runtime SHALL provide local execution-equivalent Bridge, Voice, and IDE mechanisms that can be
used by skills through strict actions and injected context.

#### Scenario: Bridge lifecycle

- **WHEN** a skill requests `bridge` actions
- **THEN** runtime supports environment registration, work enqueue/poll/ack, heartbeat,
  session-event writing, archive, and reconnect pointers
- **AND** injects reconnect context into later prompt bundles when present.

#### Scenario: Voice lifecycle

- **WHEN** a skill requests `voice` actions
- **THEN** runtime supports start, transcript append, finalize, load, and latest transcript context
- **AND** injects finalized transcript text into later prompt bundles.

#### Scenario: IDE/LSP context

- **WHEN** a skill requests `ide` actions
- **THEN** runtime supports active selection, diagnostics, bounded context injection, and
  configurable LSP command execution
- **AND** records IDE tool evidence under the active runtime session.

### Requirement: Marketplace Lifecycle Remains Out Of Scope

Runtime SHALL NOT implement Claude Code marketplace browse/install/update lifecycle in this
change.

#### Scenario: Marketplace exclusion

- **WHEN** evaluating parity for this change
- **THEN** missing marketplace UI/install lifecycle does not fail the change
- **AND** all other execution-effect mechanisms above must still be validated by selftest or
  explicit runtime evidence.
