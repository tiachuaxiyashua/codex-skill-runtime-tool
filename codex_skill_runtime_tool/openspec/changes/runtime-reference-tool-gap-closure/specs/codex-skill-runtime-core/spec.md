# Spec Delta: Reference Tool Gap Closure

## ADDED Requirements

### Requirement: Runtime Separates Task Lists From Background Agents

Runtime SHALL use `TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate` for persisted
task-list state, and SHALL use `Agent`/legacy `Task` plus `TaskOutput`,
`TaskStop`, and `SendMessage` for background worker state.

#### Scenario: Task list is updated

- **WHEN** `TaskCreate` creates a task-list item
- **THEN** `TaskGet` and `TaskList` can inspect it
- **AND** `TaskUpdate` can change status, owner, dependencies, and metadata.

#### Scenario: Background agent output is polled

- **WHEN** `Agent` starts a background worker
- **THEN** `TaskOutput` can inspect the worker before and after it completes
- **AND** `TaskOutput` can either block until completion or return a non-blocking status
- **AND** the worker record is persisted.

### Requirement: Runtime Exposes Remaining Reference Tool Names

Runtime SHALL expose generic implementations or lifecycle records for common
reference tool names that affect skill execution.

#### Scenario: Skill requests a reference tool

- **WHEN** a strict action uses a supported reference-style tool name
- **THEN** schema validation accepts it
- **AND** ToolExecutor normalizes it to a generic runtime handler.

### Requirement: Runtime Edits Notebooks Structurally

Runtime SHALL edit `.ipynb` files through JSON cell operations rather than text
replacement.

#### Scenario: Notebook cell is inserted

- **WHEN** `NotebookEdit` inserts a cell
- **THEN** the notebook remains valid JSON
- **AND** the session records the notebook as an artifact.

### Requirement: Runtime Exposes MCP OAuth And Dynamic MCP Actions

Runtime SHALL expose MCP OAuth as a model-visible action and SHALL accept
configured dynamic `mcp__<server>__<tool>` names in strict action schema.

#### Scenario: MCP server requires OAuth

- **WHEN** a skill searches for or invokes MCP authentication
- **THEN** ToolSearch exposes an `mcp__<server>__authenticate` pseudo-tool
- **AND** `McpAuth` can start or complete the configured OAuth flow
- **AND** dynamic MCP names continue through normal permission and hook gates.

### Requirement: Runtime Persists Workflow, Cron, Team, Brief, And Review State

Runtime SHALL persist these non-file lifecycle records under the active session
or runtime state.

#### Scenario: Workflow state is updated

- **WHEN** a workflow operation writes state
- **THEN** later monitor/read operations can retrieve that state.

#### Scenario: Cron prompt fires while runtime is alive

- **WHEN** a due cron record reaches its fire time during the active runtime process
- **THEN** the prompt is appended to the session cron fire queue
- **AND** monitor state can retrieve the fired prompt.

### Requirement: Runtime Tool Search Matches Executable Runtime Tools

Runtime SHALL expose every generally executable runtime-owned tool through
ToolSearch so a model can discover the same tools it can invoke through strict
actions.

#### Scenario: Model searches for a supported runtime tool

- **WHEN** the model searches for question, todo, memory, web, bridge, voice,
  IDE, or worker tools
- **THEN** ToolSearch returns matching runtime-tool records
- **AND** ToolSearch does not include full skill bodies in search results.

### Requirement: Runtime Executes Bash Hook Shims Reliably On Windows

Runtime SHALL normalize bash `.sh` hooks to LF shims and deliver hook payloads
through a stable stdin mechanism.

#### Scenario: Bash hook reads JSON from stdin

- **WHEN** a hook script reads its payload from stdin
- **THEN** the runtime provides the payload through a session payload file
- **AND** Windows bash shim execution receives a small transport grace period
  for shell startup overhead
- **AND** hook timeouts are returned as hook results instead of uncaught runtime
  exceptions.
