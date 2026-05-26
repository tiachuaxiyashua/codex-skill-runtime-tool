# Evidence: codex-runtime-equivalence

本文件记录本 change 完成时的实测证据。路径均相对
`<skill-repo-root>`。

## OpenSpec

```powershell
openspec validate codex-runtime-equivalence --strict
```

结果：

```text
Change 'codex-runtime-equivalence' is valid
```

## Python Compile

```powershell
python -B -m compileall .\codex-skill-runtime-core
```

结果：所有 runtime Python 文件编译通过。编译产生的 `__pycache__` 已清理。

## Full Live Selftest

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py --godot <godot-executable-or-dir> selftest --godot-project <godot-project> --live-strict-target README.md --live-qa-target <godot-project>
```

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-010455-prototype
PASS: strict-dry-run-contract - session=20260523-010455-strict-prototype
PASS: tool-executor-contract - session=20260523-010455-selftest-tools
PASS: permission-contract - session=20260523-021458-selftest-permissions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-021502-selftest-mcp
PASS: hook-shim-contract - session=20260523-010458-selftest-hook
PASS: godot-contract - session=20260523-010459-godot-smoke
PASS: live-strict-contract - session=20260523-010459-strict-smoke
PASS: live-codex-qa-contract - session=20260523-010908-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=14 failed=0
```

## Completion Meaning

该证据证明的是 CCGS 技能组的可观察执行效果等价：loader、路由、Task、hook、
工具代理、QA gate、Godot 实测和 session evidence 都已经由 runtime 强制执行。
它不声明 Claude Code 隐藏 prompt、私有 UI、缓存或模型内部策略被复制。

## GitHub Claude Skill Compatibility

本轮还从 GitHub 下载并运行了多个 Claude skill 仓库，详细审计见
`codex-skill-runtime-core/docs/CLAUDE_SKILL_COMPAT_AUDIT_CN.md`。

代表性 PASS session：

- `obra/superpowers`：`20260523-003317-strict-using-superpowers`
- `samber/cc-skills-golang`：`20260523-003813-strict-golang-error-handling`
- `getsentry/skills`：`20260523-002248-strict-code-review`
- `daymade/claude-code-skills`：`20260523-002828-strict-prompt-optimizer`
- `TechyMT/claude-code-superpowers`：`20260523-002828-strict-skill-and-command-dispatch`

## Expanded GitHub Skill / Plugin Compatibility

新增样本与证据：

- `rsmdt/the-startup`：`20260523-014144-strict-start-review`，插件 namespace、
  嵌套 skills、递归 agents、startup/company workflow，STRICT PASS。
- `howells/arc`：`20260523-020408-strict-arc-using-arc`，repo-root commands 与
  编程 lifecycle skill，STRICT PASS。
- `coderabbitai/skills`：`20260523-013649-strict-coderabbit-coderabbit-review`，
  command 动态上下文和 Bash 前置检查已执行；因本机未安装 CodeRabbit CLI 按 skill
  要求 BLOCKED。
- `DeepBitsTechnology/claude-plugins`：`drbinary-chat-plugin` loader PASS；远程 MCP
  配置被识别，runtime 现在具备 HTTP bridge；缺少认证或网络失败时明确 BLOCKED。
- `anthropics/claude-code` public plugin examples：plugin hook `${CLAUDE_PLUGIN_ROOT}`
  替换、官方 plugin-dev command/skill 布局、stdio/HTTP/SSE MCP bridge 和 headersHelper 均由 selftest 覆盖。

新增 selftest 项：

```text
PASS: permission-contract
PASS: external-layout-contract
PASS: mcp-bridge-contract
SELFTEST_SUMMARY total=14 failed=0
```
## 2026-05-23 Latest Live Evidence

Full live selftest was rerun after command preprocessing, plugin manifest, MCP, and hook-decision
changes:

```text
PASS: command-preprocessing-contract - session=20260523-030345-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-030345-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-030345-selftest-hook-decisions
PASS: godot-contract - session=20260523-030350-godot-smoke
PASS: live-strict-contract - session=20260523-030351-strict-smoke
PASS: live-codex-qa-contract - session=20260523-030655-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=17 failed=0
```

## 2026-05-23 Remote MCP And Memory Evidence

After adding remote MCP transports and deterministic session memory, ordinary selftest was rerun:

```text
PASS: mcp-bridge-contract - session=20260523-134722-selftest-mcp
PASS: memory-compaction-contract - session=20260523-134733-selftest-memory
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=18 failed=0
```

The MCP bridge check starts local stdio, HTTP, and SSE MCP servers, exercises `headersHelper`,
and verifies real `tools/call` results. The memory check writes `summary.json`, updates
`.codex-skill-runtime/sessions-index.json`, and verifies bounded runtime memory context can be injected
into later prompts.

Final full live selftest after this tranche:

```text
PASS: mcp-bridge-contract - session=20260523-135401-selftest-mcp
PASS: memory-compaction-contract - session=20260523-135411-selftest-memory
PASS: godot-contract - session=20260523-135412-godot-smoke
PASS: live-strict-contract - session=20260523-135413-strict-smoke
PASS: live-codex-qa-contract - session=20260523-135915-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=18 failed=0
```

Representative external GitHub plugin runs after this tranche:

- `howells/arc`: `/arc:using-arc`, session `20260523-032012-strict-arc-using-arc`, STRICT PASS.
- `rsmdt/the-startup`: `/start:review`, session `20260523-032344-strict-start-review`, STRICT PASS with five review subagent tasks.
- `coderabbitai/skills`: `/coderabbit:coderabbit-review`, session `20260523-033914-strict-coderabbit-coderabbit-review`, correctly BLOCKED because CodeRabbit CLI is not installed locally.

After fixing strict concurrent Task numbering, full live selftest was rerun on the final code:

```text
PASS: command-preprocessing-contract - session=20260523-034525-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-034525-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-034525-selftest-hook-decisions
PASS: godot-contract - session=20260523-034529-godot-smoke
PASS: live-strict-contract - session=20260523-034529-strict-smoke
PASS: live-codex-qa-contract - session=20260523-034817-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=17 failed=0
```
