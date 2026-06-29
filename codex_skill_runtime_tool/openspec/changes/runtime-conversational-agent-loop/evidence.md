# Evidence: runtime-conversational-agent-loop

Date: 2026-06-19

## OpenSpec

```text
cd codex_skill_runtime_tool && openspec validate runtime-conversational-agent-loop --strict
```

Result:

```text
Change 'runtime-conversational-agent-loop' is valid
```

## Compile Check

```text
PYTHONPYCACHEPREFIX=codex_skill_runtime_tool/.skill-runtime/pycache <python3.12> -m py_compile \
  codex_skill_runtime_tool/codex-skill-runtime-core/runtime/runtime.py \
  codex_skill_runtime_tool/codex-skill-runtime-core/runtime/session.py \
  codex_skill_runtime_tool/codex-skill-runtime-core/runtime/tool_executor.py \
  codex_skill_runtime_tool/codex-skill-runtime-core/runtime/universal_cli.py \
  codex_skill_runtime_tool/codex-skill-runtime-core/runtime/selftest.py \
  codex_skill_runtime_tool/codex-skill-runtime-ui/backend/server.py
```

Result: exit code 0.

## Conversational Loop Probe

Used a temporary generic skill repository and a fake Codex executable that returns strict JSON action responses.

Verified:

- natural-language chat entered `run_chat_loop`;
- first model step requested `skill`;
- runtime loaded `probe-skill`;
- next model step requested `brief`;
- runtime persisted `briefs.jsonl`;
- final model step returned `status: final`;
- explicit `ask_user_question` created a pending question;
- answering that pending question resumed the conversational action loop with prior session replay and answer context;
- final assistant text containing `?` did not create a pending question.

Observed:

```text
<python3.12> -B codex_skill_runtime_tool/codex-skill-runtime-core/core_cli.py \
  --root codex_skill_runtime_tool \
  selftest --only conversational-loop

PASS: conversational-loop-contract - session=<runtime-session-id>
SELFTEST_SUMMARY total=1 failed=0
```

## CLI Chat Probe

```text
<python3.12> -B codex_skill_runtime_tool/codex-skill-runtime-core/core_cli.py \
  --root <temporary-probe-repo> \
  --target-workspace <temporary-probe-repo> \
  --skill-repo <temporary-probe-repo> \
  --codex <temporary-probe-repo>/fake-codex-chat.py \
  --qa off --max-steps 5 \
  chat Use the conversational runtime probe skill
```

Result:

```text
primary: strict-step-3 exit=0
gate: CHAT-LOOP PASS - Conversational action loop reached final status.
```

## Step Budget Probe

Used a fake Codex executable that always returns `status: action_required`.

```text
skill-runtime ... --max-steps 2 chat Budget exhaustion probe
```

Result:

```text
exit=2
gate: CHAT-LOOP BLOCKED - Maximum strict action steps reached: 2
```

This verifies `max_steps` remains a safety budget and does not become a successful completion condition.

## UI Projection Probe

Called backend `_conversation_events_for_ui` with synthetic persisted events, then applied the frontend dialogue/process classification rules.

Verified:

- `action_required` JSON becomes `kind=model_stream`, `role=runtime`;
- `assistant.brief` becomes `kind=assistant_message`, `role=assistant`;
- `status=final` JSON becomes `kind=assistant_message`, text is final content;
- `tool.start` remains `kind=tool_call`.
- visible reasoning summaries, model stream events, and tool calls are process rows, not middle-dialogue rows;
- middle dialogue contains only user messages, assistant messages, explicit questions, and answers.

Result:

```text
<python3.12> -B codex_skill_runtime_tool/codex-skill-runtime-core/core_cli.py \
  --root codex_skill_runtime_tool \
  selftest --only conversational-loop --only ui-conversation-projection

PASS: conversational-loop-contract - session=<runtime-session-id>
PASS: ui-conversation-projection-contract - dialogue=user_message,assistant_message,question process=reasoning,model_stream,model_finish,model_stream,summary,tool_call
SELFTEST_SUMMARY total=2 failed=0
```

## Browser UI Smoke

Attempted to start the local UI on a temporary port and open it with Playwright.

Result: not completed in this sandbox.

Observed environment failures:

```text
PermissionError: [Errno 1] Operation not permitted
```

while binding `ThreadingHTTPServer`, and:

```text
bootstrap_check_in ... Permission denied
```

while launching Playwright Chromium.

The backend projection and static UI code were verified, but a live browser smoke test must be rerun in a local environment that permits binding localhost ports and launching Chromium.

## Hardcoding Scan

Scanned changed runtime, UI, selftest, and OpenSpec files for new CCGS/Godot/Forge/ComfyUI/local absolute-path/provider-specific branches.

Findings:

- OpenSpec mentions CCGS/Godot/Forge/ComfyUI only as prohibited hardcoding examples.
- Existing unrelated defaults such as `gpt-5.4` remain in pre-existing model config/selftest areas.
- New selftest uses a generic temporary `probe-skill` fixture only for deterministic testing.

No new runtime behavior special-cases a game, one skill repository, one local user path, one art/audio pipeline, or one provider.
