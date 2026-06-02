# Change: Generic Long-Context Runtime Memory

## Why

The runtime already supports generic skill loading, strict actions, MCP, plugin manifests,
questions, UI state, and evidence files. Its long-running workflow continuity is still weaker
than the reference project because session summaries, explicit project memory, and event replay
do not form a complete context lifecycle.

Complex skills can span many turns, workers, large tool outputs, interrupted sessions, and
recurring project decisions. The runtime needs generic memory and compaction primitives that do
not depend on CCGS, Godot, art, audio, or any other single skill repository.

## What Changes

- Add deterministic session memory files that are updated during strict action loops and at
  session completion.
- Add a runtime-owned memory directory with `MEMORY.md` as an index and topic files as durable
  memory records.
- Add relevant-memory recall that selects bounded topic files from generic metadata and query
  scoring before prompt assembly.
- Add post-session durable memory extraction and gated memory consolidation inspired by the
  reference project's extract/dream lifecycle.
- Add token-budget context and stronger prompt compaction signals so long tool observations do
  not silently dominate model context.
- Add API-safe resume context improvements: session memory, durable memory, content replacements,
  pending questions, and worker records.
- Add persistent worker records for SendMessage/TaskStop continuity evidence.
- Add selftests proving these mechanisms do not rely on CCGS, Godot, Forge, ComfyUI, fixed drive
  letters, or one local project path.

## Non-Goals

- Do not clone private Claude Code prompts or UI.
- Do not implement marketplace installation lifecycle.
- Do not make the runtime game-specific.
- Do not claim provider-level prompt cache equivalence; keep provider cache behavior as a
  performance difference unless explicitly verified.

## Success Criteria

- OpenSpec change documents generic long-context requirements.
- Python compile succeeds.
- Runtime selftest includes generic contracts for session memory, memdir recall, consolidation,
  token budget context, resume enrichment, and worker persistence.
- Hardcoding scan finds no new fixed drive paths, CCGS-only branches, Godot-only branches, or
  single-skill assumptions in runtime core.
