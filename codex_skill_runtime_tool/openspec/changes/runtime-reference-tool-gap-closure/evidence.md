# Evidence

## Local Verification

- `python -m compileall codex_skill_runtime_tool\codex-skill-runtime-core\runtime`
  - PASS
- `openspec validate runtime-reference-tool-gap-closure --strict`
  - PASS
- Hardcoding scan over changed runtime/schema/OpenSpec files
  - PASS, no matches for local drive paths, API keys, domain-specific runtime names, or asset-pipeline names.
- `git diff --check`
  - PASS, no whitespace errors.
- `python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env selftest`
  - PASS: `SELFTEST_SUMMARY total=49 failed=0`
  - Latest session: `20260604-104415-prototype` through `20260604-104554-selftest-hook`.
  - Passing contracts include task-list `TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate`, background `Agent` plus `TaskOutput`, MCP auth pseudo-tool, dynamic MCP names, Cron fire queue, full runtime ToolSearch coverage, and Windows bash hook shim execution.
- `python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env strict-smoke README.md`
  - PASS: live Codex strict action loop reached `STRICT-SMOKE PASS`.
  - Session: `20260604-104957-strict-smoke`
- `codegraph` runtime map
  - PASS: generated Python runtime code graph CSV with 502 module/class/function rows for dependency review.

## Known Limits

- Background workers are process-local. If the runtime is restarted while a worker is `running`, the reloaded registry marks it `interrupted` so it is not mistaken for a live thread.
- Cron fires are queued while the runtime process is alive. The runtime does not keep a finished CLI command alive solely to wait for future cron entries, so long-lived cron behavior requires a long-running UI/runtime process.
- WebBrowser remains a lightweight HTTP/HTML browser, not a full JavaScript browser.
