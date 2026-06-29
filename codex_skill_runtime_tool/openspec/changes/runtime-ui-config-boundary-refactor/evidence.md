# Evidence: runtime-ui-config-boundary-refactor

Date: 2026-06-29

## Config Selftest

```text
python3 codex_skill_runtime_tool/scripts/ui_config_selftest.py
```

Result:

```text
SELFTEST_SUMMARY total=16 failed=0
```

Verified:

- env variable expansion;
- model config projection;
- API key file existence detection;
- service config parsing from JSON and env vars;
- runtime env export mapping;
- state root and target workspace resolution;
- foreign Windows path fallback on non-Windows;
- managed model env write-back.

## Compile Check

```text
python3 -m py_compile \
  codex_skill_runtime_tool/scripts/ui_config_selftest.py \
  codex_skill_runtime_tool/codex-skill-runtime-ui/backend/ui_config.py \
  codex_skill_runtime_tool/codex-skill-runtime-ui/backend/server.py
```

Result: exit code 0.

## Server Import Smoke

```text
python3 - <<'PY'
import sys
from pathlib import Path
backend = Path('codex_skill_runtime_tool/codex-skill-runtime-ui/backend').resolve()
sys.path.insert(0, str(backend))
import server
values = server._load_env(server.DEFAULT_ENV)
paths = server._runtime_env_paths(server.DEFAULT_ENV)
model = server._model_config_from_env(values, server.DEFAULT_ENV)
print('SERVER_IMPORT_OK')
print('target_workspace=', paths['target_workspace'])
print('skill_repos=', len(paths['skill_repos']))
print('model=', model['config']['model'])
PY
```

Observed:

```text
SERVER_IMPORT_OK
target_workspace= /Users/sanchuan/Documents/chuanproject/claude_code_game_sutdio
skill_repos= 1
model= gpt-5.4
```

## OpenSpec

```text
cd codex_skill_runtime_tool
openspec validate runtime-ui-config-boundary-refactor --strict
openspec validate runtime-architecture-facts-check --strict
openspec validate codex-runtime-equivalence --strict
```

Result:

```text
Change 'runtime-ui-config-boundary-refactor' is valid
Change 'runtime-architecture-facts-check' is valid
Change 'codex-runtime-equivalence' is valid
```

## Architecture Snapshot

```text
python3 codex_skill_runtime_tool/scripts/architecture_facts_audit.py --format markdown
```

Observed selected line count after refactor:

```text
server.py: 2743
tool_executor.py: 2124
runtime.py: 1940
selftest.py: 2854
app.js: 2213
```

The earlier architecture snapshot recorded `server.py: 3173`, so this change removed roughly 430 lines of config parsing responsibility from `server.py` and moved it into `ui_config.py`.
