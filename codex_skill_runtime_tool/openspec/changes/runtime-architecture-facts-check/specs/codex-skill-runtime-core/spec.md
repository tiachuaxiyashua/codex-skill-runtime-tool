# Spec Delta: Runtime Architecture Facts Check

## ADDED Requirements

### Requirement: Runtime Can Recompute Architecture Facts

The repository SHALL provide a local audit script that recomputes architecture facts from the
current workspace state.

#### Scenario: Audit script emits a snapshot

- **WHEN** the audit script runs from the repository root
- **THEN** it reports selected file line counts, tracked diff size, Python file totals, and skill
  skeleton summaries
- **AND** the output can be rendered as JSON or markdown.

### Requirement: Runtime Can Selftest Architecture Facts

The repository SHALL provide a selftest mode for the audit script that verifies the generated
snapshot shape and skill skeleton classification.

#### Scenario: Audit selftest passes

- **WHEN** the audit script is invoked with selftest mode
- **THEN** it verifies the repository root, line-count collection, diff parsing, and skill skeleton
  classification
- **AND** it exits successfully only when all checks pass.

### Requirement: Architecture Analysis Remains A Regenerable Snapshot

The human-readable architecture analysis SHALL describe its numbers as a regenerable snapshot rather
than a permanent truth.

#### Scenario: Snapshot wording is present

- **WHEN** the architecture analysis footer is read
- **THEN** it states that the figures can be regenerated from the audit script
- **AND** it does not present the figures as immutable facts.
