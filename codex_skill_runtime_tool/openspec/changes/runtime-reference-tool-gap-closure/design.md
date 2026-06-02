# Design: Runtime Reference Tool Gap Closure

## Task List And Background Agent Split

`TaskCreate` shall create a task-list item with subject, description, status,
owner, dependencies, and metadata. `TaskGet`, `TaskList`, and `TaskUpdate`
operate on the same task-list state.

`Agent` and legacy `Task` remain the worker/subagent entrypoints. They can run
synchronously by default or start a background worker when
`run_in_background`/`background` is true or `wait=false`. `TaskOutput`,
`TaskStop`, and `SendMessage` operate on worker records. `TaskOutput` supports
reference-style `block` and `timeout` parameters.

## Additional Tool Families

- `Sleep`: bounded wait for polling workflows.
- `DiscoverSkills`: structured skill discovery over the same loader used by the
  skill registry.
- `NotebookEdit`: JSON-safe `.ipynb` cell replace/insert/delete.
- `EnterWorktree` / `ExitWorktree`: create/remove Git worktree records.
- `CronCreate` / `CronList` / `CronDelete`: session schedule records plus a
  process-local fire queue for due prompts while the runtime process is alive.
- `SendUserFile`: register and preview user-provided files.
- `Config`: safe runtime config inspection and plugin enable/disable routing.
- `LSP`: direct alias to existing IDE LSP command execution.
- `Monitor`: session, worker, and job state inspection.
- `Snip`: bounded file line extraction.
- `ReviewArtifact`: persist artifact review notes.
- `Brief`: persist concise briefs.
- `Workflow`: persist workflow state.
- `TeamCreate` / `TeamDelete`: persist team membership records.
- `McpAuth`: start or complete OAuth for any configured MCP server without
  binding runtime core to one server name. ToolSearch also exposes
  `mcp__<server>__authenticate` pseudo-tools.

## Dynamic MCP Tools

Strict action schema accepts dynamic `mcp__<server>__<tool>` names in addition
to its fixed runtime tool enum. Dynamic MCP actions still pass through the same
ToolExecutor permission and hook gates before transport dispatch.

## Invariants

- All tools use existing ToolExecutor permission and hook gates.
- All tools persist state under the runtime session or runtime state root.
- No tool embeds local absolute paths, API keys, or domain-specific behavior.
