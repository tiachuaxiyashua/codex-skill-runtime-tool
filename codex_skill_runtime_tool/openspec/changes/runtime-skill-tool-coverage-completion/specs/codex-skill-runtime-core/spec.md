# Spec Delta: Skill Tool Coverage Completion

## ADDED Requirements

### Requirement: Runtime Provides A Persistent Task Tool Family

Runtime SHALL expose task create, get, list, output, update, and stop operations
over the same worker registry used by Task/Agent.

#### Scenario: Worker can be queried after creation

- **WHEN** a task is created
- **THEN** it is assigned a stable worker id
- **AND** TaskGet, TaskList, TaskOutput, TaskUpdate, and TaskStop operate on the same record.

### Requirement: Runtime Provides Deferred Tool Search

Runtime SHALL expose a bounded ToolSearch action over runtime tools, visible
skills, capabilities, and MCP server metadata.

#### Scenario: Model searches for a capability

- **WHEN** ToolSearch receives a query
- **THEN** it returns ranked results with kind, name, description, and metadata
- **AND** does not load full skill bodies.

### Requirement: Runtime Supports MCP Resources

Runtime SHALL support listing and reading MCP resources through configured MCP
transports.

#### Scenario: MCP resource is read

- **WHEN** a configured MCP server exposes resources/list and resources/read
- **THEN** runtime can list resources and read a resource URI
- **AND** returns BLOCKED with evidence when no server can satisfy the request.

### Requirement: Runtime Persists Elicitation State

Runtime SHALL persist MCP elicitation request and response records in the active
session.

#### Scenario: Elicitation pauses execution

- **WHEN** a skill requests elicitation without a response
- **THEN** runtime returns BLOCKED
- **AND** writes the request for later response.

### Requirement: Runtime Provides Terminal Capture Tools

Runtime SHALL expose PowerShell, terminal capture, and Python REPL tools with
persisted stdout/stderr evidence.

#### Scenario: Terminal capture runs

- **WHEN** a command completes
- **THEN** stdout/stderr and return code are returned
- **AND** full capture evidence is written under the session directory.

### Requirement: Runtime Provides Plan Mode Lifecycle

Runtime SHALL persist plan enter, exit, and verification state.

#### Scenario: Plan is verified

- **WHEN** plan verification evidence marks tasks complete
- **THEN** runtime records the plan as verified
- **AND** the current plan state is available in later prompt context.

### Requirement: Runtime Provides A Lightweight Browser State

Runtime SHALL expose a lightweight browser tool that can open a page, click a
link, search current page text, and report current state.

#### Scenario: Browser clicks a link

- **WHEN** a page contains links
- **THEN** runtime can open the selected link
- **AND** stores the current page state in the session.

### Requirement: Runtime Persists Structured Tool Transcript

Runtime SHALL write structured tool_use and tool_result records for every tool
action and include them in transcript replay.

#### Scenario: Session is resumed

- **WHEN** a prior session has tool transcript records
- **THEN** replay context includes bounded tool_use/tool_result records.
