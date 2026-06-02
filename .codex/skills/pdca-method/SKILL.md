---
name: pdca-method
description: Use when the user asks to use PDCA方法/PDCA method, or when analyzing or fixing recurring complex runtime, skill compatibility, architecture, QA, deployment, memory, context, or tool orchestration problems that must avoid one-off hardcoded patches and requires root-cause analysis, general mechanism design, hardcoding review, verification, and iterative closure.
---

# PDCA方法

Use this skill as a strict loop for recurring or high-risk problems where superficial patches are likely. The goal is to close the real mechanism gap, not just make the current sample pass.

## Loop

Run these stages in order. Do not skip the hardcoding review before editing source code, runtime behavior, skill behavior, configuration, or test fixtures.

1. **Plan: Root Cause**
   - State the concrete failing surface: command, runtime module, skill contract, agent route, memory path, MCP/tool call, UI state, test, or user-visible workflow.
   - Use current evidence from code, docs, tests, logs, runtime artifacts, benchmark records, or observed behavior.
   - Separate confirmed facts from hypotheses.
   - Identify why previous fixes did not hold, especially where state is lost, gates diverge, fallbacks bypass the main path, skill instructions are not surfaced, subagents do not inherit context, or tests measure the wrong thing.

2. **Plan: Solution**
   - Propose the smallest general mechanism that addresses the root cause.
   - Name the data contract or invariant that should remain true after the fix.
   - Define an acceptance check before editing code.
   - If the change affects runtime behavior, skill compatibility, user workflow, deployment, or tests, keep OpenSpec/docs coherent before implementation when repo policy requires it.

3. **Check: No Hardcoded Patch**
   - Inspect the proposed implementation for request-specific, skill-specific, path-specific, project-specific, title-specific, fixture-specific, route-specific, benchmark-specific, or provider-specific special cases.
   - Reject fixes that only mention the observed example, such as one skill name, one agent name, one hardcoded drive path, one benchmark id, one API URL, one model name, or one artificial phrase.
   - Allow domain rules only when they are expressed as reusable contracts, schemas, adapters, capability registry entries, thresholds, normalization, ranking features, persistence semantics, or generic gates.
   - State the conclusion explicitly before code edits: `Hardcoding review: pass` or `Hardcoding review: fail`.

4. **Do: Implement**
   - Make focused source changes that preserve existing local patterns.
   - Keep fallback and main paths on shared gates when possible.
   - Preserve user-visible behavior separately from diagnostics/internal telemetry.
   - Add or update tests around the invariant, not only around one sample.

5. **Check: Verify**
   - Run targeted unit, contract, runtime, or integration tests that exercise the invariant.
   - When a runtime claim depends on an external service, run a real service smoke test or mark it honestly as not verified.
   - Measure the same behavior that exposed the bug. Do not claim success from unrelated green checks.

6. **Act: Decide**
   - If acceptance passes, summarize the root cause, fix, evidence, and residual risk.
   - If acceptance fails, do not stack patches. Start the next loop at **Plan: Root Cause** using the new evidence.
   - Record useful durable learning in tests, docs, OpenSpec artifacts, or skill instructions only when it improves future work.

## Reporting

During work, report each loop compactly:

```text
PDCA loop N
Root cause: ...
Solution: ...
Hardcoding review: pass/fail because ...
Implementation: ...
Verification: ...
Decision: pass / repeat with new evidence
```

## Runtime And Skill Compatibility Defaults

When this skill is used for a generic skill runtime, Claude Code skill compatibility, Codex skill execution, or long-running agent workflow:

- Treat a skill as a portable capability package. Do not solve by hardcoding one skill, one game project, one local path, or one service endpoint into the runtime core.
- Prefer capability registries, plugin manifests, skill metadata, MCP/tool adapters, and runtime-owned session memory over direct special cases.
- Keep the runtime generic. Game, art, audio, Godot, Forge, ComfyUI, and CCGS behavior should enter through loaded skills/plugins/capabilities unless there is a clearly generic runtime primitive.
- Check whether a missing behavior is a core mechanism gap, a skill packaging gap, a configuration gap, or a test coverage gap before proposing implementation.
- For long tasks, evaluate transcript durability, session memory, long-term memory recall, compaction, interruption recovery, subagent inheritance, and tool-result storage as separate mechanisms.
- For hardcoding review, actively scan for absolute local paths, fixed drive letters, single-skill names, single-project assumptions, provider-specific model names, and hidden dependency on the developer's machine.
- For verification, prefer evidence from runtime artifacts, selftests, real command output, generated session state, and external service smoke tests over prose claims.
