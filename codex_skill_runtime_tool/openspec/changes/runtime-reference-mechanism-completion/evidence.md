# Evidence

## Verification Commands

```text
python -m compileall codex_skill_runtime_tool\codex-skill-runtime-core\runtime
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env selftest
openspec validate runtime-reference-mechanism-completion --strict
```

## Selftest Result

```text
SELFTEST_SUMMARY total=42 failed=0
```

The selftest runner skipped live external-service checks because no live targets were supplied:

```text
SKIP: live-strict-contract - no --live-strict-target supplied
SKIP: live-codex-qa-contract - no --live-qa-target supplied
```

## Covered Contracts

- QA agent resolution from frontmatter/config/capability/fallback.
- Side-query memory selection with deterministic fallback.
- Memory extraction and consolidation job records.
- API-message transcript persistence and resume injection.
- Worker scratchpad persistence and resume injection.
- Compact state and session-memory compact records.

## Hardcoding Scan

Scanned newly changed runtime modules and this OpenSpec change for fixed local paths, real API keys,
fixed provider endpoints, and game/pipeline-specific branches. No new hardcoded runtime dependency
was found.
