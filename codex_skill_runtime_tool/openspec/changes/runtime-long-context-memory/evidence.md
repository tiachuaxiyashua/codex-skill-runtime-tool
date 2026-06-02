# Evidence

## Verification

- `python -m compileall codex_skill_runtime_tool\codex-skill-runtime-core\runtime`
- `python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env selftest`
- `openspec validate runtime-long-context-memory --strict`
- `python $CODEX_HOME\skills\.system\skill-creator\scripts\quick_validate.py .codex\skills\pdca-method`

Selftest result:

```text
SELFTEST_SUMMARY total=36 failed=0
```

OpenSpec result:

```text
Change 'runtime-long-context-memory' is valid
```

Project skill validation:

```text
Skill is valid!
```

Live external-service checks were not supplied and were skipped by the selftest runner:

```text
SKIP: live-strict-contract - no --live-strict-target supplied
SKIP: live-codex-qa-contract - no --live-qa-target supplied
```

## Hardcoding Scan

Scanned core runtime and this OpenSpec change for fixed local paths, real API secrets, and
domain-specific runtime branches. Findings were limited to:

- OpenSpec/PDCA text that names CCGS/Godot/Forge/ComfyUI as examples of things that must not be hardcoded.
- Existing selftest namespace fixtures such as `ccgs:start`.
- Existing fake test secrets such as `sk-test-secret`.

No new core runtime branch was added for a specific skill, game engine, art pipeline, local drive,
or provider endpoint.
