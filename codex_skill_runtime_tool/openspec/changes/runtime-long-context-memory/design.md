# Design: Generic Long-Context Runtime Memory

## Contracts

### Session Memory

Each runtime session can maintain `session-memory/summary.md` plus `session-memory/state.json`.
The markdown file is a bounded working note with:

- current state
- task specification
- files and artifacts
- tools and results
- errors and corrections
- worklog

The runtime may update this file deterministically from events and observations without model
calls. The file is injected into later strict-loop steps and resume context.

### Durable Memory Directory

Runtime-owned long-term memory lives under the configured runtime state root:

```text
memory/
  MEMORY.md
  topics/
    <topic>.md
  consolidation.json
```

`MEMORY.md` is an index. Topic files contain frontmatter-like metadata and concise content.
This mirrors the reference shape without copying private prompts.

### Relevant Memory Recall

Before prompt assembly, the runtime scores topic files against the current command, arguments,
agent, and user prompt. It surfaces at most five bounded files, capped by line and byte budgets.
Selection is generic lexical scoring, not a skill-specific branch.

### Extraction And Consolidation

At session completion, the runtime extracts durable facts from session summary, session memory,
artifacts, gates, and tool evidence into a topic file. A gated consolidation step updates
`MEMORY.md` and a rollup file when enough time or enough sessions have elapsed.

### Token Budget

The runtime estimates prompt tokens with a conservative character heuristic, records budget
reports, and injects warnings when context approaches configured limits. This does not claim
provider cache equivalence.

### Worker Persistence

Worker records are written to `workers.json` in the session directory. Resume context includes
the records so later continuation can reason about prior worker outputs even if the original
process is gone.

## Hardcoding Boundary

The implementation must not contain branches for CCGS, Godot, Forge, ComfyUI, game projects,
or local absolute paths. Domain-specific workflows should enter through skill repositories,
plugins, capabilities, or environment configuration.
