# CCGS Codex Runtime 实测证据

本文记录本轮完成后的实际运行验证。所有命令都在下面目录执行：

```text
<skill-repo-root>
```

## 1. OpenSpec 校验

命令：

```powershell
openspec validate codex-runtime-equivalence --strict
```

结果：

```text
Change 'codex-runtime-equivalence' is valid
```

## 2. Python 编译校验

命令：

```powershell
python -B -m compileall .\codex-skill-runtime-core
```

结果：所有 Python 文件编译通过。编译产生的 `__pycache__` 已清理。

## 3. 完整 live selftest

命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py selftest --live-strict-target README.md --live-qa-target <project-path>
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

## 4. 这个结果证明了什么

这次 selftest 证明：

- runtime 找到了 73 个 skill 和 49 个 agent。
- `/prototype`、`/team-qa`、`qa-tester` 的 frontmatter 路由正确。
- Task 解析和 QA gate 能拒绝弱 QA。
- dry-run 能生成正确的 Codex 命令形态和 prompt。
- strict action-loop 能真实调用 Codex，并由 runtime 执行工具。
- runtime tool executor 能读、搜、写、改、执行 bash、处理 question，并拒绝写入 `.claude`。
- permission contract 能区分 `ask`、`deny`，且不会把 skill `allowed-tools` 误当硬白名单。
- external layout contract 能发现插件 namespace、命令、嵌套 skill、递归 agent 和 fork skill。
- stdio MCP bridge 能真实初始化本地 MCP server 并调用 `tools/call`。
- hook shim 能触发子代理开始/结束 hook。
- Godot headless 和 gameplay test 真实运行并通过。
- 真实 `qa-tester` 子代理输出被 gate 接受。
- `.claude` 原件没有被修改。

## 5. 等价边界

本验证证明的是 CCGS 技能组的可观察执行效果等价。它不证明 Claude Code 隐藏
system prompt、私有 UI、内部缓存、模型 token 级输出完全相同。

## 6. 本轮新增验证

新增普通 selftest：

```text
PASS: permission-contract - session=20260523-021109-selftest-permissions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-021113-selftest-mcp
SELFTEST_SUMMARY total=14 failed=0
```

新增外部仓库验证：

- `rsmdt/the-startup`：`/start:review` live strict PASS，session
  `20260523-014144-strict-start-review`。
- `howells/arc`：`/arc:using-arc` live strict PASS，session
  `20260523-020408-strict-arc-using-arc`。
- `coderabbitai/skills`：`/coderabbit:coderabbit-review` live strict 正确执行前置
  Bash 检查，并因本机缺 CodeRabbit CLI 返回 BLOCKED，session
  `20260523-013649-strict-coderabbit-coderabbit-review`。
- `DeepBitsTechnology/claude-plugins`：`drbinary-chat-plugin` loader PASS；其远程
  MCP 配置被识别；当前 runtime 已有 HTTP bridge，若远程服务要求认证或网络不可达，会明确 BLOCKED。
## 2026-05-23 本轮新增验证

普通 selftest 已扩展到 17 项并通过：

```text
PASS: command-preprocessing-contract
PASS: plugin-manifest-contract
PASS: hook-decision-contract
SELFTEST_SUMMARY total=17 failed=0
```

## 2026-05-23 远程 MCP 与记忆补齐后的普通 selftest

本轮新增远程 MCP 和 runtime memory 后，先运行普通 selftest：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py selftest
```

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-134655-prototype
PASS: strict-dry-run-contract - session=20260523-134704-strict-prototype
PASS: tool-executor-contract - session=20260523-134712-selftest-tools
PASS: permission-contract - session=20260523-134718-selftest-permissions
PASS: command-preprocessing-contract - session=20260523-134718-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-134718-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-134718-selftest-hook-decisions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-134722-selftest-mcp
PASS: memory-compaction-contract - session=20260523-134733-selftest-memory
PASS: hook-shim-contract - session=20260523-134733-selftest-hook
SKIP: live-plugin-contract - no plugin live target supplied
SKIP: live-strict-contract - no --live-strict-target supplied
SKIP: live-codex-qa-contract - no --live-qa-target supplied
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=18 failed=0
```

新增验证含义：

- `mcp-bridge-contract` 现在覆盖 stdio、HTTP、SSE 和 headersHelper；HTTP/SSE 都启动本地真实 MCP 测试服务器并调用 `tools/call`。
- `memory-compaction-contract` 证明 runtime 会写 session `summary.json`、更新 `.codex-skill-runtime/sessions-index.json`，并能生成 `Runtime Memory / Compacted Session Context`。
- `.claude` 仍保持无 diff。

## 2026-05-23 远程 MCP 与记忆补齐后的最终完整 live selftest

命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py selftest --live-strict-target README.md --live-qa-target <project-path>
```

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-135330-prototype
PASS: strict-dry-run-contract - session=20260523-135341-strict-prototype
PASS: tool-executor-contract - session=20260523-135350-selftest-tools
PASS: permission-contract - session=20260523-135356-selftest-permissions
PASS: command-preprocessing-contract - session=20260523-135356-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-135356-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-135356-selftest-hook-decisions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-135401-selftest-mcp
PASS: memory-compaction-contract - session=20260523-135411-selftest-memory
PASS: hook-shim-contract - session=20260523-135411-selftest-hook
PASS: godot-contract - session=20260523-135412-godot-smoke
PASS: live-strict-contract - session=20260523-135413-strict-smoke
PASS: live-codex-qa-contract - session=20260523-135915-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=18 failed=0
```

新增验证含义：

- `command-preprocessing-contract` 证明 `$1/$2`、`$ARGUMENTS[index]`、`@file`、`@${CLAUDE_PLUGIN_ROOT}/file` 和动态命令在 prompt 构造前被正确处理。
- `plugin-manifest-contract` 证明 manifest 自定义 component 路径会补充默认目录，并且插件根 `.mcp.json` 与 manifest MCP path 都能被发现。
- `hook-decision-contract` 证明 command hook 的 `permissionDecision`、`updatedInput`、exit code `2` 和 prompt hook 的 `decision:block` 都会被 runtime 解释并执行。

待本轮完整 live selftest 重新运行后，本节会同步记录带 Godot、live strict、live QA 的最新 17 项结果。
## 2026-05-23 最新完整 live selftest

命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py selftest --live-strict-target README.md --live-qa-target <project-path>
```

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-030319-prototype
PASS: strict-dry-run-contract - session=20260523-030330-strict-prototype
PASS: tool-executor-contract - session=20260523-030340-selftest-tools
PASS: permission-contract - session=20260523-030345-selftest-permissions
PASS: command-preprocessing-contract - session=20260523-030345-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-030345-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-030345-selftest-hook-decisions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-030350-selftest-mcp
PASS: hook-shim-contract - session=20260523-030350-selftest-hook
PASS: godot-contract - session=20260523-030350-godot-smoke
PASS: live-strict-contract - session=20260523-030351-strict-smoke
PASS: live-codex-qa-contract - session=20260523-030655-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=17 failed=0
```

外部 GitHub plugin 回归：

- `howells/arc`：`/arc:using-arc`，session `20260523-032012-strict-arc-using-arc`，STRICT PASS。
- `rsmdt/the-startup`：`/start:review`，session `20260523-032344-strict-start-review`，STRICT PASS；该命令实际启动了 5 个 review 子 agent。
- `coderabbitai/skills`：`/coderabbit:coderabbit-review`，session `20260523-033914-strict-coderabbit-coderabbit-review`，按预期 BLOCKED，原因是本机未安装 CodeRabbit CLI；这证明动态上下文和前置检查执行到了真实依赖边界。

## 2026-05-23 并发 Task 编号修复后的最终 live selftest

修复 strict mode 中同一轮多个 Task 并发时 task label 可能重复的问题后，又重新运行了一次完整 live selftest。

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-034509-prototype
PASS: strict-dry-run-contract - session=20260523-034516-strict-prototype
PASS: tool-executor-contract - session=20260523-034522-selftest-tools
PASS: permission-contract - session=20260523-034525-selftest-permissions
PASS: command-preprocessing-contract - session=20260523-034525-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-034525-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-034525-selftest-hook-decisions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-034528-selftest-mcp
PASS: hook-shim-contract - session=20260523-034528-selftest-hook
PASS: godot-contract - session=20260523-034529-godot-smoke
PASS: live-strict-contract - session=20260523-034529-strict-smoke
PASS: live-codex-qa-contract - session=20260523-034817-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=17 failed=0
```
