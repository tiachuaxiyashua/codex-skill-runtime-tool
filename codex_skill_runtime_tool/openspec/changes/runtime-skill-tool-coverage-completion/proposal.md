# Proposal: Runtime Skill Tool Coverage Completion

## Why

The runtime can load and execute Claude Code style skills, but several reference
mechanism families are not yet model-visible as first-class tools. A generic
skill may expect explicit task query/update tools, deferred tool discovery,
plan-mode lifecycle, MCP resource access, terminal capture/REPL, structured
tool transcripts, or a lightweight browser state.

Without these mechanisms, the runtime can still complete some workflows, but
arbitrary skills may route around the intended state machine or lose evidence.

## What Changes

- Add a full persistent Task tool family on top of the existing worker registry.
- Add ToolSearch over runtime tools, visible skills, capabilities, and MCP servers.
- Add MCP resources list/read and elicitation lifecycle tools.
- Add PowerShell, TerminalCapture, and Python REPL tools with persisted captures.
- Add PlanMode enter/exit/verify lifecycle records.
- Add a lightweight stateful WebBrowser tool.
- Add structured tool_use/tool_result transcript records and replay injection.
- Update strict action schema, prompt tool list, selftests, and OpenSpec evidence.

## Scope

This change is runtime-generic. It does not add domain-specific pipelines,
marketplace lifecycle behavior, private UI behavior, or private prompt behavior.
