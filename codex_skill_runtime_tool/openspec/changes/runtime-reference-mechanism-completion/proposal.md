# Proposal: Complete Reference Runtime Mechanisms

## Problem

The current runtime has first-pass long-context memory, but several reference-inspired mechanisms
are still partial:

- memory recall uses deterministic lexical scoring instead of a side-query contract
- durable memory extraction/consolidation runs synchronously and deterministically only
- transcript resume reconstructs runtime events, not API-message-level exchanges
- worker persistence does not include a structured scratchpad lifecycle
- QA still selects `qa-tester` directly instead of resolving from config, frontmatter, or capability
- context compaction lacks a session-memory compact record and autocompact trigger contract

## Goals

- Add generic side-query memory selection with deterministic fallback.
- Add background memory job records for extraction and consolidation.
- Add session-memory compact/autocompact state records.
- Persist API-message transcript entries for prompt/assistant exchanges and expose them in resume.
- Persist worker scratchpads alongside worker records.
- Resolve QA agents through config, frontmatter, capability metadata, and fallback.
- Keep all mechanisms generic and independent of any skill repository or game workflow.

## Non-Goals

- No private prompt copying.
- No marketplace lifecycle implementation.
- No hardcoded provider endpoint, model, game engine, or skill name except as fallback names that can
  be overridden by config/capability/frontmatter.

## Acceptance

- Runtime selftest covers each new mechanism.
- OpenSpec validates with `--strict`.
- Hardcoding scan finds no new fixed local paths, provider endpoints, API secrets, or domain-specific
  runtime branches.
