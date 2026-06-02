# Spec Delta: Generic Long-Context Runtime Memory

## ADDED Requirements

### Requirement: Runtime Maintains Session Memory

Runtime SHALL write bounded `session-memory/summary.md` and `session-memory/state.json` files
for sessions that execute tools or record material events.

#### Scenario: Strict loop updates session memory

- **WHEN** a strict action loop records tool observations
- **THEN** runtime updates `session-memory/summary.md`
- **AND** later strict steps can include that memory in prompt context.

### Requirement: Runtime Maintains Durable Memory Directory

Runtime SHALL maintain a runtime-owned long-term memory directory with an index file and topic
files.

#### Scenario: Session completion extracts durable memory

- **WHEN** a session summary is recorded
- **THEN** runtime writes or updates a topic memory file
- **AND** `MEMORY.md` contains a pointer to that topic file.

### Requirement: Runtime Recalls Relevant Memories

Runtime SHALL surface a bounded set of relevant memory topic files based on current task text.

#### Scenario: Relevant memory is injected

- **WHEN** prompt context is built for a task matching a topic file
- **THEN** runtime includes at most five relevant topic files
- **AND** each surfaced file is bounded by line and byte limits.

### Requirement: Runtime Reports Token Budget

Runtime SHALL estimate prompt size and record a budget report for strict action prompts.

#### Scenario: Budget report is present

- **WHEN** strict action prompt is built
- **THEN** runtime includes or records an estimated token budget report
- **AND** warns when estimated usage is near the configured context window.

### Requirement: Runtime Enriches Resume Context

Runtime SHALL include session memory, worker records, pending questions, and content replacement
manifests when rebuilding resume context.

#### Scenario: Resume shows worker records

- **WHEN** a session has `workers.json`
- **THEN** transcript replay includes worker ids, agents, status, and latest output previews.
