---
name: godot-tool-bridge
description: Run Godot project import, headless smoke checks, and optional gameplay test scripts for Godot projects. Use when a loaded workflow needs to verify or integrate a Godot game without relying on runtime-core Godot tools.
allowed-tools: read_file, write_file, edit_file, glob, grep, bash, project_memory_read, project_memory_write
disable-model-invocation: false
---

# Godot Tool Bridge

Use this skill when another skill needs Godot-specific execution: importing assets, checking that a project opens headlessly, running a project smoke check, or executing `res://scripts/gameplay_test.gd`.

This is a normal plugin skill. Do not assume Godot is built into the runtime core.

## Workflow

1. Locate the Godot project directory. It must contain `project.godot`.
2. Locate the Godot executable from, in order:
   - the user's explicit argument,
   - `GODOT_EXE`,
   - `SKILL_RUNTIME_ENV_GODOT_EXE`,
   - `SKILL_RUNTIME_GODOT`.
3. Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/godot_smoke.py" --project "<project-dir>" --godot "<godot-exe-or-dir>"
```

4. Read the JSON result printed by the script and the evidence files it reports.
5. If the result is not `PASS`, stop and report the failing phase with the evidence paths.
6. If a CCGS or other game workflow is active, return concise evidence for that workflow's QA gate.

## Evidence Contract

Report:

- Godot executable path.
- Project directory.
- Import command return code.
- Smoke command return code.
- Gameplay test command return code if `scripts/gameplay_test.gd` exists.
- `stdout` and `stderr` evidence file paths for every command run.

Do not claim a Godot project was tested unless this script or an equivalent explicit command actually ran.
