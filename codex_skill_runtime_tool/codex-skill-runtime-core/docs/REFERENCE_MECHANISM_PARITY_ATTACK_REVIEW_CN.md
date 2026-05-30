# 对照 `<reference-project>` 的机制一致性与 20 轮攻击审查

生成时间：2026-05-23  
对照对象：`<reference-project>`  
被审查对象：`<skill-repo-root>\codex-skill-runtime-core`

## 结论先行

如果把目标定义为“让 CCGS 这套 Claude Code 游戏开发 skill 在 Codex 外层 runtime 下产生相同的可观察执行效果”，当前实现已经接近可用：能发现原始 skill/agent，能按 frontmatter 路由，能让主流程进入 strict action-loop，能由 runtime 执行常见工具，能触发主要 hooks，能启动 Task/Agent 子代理，能跑 QA gate，能跑 Godot smoke/gameplay 测试，能写 session evidence，并且普通 selftest 本轮通过。

如果把目标定义为“机制上 100% 复刻 `<reference-project>` 这套完整 Claude Code 客户端”，当前实现不能称为 100%。参考实现包含长期后台助手、Bridge 远程遥控、OAuth 自动刷新、完整 MCP SDK transport、会话恢复、token budget 续跑、工具结果持久化、Agent memory、Coordinator/SendMessage/TaskStop、UI 权限审批、feature gate、marketplace/plugin lifecycle 等大量客户端级机制。当前 runtime 对其中一部分做了轻量近似，一部分明确未实现。

因此最终判断是：

| 判断对象 | 结论 |
|---|---|
| CCGS 普通 skill/agent/QA/Godot 执行闭环 | 基本一致，已有自测证据 |
| 多个公开 GitHub Claude skill/plugin 的基础执行 | 基本可用，但不是全覆盖 |
| `<reference-project>` 全量客户端机制 | 部分一致，不能说 100% |
| 执行效果层面是否能无条件 100% 还原 Claude Code | 不能，无条件 100% 不成立 |
| 对不使用长会话/OAuth MCP/Coordinator 续跑/自动记忆的 skill | 高概率一致 |
| 对依赖上述高级机制的 skill | 需要继续补齐，否则会退化、BLOCKED 或行为不一致 |

## 本轮实际验证

本轮先执行了基础验证：

```powershell
python -B -m compileall .\codex-skill-runtime-core
openspec validate codex-runtime-equivalence --strict
python -B .\codex-skill-runtime-core\core_cli.py selftest
```

结果：

```text
compileall: 通过
openspec validate: Change 'codex-runtime-equivalence' is valid
selftest: SELFTEST_SUMMARY total=18 failed=0
```

随后继续执行带 Godot、live strict、live QA 的完整验证：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py selftest --live-strict-target README.md --live-qa-target <project-path>
```

结果：

```text
PASS: loader-discovery - skills=73 agents=49
PASS: frontmatter-contract - prototype/team-qa/qa-tester frontmatter matched expected routing
PASS: task-and-gate-contract - Task parser and QA gate reject weak QA output
PASS: codex-dry-run-contract - session=20260523-144436-prototype
PASS: strict-dry-run-contract - session=20260523-144451-strict-prototype
PASS: tool-executor-contract - session=20260523-144505-selftest-tools
PASS: permission-contract - session=20260523-144514-selftest-permissions
PASS: command-preprocessing-contract - session=20260523-144514-selftest-command-preprocess
PASS: plugin-manifest-contract - session=20260523-144514-selftest-plugin-manifest
PASS: hook-decision-contract - session=20260523-144515-selftest-hook-decisions
PASS: external-layout-contract - external command/plugin/fork contracts matched
PASS: mcp-bridge-contract - session=20260523-144524-selftest-mcp
PASS: memory-compaction-contract - session=20260523-144534-selftest-memory
PASS: hook-shim-contract - session=20260523-144534-selftest-hook
PASS: godot-contract - session=20260523-144535-godot-smoke
PASS: live-strict-contract - session=20260523-144535-strict-smoke
PASS: live-codex-qa-contract - session=20260523-144858-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=18 failed=0
```

所以本报告的“CCGS 核心执行闭环基本一致”不是只靠普通 selftest 推断，而是包含本轮真实 Godot、live strict 和真实 `qa-tester` gate 的完整自测结果。

## 对照方法

本报告没有复制参考实现的私有源码逻辑，只做机制级白盒对照。对照依据来自：

- 参考实现文档：`<reference-project>\docs\02-kairos.md`、`04-coordinator.md`、`05-hidden-commands.md`、`06-bridge.md`、`07-feature-gates.md`
- 参考实现源码索引：`src/main.tsx`、`src/commands.ts`、`src/bootstrap/state.ts`、`src/services/mcp/*`、`src/services/compact/*`、`src/tools/AgentTool/*`、`src/utils/toolResultStorage.ts`、`src/query/tokenBudget.ts`
- 当前 runtime 源码：`runtime.py`、`action_loop.py`、`tool_executor.py`、`hooks.py`、`loaders.py`、`mcp.py`、`memory.py`、`prompts.py`、`session.py`

判定等级：

| 等级 | 含义 |
|---|---|
| 等同 | 执行效果、输入输出、阻断点和证据基本可视为一致 |
| 基本一致 | 常见路径一致，但高级边界或内部优化不同 |
| 部分一致 | 有同名或相似机制，但关键语义缺失 |
| 不一致 | 机制目标或执行效果明显不同 |
| 非目标 | 对 CCGS 当前执行效果不关键，且用户此前明确可不追求 UI/marketplace 等全量一致 |

## 证据路径索引

下面这张表用于把“机制判断”追到具体文件。它不是源码复述，只标明本轮判断依据来自哪里。

| 机制 | 参考实现证据 | 当前 runtime 证据 | 本报告重点判断 |
|---|---|---|---|
| CLI/主入口 | `<reference-project>\src\main.tsx`、`src\commands.ts` | `codex-skill-runtime-core/runtime/cli.py`、`runtime.py` | 当前是 adapter，不是完整客户端 |
| Feature gate | `docs\05-hidden-commands.md`、`docs\07-feature-gates.md`、`src\commands.ts` | 无同等 GrowthBook/compile gate | 不一致 |
| Skill/command 发现 | `src\skills\loadSkillsDir.ts`、`src\commands.ts` | `runtime/loaders.py` | 基础布局基本一致，优先级不保证完全一致 |
| Agent 发现与定义 | `src\tools\AgentTool\loadAgentsDir.ts` | `runtime/loaders.py`、`runtime/runtime.py` | agent 文件加载可用，frontmatter 高级字段不足 |
| Agent frontmatter MCP/skills/memory | `src\tools\AgentTool\runAgent.ts`、`agentMemory.ts` | `runtime/runtime.py`、`runtime/prompts.py`、`runtime/mcp.py` | 部分一致，关键高级语义缺失 |
| Prompt 参数渲染 | `src\commands.ts` 相关命令机制、公开 plugin/skill 样本 | `runtime/prompts.py` | 常见参数、文件引用、plugin root 可用 |
| Tool loop | `src\query.ts`、`src\QueryEngine.ts`、`src\Tool.ts` | `runtime/action_loop.py`、`runtime/tool_executor.py` | 主流程基本一致，子代理内部不完整 |
| 文件/Bash/Web/Todo/Skill 工具 | `src\tools\*` | `runtime/tool_executor.py` | 常见工具覆盖，非全工具集 |
| 权限 | `src\hooks\toolPermission\PermissionContext.ts`、`src\utils\swarm\permissionSync.ts` | `runtime/hooks.py`、`runtime/tool_executor.py` | settings/hook 基础可用，UI/managed/swarm 不完整 |
| Hooks | `src\schemas\hooks.ts`、`src\utils\hooks*`、`src\components\hooks\*` | `runtime/hooks.py` | command/prompt/skill hook 部分一致 |
| Task/Agent 子代理 | `src\tools\AgentTool\runAgent.ts`、`resumeAgent.ts` | `runtime/runtime.py`、`runtime/prompts.py` | 能启动独立子代理，不等价完整 AgentTool |
| Coordinator | `docs\04-coordinator.md`、`src\coordinator\coordinatorMode.ts`、`src\tools\SendMessageTool\*`、`src\tools\TaskStopTool\*` | 只有 `runtime/action_loop.py` 的 Task 并发 | 不一致 |
| MCP 发现 | `src\services\mcp\utils.ts`、`src\tools\AgentTool\runAgent.ts` | `runtime/mcp.py` | project/plugin MCP 可用，agent frontmatter MCP 缺失 |
| MCP transport | `src\services\mcp\client.ts`、`headersHelper.ts` | `runtime/mcp.py` | stdio/HTTP/SSE/WS 部分一致 |
| MCP OAuth/XAA | `src\services\mcp\client.ts`、`xaa.ts`、`xaaIdpLogin.ts` | `runtime/mcp.py` | 不一致，缺完整授权/刷新生命周期 |
| Session state/resume | `src\bootstrap\state.ts`、`src\assistant\sessionHistory.ts`、`src\main.tsx` | `runtime/session.py`、`runtime/memory.py` | session evidence 有，resume 不等价 |
| Compact/memory | `src\services\compact\*`、`src\utils\toolResultStorage.ts` | `runtime/memory.py` | summary 近似，不等价 compact 系统 |
| Token budget | `src\query\tokenBudget.ts`、`src\utils\tokenBudget.ts` | 无同等模块 | 不一致 |
| KAIROS/Proactive/Cron/Dream | `docs\02-kairos.md`、`src\assistant\*`、`src\proactive\*`、`src\services\autoDream\*` | 无同等模块 | 不一致 |
| Bridge | `docs\06-bridge.md`、`src\remote\*`、`src\server\*` | 无同等模块 | 非目标且不一致 |
| Plugin lifecycle | `src\commands\plugin\*`、`src\plugins\*` | `runtime/loaders.py` | 本地加载部分一致，安装生命周期不一致 |
| CCGS Godot/QA gate | CCGS 原 skill/agent、测试框架 | `runtime/state_machines.py`、`runtime/gates.py`、`runtime/godot.py` | CCGS 核心闭环基本一致 |

## 机制逐项对照

### 1. 入口与运行模型

参考实现：

- `main.tsx` 是完整 CLI/REPL/print/daemon/remote/session 恢复入口。
- 同一客户端内承载交互 UI、工具调度、会话状态、插件、MCP、远程控制、长期任务。

当前 runtime：

- `core_cli.py` / `runtime/cli.py` 是轻量命令入口。
- `CodexSkillRuntime` 负责任务装配，Codex CLI 作为“大脑”，Python runtime 执行部分副作用。

判断：部分一致。

原因：普通 skill 执行路径一致，但参考客户端是一整套交互式应用，当前是外层 adapter，不是完整客户端。

### 2. Feature gate 与隐藏命令

参考实现：

- `commands.ts` 和 `main.tsx` 大量使用 `feature(...)`、`USER_TYPE === 'ant'`、GrowthBook 远程开关。
- KAIROS、Bridge、Coordinator、Proactive、Voice、Ultraplan、Fork、Peers 等命令都受 gate 控制。

当前 runtime：

- 没有复刻编译期开关、用户类型、GrowthBook。
- 只按本地文件实际存在与 CLI 参数显式执行。

判断：不一致，但对本地 skill 执行通常不是 blocker。

风险：如果某个 skill/command 依赖 feature-gated 命令是否存在、内部用户能力或 GrowthBook 开关，当前 runtime 不会给出同样的可见性与策略。

### 3. Skill/Command/Agent 发现

参考实现：

- 支持 `.claude/skills`、`.claude/commands`、用户/项目/策略/插件来源、内置 skill、MCP skill、命名空间 command。
- Agent 支持 built-in/custom/plugin，多来源优先级。

当前 runtime：

- `loaders.py` 支持 `.claude/skills`、根 `skills/`、递归 `SKILL.md`、`.claude/commands`、根 `commands/`、插件 commands/skills、`.claude/agents`、根 `agents/`、插件 agents。
- 支持 `.claude-plugin/plugin.json` 的基础 namespace。
- 缺 agent 文件时生成 synthetic agent。

判断：基本一致。

边界：

- 没有完整用户全局目录、策略目录、内置命令 registry、MCP 动态 skill 搜索。
- 优先级与参考实现不保证完全一致。

### 4. Frontmatter 解析

参考实现：

- Skill/agent frontmatter 支持 `description`、`tools`、`model`、`effort`、`color`、`memory`、`skills`、`mcpServers`、`hooks`、`permissionMode` 等字段。

当前 runtime：

- `frontmatter.py` 是简单 YAML-like parser。
- 常用 `name`、`description`、`agent`、`allowed-tools`、`context: fork` 等能工作。
- Agent 的 `mcpServers`、`skills`、`memory`、`hooks` 不是完整语义实现。

判断：部分一致。

风险：公开 skill 如果只靠普通 frontmatter，可以运行；如果 agent frontmatter 声明了专属 MCP、预加载 skills 或 memory，当前执行效果会缺失。

### 5. Prompt 渲染与参数替换

参考实现：

- 支持 `$ARGUMENTS`、参数位置、文件引用、动态 shell 上下文、CLAUDE.md 注入、hook additional context、skill loading metadata。

当前 runtime：

- `prompts.py` 支持 `$ARGUMENTS`、`$ARGUMENTS[index]`、`$1/$2`、`@file`、`@${CLAUDE_PLUGIN_ROOT}/file`、动态命令上下文、supporting file manifest。
- 会把 runtime memory 注入 prompt。

判断：基本一致。

边界：

- 没有完整 CLAUDE.md 外部 include 安全交互。
- 没有参考客户端的全部上下文层级、IDE selection、消息附件类型、hook attachment 细粒度行为。

### 6. Strict action-loop

参考实现：

- 模型原生产生 tool_use，客户端逐个调度工具，结果回填下一轮上下文。
- 支持并发工具、权限、hooks、流式响应、中断、压缩、resume。

当前 runtime：

- `action_loop.py` 要求 Codex 返回 JSON：`action_required | final | blocked`。
- Runtime 执行 actions，再把 observation 交回 Codex。
- `--output-schema` 失败时降级 prompt-only JSON。
- 多个 Task/Agent action 可并发，最大 4。

判断：基本一致，用于主流程足够；不是原生 tool_use 完全一致。

风险：

- JSON fallback 依赖模型遵守格式。
- 子代理内部不是 strict action-loop，而是普通 Codex exec。
- 不能拦截 Codex 子进程内部每一个原生工具事件。

### 7. Runtime tool suite

参考实现：

- 工具非常多：Read/Write/Edit/MultiEdit/Bash/Glob/Grep/TodoWrite/WebFetch/WebSearch/Task/Agent/Skill/MCP/AskUserQuestion/Notebook/IDE/特殊工具等。

当前 runtime：

- `tool_executor.py` 支持 read_file、glob、grep、write_file、edit_file、multi_edit、bash、task/agent、ask_user_question、todo_write、skill、web_fetch、web_search、godot_smoke、mcp。

判断：基本一致，覆盖普通 coding/game skill；非全工具集。

风险：

- 不支持 Notebook、IDE diff、特殊内部工具、Computer Use、native UI 工具。
- web_fetch/web_search 是轻量兼容，不等价参考客户端完整联网工具。

### 8. 权限模型

参考实现：

- 有 settings allow/ask/deny、permission UI、classifier、managed policy、远程权限、swarm 权限同步、hook permissionDecision、updatedInput。

当前 runtime：

- 支持 settings allow/ask/deny 的基础匹配。
- `--assume-yes` 可自动通过 ask。
- 支持 `.claude` 写保护。
- 支持 hook 的 permissionDecision 与 updatedInput。

判断：部分一致。

风险：

- 没有真实交互式 permission UI。
- 没有 managed policy、classifier、远程审批、worker badge、swarm 权限同步。
- `ask` 在自动模式下会按 assume_yes 退化，不等价用户逐项选择。

### 9. Hooks

参考实现：

- 支持 SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、Stop、SubagentStart、SubagentStop 等事件。
- 支持 matcher、command hook、prompt hook、skill hook、函数 hook、策略禁用、additional context、阻断、修改输入。

当前 runtime：

- `hooks.py` 支持 settings 与插件 hook 合并。
- 支持 command/prompt/skill hook 的基础行为。
- 支持 matcher、exit code 2、`continue:false`、`decision:block|deny`、`permissionDecision`、`updatedInput`。
- Runtime 主流程触发 UserPromptSubmit、SessionStart、SessionEnd、SubagentStart、SubagentStop、PreToolUse、PostToolUse。

判断：基本一致到部分一致之间。

关键边界：

- 函数 hook、policy 层级、hook UI、deferred hook message、所有 payload 字段并不完整。
- 子代理内部原生 Codex 工具调用不被 runtime 的 PreToolUse/PostToolUse 逐项拦截。

### 10. Task/Agent 子代理

参考实现：

- AgentTool 使用同一套 query engine，能 fork context，能继承/过滤工具，能接入 agent frontmatter MCP，能触发 Subagent hooks，能记录 transcript，能 resume。

当前 runtime：

- `Task`/`Agent` action 会启动独立 Codex session。
- 能加载原始 agent 或 synthetic agent。
- 能触发 SubagentStart/SubagentStop。
- CCGS QA tester 已能作为独立子代理执行。

判断：部分一致。

关键差距：

- 子代理不是 runtime strict loop，内部工具行为不可逐项监督。
- 没有 AgentTool resume、agent memory snapshot、frontmatter skills preload、frontmatter MCP servers。
- 没有 SendMessage/TaskStop 续跑控制。

### 11. Coordinator / Worker 编排

参考实现：

- Coordinator 模式让主 Claude 只做指挥，通过 Agent、SendMessage、TaskStop 调度 Worker。
- Worker 可异步并行，支持 scratchpad，支持继续已有 worker，支持停止错误方向。

当前 runtime：

- 只有普通 task/agent 并发启动。
- 没有 Coordinator mode，不支持 SendMessage 到已有 worker，不支持 TaskStop，不支持 scratchpad 语义。

判断：不一致。

风险：如果一人公司 skill 或编程 skill 依赖持续 worker、复用 worker 上下文、跨 worker scratchpad，当前 runtime 不能 100% 复刻。

### 12. MCP 发现

参考实现：

- 项目、插件、agent frontmatter、内置连接器、claude.ai proxy、IDE/Chrome/Computer Use 等都可形成 MCP client。

当前 runtime：

- `mcp.py` 发现项目 `.mcp.json`、插件根 `.mcp.json`、插件 manifest 内联或路径型 `mcpServers`。
- 支持 `mcp__server__tool` 风格调用。

判断：部分一致。

关键差距：

- 当前未发现 agent frontmatter 的 `mcpServers`。
- 没有内置 claude.ai connector、IDE connector、Chrome/Computer Use 这类 in-process MCP。

### 13. MCP transport

参考实现：

- 使用 MCP SDK 的 stdio、SSE、Streamable HTTP、WebSocket transport。
- 处理 Accept header、SSE 重连、session 过期、错误分类、工具/资源/prompt fetch、elicitation。

当前 runtime：

- 支持 stdio JSON-RPC。
- 支持 HTTP JSON-RPC POST，带 `Accept: application/json, text/event-stream` 和 `Mcp-Session-Id`。
- 支持 SSE endpoint/message 模式。
- WebSocket 依赖 Python `websocket-client`，缺包时 BLOCKED。
- 支持 headers 和 headersHelper。

判断：部分一致到基本一致之间，取决于 server 复杂度。

风险：

- 不具备 SDK 级重连、elicitation、资源、prompts、复杂错误恢复。
- 对严格 MCP server 可能不完全兼容。

### 14. MCP OAuth / XAA

参考实现：

- 支持 OAuth token 检查、401 后刷新、auth provider、McpAuthTool、XAA id_token -> access_token 流程、keychain 缓存、刷新锁。

当前 runtime：

- 不内置浏览器 OAuth、动态客户端注册、token 刷新、XAA。
- 401/403 时提示配置 headers、headersHelper 或 token/OAuth 结果。

判断：不一致。

风险：任何需要自动 OAuth 的远程 MCP skill 会 BLOCKED，不能达到 Claude Code 原生效果。

### 15. Session state 与 resume

参考实现：

- `bootstrap/state.ts` 维护 sessionId、parentSessionId、sessionProjectDir、invokedSkills、sessionCronTasks、prompt cache 状态、post-compaction 标记等。
- `main.tsx` 有大量 `--continue`、`--resume`、teleport/remote 逻辑。

当前 runtime：

- 每次运行创建 `.codex-skill-runtime/sessions/<id>/`。
- 写 `events.jsonl`、prompt、stdout/stderr、tools、strict-result、summary。
- 有 sessions-index 和 bounded runtime memory。

判断：部分一致。

差距：

- 没有真实 transcript resume。
- 没有继续同一个模型对话/worker 的完整历史。
- 没有 session mode 恢复、parent lineage、remote history API。

### 16. Compact / Memory

参考实现：

- 有 compact prompt、partial compact、session memory compact、microcompact、tool result budget、large tool result 持久化、invoked skill preservation、post-compact cache 标记。

当前 runtime：

- session 结束写 `summary.json`。
- 更新 `.codex-skill-runtime/sessions-index.json`。
- 后续 prompt 注入 `Runtime Memory / Compacted Session Context`。

判断：部分一致。

风险：

- 这是“可观察近似”，不是同等压缩算法。
- 不会根据 token 预算自动压缩当前对话。
- 不会保留 exact post-compaction message set。
- 不会做 large tool result budget 替换。

### 17. Token budget 与自动续跑

参考实现：

- 能解析用户的 token budget，接近预算时决定继续或停止，并生成 continuation nudge。

当前 runtime：

- 未实现等价 token budget tracker。

判断：不一致。

风险：超长任务中，Claude Code 可能自动继续压榨预算；当前 runtime 可能提前结束、丢上下文或依赖 Codex 单轮能力。

### 18. Large tool result persistence

参考实现：

- 大工具结果会持久化到 session 工具结果目录，正文替换成 preview/tag，并保证 resume 后替换决策稳定。

当前 runtime：

- 工具结果写 JSON 到 session，但没有同等预算替换和稳定 wire prefix 策略。

判断：部分一致。

风险：大 grep、大日志、大测试输出可能让后续 prompt 变胖或丢失细节，和参考客户端不同。

### 19. KAIROS / Proactive / Cron / Dream

参考实现：

- 支持长期后台助手、scheduled_tasks、permanent cron、proactive tick、SleepTool、AutoDream 记忆整合、每日日志、锁机制。

当前 runtime：

- 没有常驻 daemon。
- 没有 proactive tick。
- 没有 scheduled_tasks 监视器。
- 没有 AutoDream。

判断：不一致。

风险：依赖长期自主运行、定时任务、自动记忆整理的 skill 不会等价。

### 20. Bridge 远程遥控

参考实现：

- 支持 claude.ai/Web/手机远程控制本地 CLI，SSE/WebSocket/HTTP 双向通道，远程权限审批，JWT 刷新，崩溃恢复 pointer。

当前 runtime：

- 无 Bridge。

判断：非目标且不一致。

说明：用户此前表示 UI 体验不同可忽略；如果 skill 执行不依赖远程遥控，影响较小。

### 21. Plugin / Marketplace 生命周期

参考实现：

- 有插件安装、信任、启用/禁用、marketplace、版本、依赖、通知、配置 UI。

当前 runtime：

- 能加载本地已存在插件布局、manifest、commands、skills、agents、hooks、mcpServers。
- 不负责安装、升级、禁用、marketplace 完整生命周期。

判断：部分一致。

风险：执行本地已下载插件没问题；需要从 marketplace 动态安装、更新、启停时不一致。

### 22. UI / 交互 / Keybindings

参考实现：

- 大量 Ink/React UI：权限弹窗、hook 浏览器、MCP 菜单、agent 编辑器、resume 页面、model picker、diff viewer。

当前 runtime：

- CLI 输出和 session 文件证据。
- `ask_user_question` 在非 `--assume-yes` 时 BLOCKED，在 assume_yes 下自动取默认/第一项。

判断：非目标且部分不一致。

说明：用户明确说交互体验不同不重要；但“需要交互的地方仍然要有交互”。当前交互只是阻断或自动选择，不是等价 UI。

### 23. Godot / CCGS QA gate

参考实现：

- 原 CCGS skill 要求 QA tester 等 agent 真实参与。

当前 runtime：

- `/prototype` workflow plan 中保留 required QA。
- `qa-tester` 子代理会真实运行。
- gate 要求 `VERDICT` 和 `EVIDENCE MATRIX`。
- `godot_smoke` 能运行 Godot headless 和 `scripts/gameplay_test.gd`。

判断：基本一致。

边界：这是 CCGS 特化能力，不代表所有 Claude Code skill 的通用机制都已还原。

## 20 轮攻击式审查

### 第 1 轮：把“普通 selftest 通过”攻击成假阳性

攻击问题：本轮 `selftest` 没带 Godot/live 参数，Godot/live QA 被 SKIP，是否说明没测到关键执行效果？

审查结果：这个攻击在第一次普通 selftest 后成立一半，但随后已被完整 live selftest 反证。本轮追加执行了带 Godot、live strict、live QA 的完整命令，Godot smoke、strict smoke 和真实 `qa-tester` gate 全部 PASS。

判定：PASS。报告仍保留这个攻击项，因为它说明不能用普通 selftest 偷换完整 live selftest。

### 第 2 轮：攻击 loader 是否仍然只支持 CCGS

攻击问题：是否硬编码 `.claude/skills` 导致只能跑 CCGS？

审查结果：不成立。`loaders.py` 已支持根 `skills/`、递归 `SKILL.md`、commands、plugins、root agents 等。历史外部仓库测试覆盖 superpowers、Go skills、Sentry、Daymade、the-startup、arc 等。

判定：PASS。

### 第 3 轮：攻击是否仍然存在 CCGS 特化

攻击问题：是否为了 CCGS 写死 `/prototype` 和 QA？

审查结果：成立。`state_machines.py` 对 `/prototype`、`/team-qa` 有 CCGS 专用 workflow/gate。它是为了还原 CCGS 执行效果保留的特化，不应被误称为通用 Claude Code runtime。

判定：WARN。可接受，但必须明示。

### 第 4 轮：攻击 frontmatter 覆盖是否完整

攻击问题：agent frontmatter 中的 `mcpServers`、`skills`、`memory`、`hooks` 是否完整生效？

审查结果：不完整。参考实现的 AgentTool 会处理 agent-specific MCP、skills preload、agent memory、frontmatter hooks。当前 runtime 主要加载 agent body/persona，未完整实现这些字段。

判定：FAIL。依赖这些字段的 skill 执行效果不等价。

### 第 5 轮：攻击参数与文件引用处理

攻击问题：`$ARGUMENTS`、`$1`、`@file`、plugin root 动态引用是否能工作？

审查结果：基本能。`prompts.py` 与 selftest 覆盖这些形式。缺口是参考客户端还有更复杂附件、CLAUDE.md include、安全弹窗和上下文层级。

判定：PASS/WARN。

### 第 6 轮：攻击 strict action-loop 是否能约束所有工具

攻击问题：主流程 strict，但子代理是否也被 runtime 工具层约束？

审查结果：不完整。主流程 action 由 `ToolExecutor` 执行；但 `_run_agent_task` 启动普通 Codex exec，子代理内部原生工具不逐项经过 runtime PreToolUse/PostToolUse。

判定：FAIL。对 CCGS QA 够用，但对“100% 机制复刻”不够。

### 第 7 轮：攻击 Task 并发与 worker 生命周期

攻击问题：是否等价 Coordinator/worker 的并行、继续、停止？

审查结果：不等价。当前只在同一轮多个 Task action 时并发启动，缺 SendMessage、TaskStop、长期 worker、scratchpad、worker resume。

判定：FAIL。

### 第 8 轮：攻击权限 ask 语义

攻击问题：`ask` 是否真正暂停等待用户交互？

审查结果：部分。非 assume_yes 时可 BLOCKED，assume_yes 时自动通过。没有参考客户端的交互式权限 UI、选项、managed policy、classifier、远程审批。

判定：WARN/FAIL，取决于 skill 是否依赖人工审批。

### 第 9 轮：攻击 hook 事件完整性

攻击问题：hook 事件是否与参考实现完全一致？

审查结果：常见事件可用，但不完整。SessionStart/UserPromptSubmit/SessionEnd/SubagentStart/SubagentStop/PreToolUse/PostToolUse 有实现；函数 hook、policy disable、所有 payload 字段、deferred hook message 不完整。

判定：WARN。

### 第 10 轮：攻击 hook updatedInput 语义

攻击问题：hook 改写工具输入后是否重新检查权限？

审查结果：当前 runtime 会应用 `updatedInput` 并重新做 permission decision。这个关键点与参考效果接近。

判定：PASS。

### 第 11 轮：攻击 MCP transport

攻击问题：HTTP/SSE/WebSocket 是否足以等价 MCP SDK？

审查结果：不完全。当前有真实 bridge，但不是 SDK 完整实现，缺 reconnect、elicitation、资源/prompt fetch、复杂 error lifecycle。

判定：WARN。

### 第 12 轮：攻击 MCP OAuth

攻击问题：远程 MCP 需要 OAuth 时能否像 Claude Code 一样自动授权/刷新？

审查结果：不能。当前只接受 headers/token/headersHelper 已准备好的结果，401/403 明确 BLOCKED。参考实现有 OAuth/XAA/keychain/刷新锁/AuthTool。

判定：FAIL。需要 OAuth 的远程 MCP skill 不等价。

### 第 13 轮：攻击 agent frontmatter MCP

攻击问题：某个 agent 自带 `mcpServers`，当前 runtime 是否会在该 agent 运行时附加工具？

审查结果：未发现等价实现。`mcp.py` 发现 project/plugin MCP；`runtime.py` 加载 agent 但不把 agent frontmatter MCP 合并进当前 ToolExecutor。

判定：FAIL。

### 第 14 轮：攻击 session resume

攻击问题：能否从已有 transcript 恢复同一会话继续执行？

审查结果：不能。当前有 summary/index 近似记忆，但没有 transcript resume、session mode 恢复、worker resume。

判定：FAIL。

### 第 15 轮：攻击 compact/memory

攻击问题：当前 memory 是否等价参考实现的 compact 和 session memory？

审查结果：不等价。当前是 session summary 注入；参考实现有 compact prompt、partial compact、microcompact、session memory compact、tool result replacement、invoked skill preservation。

判定：FAIL，但对短任务可接受。

### 第 16 轮：攻击大输出处理

攻击问题：测试日志、grep 输出很大时是否会像参考实现一样稳定持久化并替换正文？

审查结果：不完整。当前写 tool JSON，但没有稳定 replacement decision、preview tag、resume 重构。

判定：WARN/FAIL。

### 第 17 轮：攻击 token budget 自动续跑

攻击问题：用户要求 `+50k` 或 “spend 30k tokens” 时是否有预算 tracker？

审查结果：没有等价机制。

判定：FAIL。

### 第 18 轮：攻击 KAIROS/Proactive 长期自主执行

攻击问题：skill 依赖定时任务、proactive tick、Dream memory 时是否等价？

审查结果：不等价。当前 runtime 是一次性命令执行，不是常驻助手。

判定：FAIL。

### 第 19 轮：攻击 Bridge/远程权限

攻击问题：远程 claude.ai 控制、本地执行、远程权限审批是否存在？

审查结果：不存在。用户此前允许忽略 UI/远程交互体验，但机制本身不等价。

判定：非目标/FAIL。

### 第 20 轮：攻击“执行效果 100%”这个总目标

攻击问题：是否可以诚实宣称当前已对 `<reference-project>` 达到执行效果层面 100% 还原？

审查结果：不能。可以说“对 CCGS 核心 skill 流程和已测公开 skill 的常见路径，执行效果基本一致”；不能说“对参考客户端全部机制 100% 一致”。

判定：FAIL 100% 全量宣称，PASS 有边界的基本一致宣称。

## 风险优先级

### P0：会直接破坏执行效果一致的缺口

1. 子代理内部不是 strict runtime tool loop，无法完整拦截工具、权限、hooks。
2. agent frontmatter 的 `mcpServers`、`skills`、`memory`、`hooks` 未完整实现。
3. 远程 MCP OAuth/XAA/刷新生命周期缺失。
4. session resume / worker resume 缺失。
5. Coordinator 的 SendMessage/TaskStop/scratchpad 缺失。

### P1：长任务中会放大差异的缺口

1. compact/microcompact/session memory 不等价。
2. large tool result persistence 不等价。
3. token budget tracker 不存在。
4. prompt cache/session-stable cache 近似而非等价。

### P2：对 CCGS 当前目标影响较低，但对完整客户端不一致

1. Bridge remote control。
2. KAIROS/proactive/cron/dream。
3. marketplace 安装/升级/启停生命周期。
4. UI、keybindings、菜单、模型切换等体验层机制。

## 剩余缺口对执行效果的影响

| 缺口 | 对 CCGS Godot skill 的影响 | 对普通公开 skill 的影响 | 对完整参考客户端复刻的影响 |
|---|---|---|---|
| 子代理内部不是 strict loop | 中等：QA 子代理可跑，但内部工具不可逐项监督 | 高：复杂 agent 可能绕过 runtime hook/permission | 极高 |
| agent frontmatter `mcpServers` | 低到中：当前 CCGS agent 未依赖 | 高：agent 专属 MCP 会缺工具 | 高 |
| agent frontmatter `skills` preload | 低到中 | 中到高：依赖预加载技能的 agent 行为会变弱 | 高 |
| agent memory | 低 | 中：长期专用 agent 会失忆 | 高 |
| MCP OAuth/XAA | 低，除非使用远程 MCP | 高：远程企业 MCP 直接 BLOCKED | 极高 |
| SendMessage/TaskStop/Coordinator | 中：团队式 QA 编排会退化 | 高：一人公司/编程团队 skill 可能依赖 | 极高 |
| session resume | 中：长任务中断后不能原样继续 | 高 | 极高 |
| compact/microcompact | 中：长 Godot 项目会受影响 | 高 | 极高 |
| large tool result persistence | 中：测试日志大时受影响 | 高 | 高 |
| token budget | 低到中 | 中 | 中 |
| KAIROS/Cron/Dream | 低：当前一次性游戏 skill 不依赖 | 中：长期自动化 skill 受影响 | 极高 |
| Bridge/UI | 低，用户已说 UI 可忽略 | 低到中：需要人工审批时会受影响 | 高 |
| marketplace lifecycle | 低：本地仓库已存在 | 中：需要安装/升级 skill 时受影响 | 高 |

## 是否“机制上基本一致”

必须分范围回答：

1. 对 CCGS 这套游戏开发 skill 的核心执行机制：基本一致。
2. 对普通公开 Claude Code skill 的基础执行：基本一致到部分一致，取决于是否使用高级 frontmatter、OAuth MCP、长期会话或 worker 续跑。
3. 对 `<reference-project>` 体现的完整 Claude Code 客户端机制：不基本一致，只是 adapter 级部分一致。
4. 对“无条件 100% 还原执行效果”：当前不能成立。

## 下一步建议

如果目标仍然是尽可能逼近“执行效果 100%”，建议按以下顺序补齐，而不是继续改 CCGS 原始 skill：

1. 把子代理也改成 strict action-loop，让 Task/Agent 内部工具同样经过 runtime ToolExecutor。
2. 实现 agent frontmatter `mcpServers`、`skills` preload、`memory` prompt 注入、frontmatter hooks。
3. 增加 SendMessage、TaskStop、worker registry、scratchpad，形成简化 Coordinator。
4. 增加 transcript resume：至少能从 `.codex-skill-runtime/sessions/<id>/` 恢复主 prompt、工具结果、summary 和 worker 状态。
5. 对远程 MCP 增加 OAuth provider 抽象：不一定复刻 Claude UI，但要能外接 token store、刷新器、auth command。
6. 增加 large tool result budget：大输出写文件，prompt 中放 preview 和可读路径。
7. 增加 token budget tracker 与 continuation nudge。
8. 针对 3 到 5 个依赖高级机制的公开 skill 建立回归测试，而不只测 CCGS。

当前状态可以继续用于 CCGS Godot skill 迁移验证，但不能对外宣称“完整 Claude Code 机制 100% 复刻”。
