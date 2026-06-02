# Design: Runtime Skill Tool Coverage Completion

## General Contract

All new tools share these invariants:

- Tool names are normalized from Claude-style aliases to runtime snake_case.
- Results are persisted in the active session under `.skill-runtime/state/sessions`.
- Hook and allowed-tools decisions use the same ToolExecutor gate as existing tools.
- The strict JSON schema and prompt tool list expose the same model-visible tools.
- The implementation is generic and configured by loaded skills/plugins/MCP files.

## Mechanism Families

### Task Tool Family

`task_create`, `task_get`, `task_list`, `task_output`, `task_update`, and
`task_stop` map to the persistent `WorkerRegistry`. This preserves worker ids,
names, statuses, turns, latest output, and scratchpad paths across resume.

### ToolSearch

`tool_search` performs bounded lexical search across:

- runtime tool metadata,
- model-invocable skill listings,
- capability registry entries,
- configured MCP servers.

It is a deferred discovery mechanism; it does not load full skill bodies.

### MCP Resources And Elicitation

`list_mcp_resources` and `read_mcp_resource` call `resources/list` and
`resources/read` through the same stdio/HTTP/SSE/WebSocket MCP transports as
`mcp` tool calls. `mcp_elicitation` records request/respond/list state in the
session so a skill can pause for structured external input.

### Terminal And REPL

`powershell`, `terminal_capture`, and `repl` are evidence-producing local tools.
Terminal captures are written to `terminal-captures/` and summarized in tool
results.

### PlanMode

`plan_mode` and Claude-style aliases `EnterPlanMode`, `ExitPlanMode`, and
`VerifyPlanExecution` persist a `plan-mode.json` lifecycle. Current plan state
is injected into runtime context under token budgeting.

### WebBrowser

`web_browser` provides a small stateful browser over `open`, `click`, `find`,
and `current`. It uses standard HTTP fetch and HTML link extraction. It is not a
private UI clone.

### Structured Tool Transcript

Each ToolExecutor action appends `tool_use` and `tool_result` records to
`tool-transcript.jsonl`. Transcript replay includes those records so resume can
reconstruct prior tool boundaries.
