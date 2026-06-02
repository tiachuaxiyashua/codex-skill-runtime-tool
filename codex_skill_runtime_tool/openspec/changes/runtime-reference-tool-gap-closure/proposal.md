# Proposal: Runtime Reference Tool Gap Closure

## Why

The runtime now covers the core skill execution loop, but the reference project
still exposes additional first-class tools that arbitrary skills may request.
If those tool names are absent, strict mode rejects the action before the model
can use a generic fallback.

The highest execution-effect gap is the split between task-list tools and
background agent tools. The reference project uses `TaskCreate`/`TaskGet`/
`TaskList`/`TaskUpdate` for task tracking, while `Agent`/legacy `Task`,
`SendMessage`, `TaskOutput`, and `TaskStop` manage background workers.

## What Changes

- Align `TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate` with task-list
  semantics.
- Keep background worker execution on `Agent`/legacy `Task`, including
  background worker polling through `TaskOutput`.
- Add generic tools for Sleep, DiscoverSkills, NotebookEdit, Worktree, Cron,
  SendUserFile, Config, LSP, Monitor, Snip, ReviewArtifact, Brief, Workflow, and
  Team lifecycle records.
- Expose MCP OAuth as a model-visible `McpAuth` action and accept dynamic
  `mcp__<server>__<tool>` names in strict action schema.
- Update schema, action prompt, aliases, permissions, selftests, and evidence.

## Scope

This change remains runtime-generic. It adds clean-room compatibility behavior
for public tool contracts and local state lifecycles; it does not add private UI,
marketplace lifecycle behavior, or domain-specific pipelines.
