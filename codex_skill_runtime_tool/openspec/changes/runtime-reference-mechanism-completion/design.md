# Design: Complete Reference Runtime Mechanisms

## QA Resolution

Required QA uses a resolver, not a direct agent literal. Resolution order:

1. explicit runtime environment/config value
2. skill frontmatter metadata
3. discovered capability metadata
4. bundled/default fallback

The resolver records its source in session evidence.

## Side-Query Memory Selection

Memory recall accepts an optional side-query selector. The selector receives a bounded manifest and
must return JSON with selected filenames. Invalid or unavailable side-query output falls back to
deterministic lexical scoring.

## Background Memory Jobs

Extraction and consolidation write job records under `memory/jobs/`. The default implementation may
run inline for deterministic tests, but the state shape is background-safe: queued, running,
completed, failed.

## Session-Memory Compact And Autocompact

The runtime estimates context tokens, records a compact state, and writes compact summaries when
usage crosses configured thresholds. This is runtime-level compaction metadata and prompt shaping;
it does not claim provider prompt-cache equivalence.

## API Transcript

Codex prompt and assistant exchanges are persisted as API-like message JSONL records. Resume loads
these records in addition to runtime event replay.

## Worker Scratchpad

Worker records persist scratchpad directories. Scratchpad files are listed in worker evidence and
resume context without assuming a domain-specific workflow.

## Hardcoding Boundary

All mechanisms are driven by config, metadata, capabilities, session state, or env. The runtime core
must not special-case a game engine, art pipeline, audio pipeline, local absolute path, or one skill
repository.
