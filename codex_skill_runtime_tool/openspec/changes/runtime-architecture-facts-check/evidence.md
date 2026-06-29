# Evidence: runtime-architecture-facts-check

Date: 2026-06-29

## Audit Selftest

```text
python3 codex_skill_runtime_tool/scripts/architecture_facts_audit.py --selftest
```

Result:

```text
SELFTEST_SUMMARY total=22 failed=0
```

## OpenSpec

```text
cd codex_skill_runtime_tool
openspec validate runtime-architecture-facts-check --strict
```

Result:

```text
Change 'runtime-architecture-facts-check' is valid
```

## Snapshot Output

The audit script can render both markdown and JSON:

```text
python3 codex_skill_runtime_tool/scripts/architecture_facts_audit.py --format markdown
python3 codex_skill_runtime_tool/scripts/architecture_facts_audit.py --format json
```

The human-readable diagnosis in `项目问题分析图谱.md` now states that its numbers are a regenerable workspace snapshot rather than permanent facts.
