# Spec Delta: Reference Mechanism Completion

## ADDED Requirements

### Requirement: Runtime Resolves QA Agents Generically

Runtime SHALL select required QA agents through config, frontmatter, or capability metadata before
using a fallback.

#### Scenario: Frontmatter QA agent is used

- **WHEN** a skill declares QA agent metadata
- **THEN** required QA uses that agent
- **AND** session evidence records the resolver source.

### Requirement: Runtime Supports Side-Query Memory Selection

Runtime SHALL support a side-query selector for relevant memory files and fall back safely when it
is unavailable.

#### Scenario: Side-query selects memory

- **WHEN** a selector returns valid filenames
- **THEN** memory recall uses those filenames
- **AND** ignores filenames not present in the bounded manifest.

### Requirement: Runtime Records Background Memory Jobs

Runtime SHALL record durable memory extraction and consolidation as job lifecycle records.

#### Scenario: Memory extraction completes

- **WHEN** a session completes
- **THEN** an extraction job record is written with status and output paths.

### Requirement: Runtime Records Compact State

Runtime SHALL record token budget and session-memory compact state when context approaches limits.

#### Scenario: Context crosses autocompact threshold

- **WHEN** estimated context exceeds the configured threshold
- **THEN** runtime writes compact state evidence and includes compact guidance.

### Requirement: Runtime Persists API Message Transcript

Runtime SHALL persist prompt and assistant exchanges as API-like message records.

#### Scenario: Resume includes API messages

- **WHEN** a prior session has API message records
- **THEN** resume context includes bounded API message transcript entries.

### Requirement: Runtime Persists Worker Scratchpads

Runtime SHALL create and persist scratchpad directories for worker records.

#### Scenario: Worker scratchpad is resumed

- **WHEN** a prior worker has scratchpad files
- **THEN** transcript replay lists the worker scratchpad and bounded file previews.
