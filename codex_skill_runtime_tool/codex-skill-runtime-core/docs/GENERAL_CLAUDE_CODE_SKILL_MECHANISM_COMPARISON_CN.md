# 通用 Claude Code Skill 执行机制对照：当前 Codex Runtime vs `<reference-project>`

生成时间：2026-05-23  
对照对象：`<reference-project>`  
当前代码：`<skill-repo-root>\codex-skill-runtime-core`

## 这篇文档的范围

这篇文档不再把 CCGS 当成目标。CCGS 只是一组验证用 skill。真正要回答的是：

1. 当前 runtime 与 `<reference-project>` 在机制上有哪些相同点。
2. 有哪些不同点。
3. 哪些不同点会影响普通 Claude Code skill 的执行效果。
4. 哪些只是 UI、安装、遥测、内部开关差异，通常不影响一个本地 skill 的核心产出。

下面所有判断都按“通用 Claude Code skill 能否在 Codex runtime 中执行出相同效果”来衡量。

## 本轮核对的源码范围

这份对照不是按 CCGS 的验证结果倒推，而是按 `<reference-project>` 中的机制源码逐项检查。重点核对了这些路径：

- 参考入口与主循环：`src/main.tsx`、`src/cli/print.ts`、`src/query.ts`、`src/QueryEngine.ts`、`src/bootstrap/state.ts`。
- 参考 skill/command/plugin 加载：`src/commands.ts`、`src/skills/loadSkillsDir.ts`、`src/skills/bundledSkills.ts`、`src/skills/mcpSkills.ts`、`src/plugins/`、`src/commands/plugin/`。
- 参考 agent/coordinator：`src/tools/AgentTool/`、`src/tools/AgentTool/loadAgentsDir.ts`、`src/tools/AgentTool/runAgent.ts`、`src/tools/AgentTool/resumeAgent.ts`、`src/tools/AgentTool/agentMemory.ts`、`src/coordinator/`。
- 参考 MCP：`src/services/mcp/client.ts`、`src/services/mcp/headersHelper.ts`、`src/services/mcp/xaa.ts`、`src/services/mcp/xaaIdpLogin.ts`。
- 参考上下文/压缩/缓存：`src/services/compact/`、`src/utils/toolResultStorage.ts`、`src/query/tokenBudget.ts`、`src/utils/context.ts`、`src/utils/claudemd.ts`。
- 参考长期/远程机制：`docs/02-kairos.md`、`docs/04-coordinator.md`、`docs/05-hidden-commands.md`、`docs/06-bridge.md`、`src/bridge/`、`src/utils/cron*.ts`、`src/services/autoDream/`。
- 当前 runtime：`core_cli.py`、`runtime/cli.py`、`runtime/runtime.py`、`runtime/loaders.py`、`runtime/prompts.py`、`runtime/action_loop.py`、`runtime/tool_executor.py`、`runtime/hooks.py`、`runtime/mcp.py`、`runtime/memory.py`、`runtime/session.py`。

## 总体结论

当前 runtime 是一个“Claude Code skill 执行适配层”，不是完整 Claude Code 客户端复刻。它已经覆盖了普通 skill 执行最关键的一批机制：

- 发现 skill、command、agent、plugin。
- 渲染 skill prompt、参数、文件引用和 plugin root。
- 调用 Codex CLI 做推理。
- 用 strict action-loop 让主流程工具调用交给 runtime 执行。
- 执行常见 Read/Write/Edit/MultiEdit/Bash/Glob/Grep/Todo/Skill/Agent/MCP/Web 类动作。
- 触发一部分 Claude-like hooks。
- 写 session evidence。
- 提供 lightweight memory summary。

但它没有完整实现参考客户端的这些机制：

- 原生 tool_use 流式循环。
- 隐藏 system prompt、输出风格、模式附件、模型/effort 选择链路。
- 子代理内部同样受 runtime 工具层控制。
- Skill/command frontmatter 的完整语义：`paths` 条件激活、`user-invocable`、`disable-model-invocation`、`arguments`、`argument-hint`、`shell`、`model`、`effort`。
- Agent frontmatter 的完整语义：`mcpServers`、`skills` preload、`memory`、`permissionMode`、frontmatter hooks。
- Coordinator/Worker 的 SendMessage、TaskStop、scratchpad、worker resume。
- 远程 MCP OAuth/XAA/token refresh/elicitation/reconnect。
- transcript resume、session mode 恢复、完整 compact/microcompact、large tool result replacement。
- bundled skills、MCP skills、动态 skill 发现/缓存失效。
- settings/config 的 user/project/managed/policy/add-dir/bare/plugin-only 分层。
- KAIROS、proactive、cron、Dream。
- Bridge 远程控制。
- marketplace/plugin 安装、启用、升级生命周期。
- UI 权限审批和 managed policy。

所以：对“普通、一次性、本地执行、少依赖高级 Agent/MCP/session 的 skill”，当前 runtime 已经接近执行效果一致；对依赖高级 Claude Code 客户端机制的 skill，当前 runtime 只能部分一致，甚至会 BLOCKED 或退化。

## 影响等级说明

| 影响等级 | 含义 |
|---|---|
| 直接影响 | skill 很可能执行不出来、执行路径不同、产出质量下降或安全边界不同 |
| 条件影响 | 只有 skill 使用该高级机制时才影响 |
| 通常不影响 | 对本地一次性 skill 的核心产出影响较小，多数是 UI、安装、遥测、内部体验差异 |

## 一一机制对照表

| # | 机制 | `<reference-project>` 的机制 | 当前 runtime 的机制 | 相同点 | 不同点 | 是否影响 skill 执行效果 |
|---:|---|---|---|---|---|---|
| 1 | 主入口 | `src/main.tsx` 是完整 CLI/REPL/daemon/remote 入口 | `core_cli.py` + `runtime/cli.py` 是轻量入口 | 都能从命令进入 skill 执行 | 参考是完整客户端，当前是外层适配器 | 条件影响：复杂 REPL/daemon/remote skill 会受影响 |
| 2 | 执行大脑 | 参考客户端直接驱动 Claude 模型和工具循环 | 当前把 Codex CLI 当大脑，Python runtime 管副作用 | 都是“模型推理 + runtime 执行工具” | 模型不同，原生 tool_use 协议不同 | 直接影响：模型行为和工具调用格式不可能逐字一致 |
| 3 | Skill 发现 | 多来源：用户、项目、策略、插件、内置、MCP skill | 支持 `.claude/skills`、根 `skills`、递归 `SKILL.md`、plugin skills | 常见本地 skill 能发现 | 没有完整全局用户目录、策略目录、内置 registry、MCP 动态 skill | 条件影响：依赖全局/策略/内置/MCP skill 时受影响 |
| 4 | Slash command 发现 | `.claude/commands`、插件 commands、内置 commands、feature-gated commands | 支持 `.claude/commands`、根 `commands`、plugin commands | 本地 markdown command 可运行 | 没有内置 command registry 和 feature gate command | 条件影响：依赖内置或隐藏命令时受影响 |
| 5 | Plugin namespace | plugin manifest 决定命名空间、组件和 lifecycle | 读取 `.claude-plugin/plugin.json`，生成 `/plugin:name` | 本地 plugin command/skill/agent 能 namespace 化 | 没有安装、启用/禁用、升级、trust、marketplace lifecycle | 条件影响：本地已下载插件可用，动态安装/升级不一致 |
| 6 | Agent 发现 | built-in/custom/plugin/policy 多来源 agent | `.claude/agents`、根 `agents`、plugin agents；缺失时 synthetic agent | 常见 agent 文件可加载 | 没有完整 built-in/policy 优先级和 agent metadata 生态 | 条件影响 |
| 7 | Agent frontmatter 基础字段 | `description`、`tools`、`model`、`effort`、`color` 等完整解析 | 简单 frontmatter parser，主要使用 name/agent/tools 类字段 | 基础 persona 可注入 | model/effort/color 等多数只作为文本，不驱动 runtime 行为 | 条件影响：agent 选模型/effort 时不一致 |
| 8 | Agent frontmatter `mcpServers` | agent 运行时合并 agent-specific MCP tools | 当前 MCP 只发现 project/plugin 配置，未按 agent frontmatter 合并 | 都有 MCP 能力 | agent 私有 MCP 缺失 | 直接影响：依赖 agent 私有 MCP 的 skill 会缺工具 |
| 9 | Agent frontmatter `skills` | AgentTool 会预加载声明的 skills | 当前没有完整 agent skill preload | 都能通过 Skill action 加载 skill | 不会自动预加载 agent 声明的 skill | 条件影响：依赖预加载上下文时产出变弱 |
| 10 | Agent memory | user/project/local agent memory 自动注入并可写 | 当前只有 runtime session summary，不是 agent memory | 都有“记忆”近似 | 缺 agent 专属长期记忆目录和写入规则 | 条件影响：长期 agent 质量下降 |
| 11 | Agent permissionMode | agent 可覆盖权限模式 | 当前主要使用全局 settings/assume_yes | 都会做权限判断 | agent 级权限模式不完整 | 条件影响 |
| 12 | Skill frontmatter `allowed-tools` | 用作工具权限/提示的一部分，与权限系统协同 | 当前解析为 preapproved tools，真正阻断来自 settings/hook | 都识别 allowed tools | 语义不是完全等价 | 条件影响：依赖精细工具白名单时不一致 |
| 13 | 参数替换 | `$ARGUMENTS`、位置参数、上下文、文件附件等 | 支持 `$ARGUMENTS`、`$ARGUMENTS[index]`、`$1/$2`、`@file`、plugin root | 常见参数能替换 | 附件/IDE selection/复杂上下文不完整 | 条件影响 |
| 14 | 文件引用注入 | Claude Code 能把相关文件内容作为上下文 | 当前 `prompts.py` 对 `@file` 注入文件内容或诊断 | 都能让 skill 看到文件内容 | 安全提示、外部 include UI、附件类型不完整 | 条件影响 |
| 15 | 动态 shell 上下文 | 支持命令动态上下文和 shell snapshot | 当前支持 `` !`command` `` 轻量执行 | 都能注入命令输出 | 没有完整 shell snapshot/cache/sandbox 策略 | 条件影响 |
| 16 | CLAUDE.md / 项目上下文 | 多层 CLAUDE.md、外部 include、session start hook 注入 | 当前 `_context_bundle` 注入部分上下文文件和 memory | 都能给模型项目背景 | 不完整复刻 CLAUDE.md 层级和 include 交互 | 条件影响 |
| 17 | 主工具循环 | 原生 tool_use 流式协议，工具结果回填模型 | strict JSON action-loop，Codex 返回 JSON action，runtime 执行 | 都是“模型请求工具，runtime 执行，再反馈” | JSON 约束是模拟，不是原生 tool_use | 直接影响：格式失败或复杂工具流时可能退化 |
| 18 | schema 约束 | 参考客户端工具 schema 原生约束 | 当前优先 `--output-schema`，失败后 prompt-only JSON fallback | 都试图约束结构 | fallback 依赖模型自觉 | 条件影响 |
| 19 | 并发工具 | 支持多 tool_use 并发和调度 | 当前同一轮多个 Task/Agent action 可并发，普通动作多为顺序 | 都有有限并发 | 并发范围和调度规则不同 | 条件影响：多工具并行 skill 会慢或行为不同 |
| 20 | Read/Glob/Grep | 原生工具 | Python runtime 工具实现 | 常见读搜能工作 | 输出格式、截断、错误细节不同 | 通常影响较小 |
| 21 | Write/Edit/MultiEdit | 原生编辑工具，diff UI、权限、hook | Python runtime 写/替换/多替换 | 文件能修改 | 没有完整 diff UI、冲突处理、精细编辑语义 | 直接影响：复杂编辑可能不如原生稳定 |
| 22 | Bash | 原生 BashTool，权限、sandbox、output handling | `subprocess.run`，settings/hook deny，session 记录 | 能执行命令并记录输出 | sandbox、交互、后台、流式输出、shell 细节不同 | 条件影响到直接影响 |
| 23 | TodoWrite | 原生 todo state | 写 `todos/latest.json` | 模型能表达计划状态 | 没有完整 UI/state integration | 通常影响较小 |
| 24 | AskUserQuestion | 原生权限式用户交互 | 非 assume_yes 时 BLOCKED，assume_yes 时自动选默认/第一项 | 都能形成决策点 | 没有真实交互式问答 UI | 直接影响：需要用户真实选择时不一致 |
| 25 | Skill tool | 原生 Skill tool 可加载 registered skill | 当前 `skill` action 加载同 root skill，`context: fork` 时可 fork | skill 调 skill 可用 | 没有全局 Skill index 和所有 metadata 行为 | 条件影响 |
| 26 | Task/Agent tool | AgentTool 使用同一 query engine，支持 fork/resume/context/tools | 当前启动独立 Codex exec 子会话 | 都能启动独立 agent | 子代理内部不是 strict runtime tool loop，不能完整拦截 | 直接影响：复杂 agent 执行不等价 |
| 27 | 子代理 hooks | SubagentStart/Stop、frontmatter hooks、additional context | 支持 SubagentStart/Stop 基础事件 | 生命周期 hook 有 | frontmatter scoped hooks/additional context 不完整 | 条件影响 |
| 28 | 子代理 resume | Agent 可 resume background transcript | 当前不能 resume 具体 agent | 都有 session evidence | 缺 resume | 直接影响长任务/失败重试 |
| 29 | SendMessage | 可向已有 worker/teammate 发后续消息 | 未实现 | 无 | 无法继续已有 worker 上下文 | 直接影响团队/一人公司类 skill |
| 30 | TaskStop | 可停止方向错误 worker | 未实现 | 无 | 无法中断错误 worker | 条件影响 |
| 31 | Coordinator mode | 主 agent 只编排，worker 并行执行，scratchpad 共享 | 未实现完整 coordinator，只能并发 Task | 都能某种程度委派 | 缺 SendMessage/TaskStop/scratchpad/模式恢复 | 直接影响编程团队类 skill |
| 32 | Scratchpad | worker 可共享 durable scratchpad | 未实现专门 scratchpad 语义 | 可以普通文件写入 | 没有权限免询问和跨 worker 规范 | 条件影响 |
| 33 | 权限 allow/ask/deny | settings、managed policy、UI、classifier、remote permission | settings allow/ask/deny + assume_yes + hook decision | 基础阻断可用 | 没有 UI/managed/classifier/remote/swarm | 条件影响到直接影响 |
| 34 | Hook command | command hook 可执行并影响流程 | 支持 command hook、timeout、payload、exit code 2 | 常见 command hook 可用 | payload/策略/禁用层级不完整 | 条件影响 |
| 35 | Hook prompt | prompt hook 可让模型决策 | 当前有 prompt hook runner 协议 | 都能把 hook 作为模型决策点 | 非原生 UI/context，复杂回填不完整 | 条件影响 |
| 36 | Hook skill | 一些 plugin 用 skill hook 注入能力 | 当前识别 `type: skill` 并注入/记录 | 基础可用 | 不等价完整 skill hook lifecycle | 条件影响 |
| 37 | Hook updatedInput | hook 可改写工具输入 | 当前解析 `updatedInput` 并重新检查权限 | 关键语义相同 | 工具输入映射覆盖不一定全 | 条件影响 |
| 38 | Hook permissionDecision | hook 可 allow/deny/ask/block | 当前解析 permissionDecision/decision/continue false | 关键阻断语义相同 | UI ask 不完整 | 条件影响 |
| 39 | MCP stdio | SDK stdio transport | 自实现 JSON-RPC stdio | 基础 tools/call 可用 | SDK 能力更多 | 条件影响 |
| 40 | MCP HTTP | StreamableHTTPClientTransport，session、headers、auth、reconnect | JSON-RPC POST，Accept header，Mcp-Session-Id | 基础 HTTP MCP 可用 | 缺 SDK 完整语义和重连 | 条件影响到直接影响 |
| 41 | MCP SSE | SSEClientTransport，持久流、重连 | GET endpoint + POST + message 匹配 | 简单 SSE 可用 | 复杂 SSE/reconnect 不完整 | 条件影响到直接影响 |
| 42 | MCP WebSocket | WebSocketTransport，proxy/TLS 等 | 依赖 `websocket-client` best effort | 简单 WS 可用 | 依赖包和高级网络选项缺失 | 条件影响 |
| 43 | MCP headersHelper | helper 返回动态 headers | 支持 headersHelper、env 展开 | 基础一致 | trust 检查、错误 UI、策略不完整 | 条件影响 |
| 44 | MCP OAuth/token refresh | OAuth provider、401 refresh、McpAuthTool、XAA、keychain | 未内置；401/403 返回 BLOCKED | 都能使用外部准备好的 token/header | 不会自动授权/刷新 | 直接影响远程 MCP skill |
| 45 | MCP elicitation | URL/form elicitation 可进 UI 或 hook | 未实现 | 无 | 需要用户打开 URL 或填表的 MCP tool 失败 | 直接影响 |
| 46 | MCP resources/prompts | 可 fetch resources/prompts | 当前主要 `tools/call` | 都能调用工具 | 资源/prompt 能力缺失 | 条件影响 |
| 47 | WebFetch/WebSearch | 原生联网工具和权限 | 轻量兼容 action | 名称和基础动作存在 | 行为、结果质量、来源策略不等价 | 条件影响 |
| 48 | Session 记录 | transcript、jsonl、state、metadata、resume 信息 | `.codex-skill-runtime/sessions/<id>` 写 events/prompt/stdout/tools/summary | 都有证据落盘 | 不能完整 replay/resume | 条件影响到直接影响 |
| 49 | Session resume | `--continue`、`--resume`、sessionId 切换、remote history | 没有真实 transcript resume，只注入 summary | 都能提供部分历史摘要 | 无法恢复同一对话状态 | 直接影响长 skill |
| 50 | Parent session lineage | parentSessionId、session mode match | 未完整实现 | 无 | session 链路和 mode 不一致 | 条件影响 |
| 51 | Compact | compact prompt、partial compact、manual/auto compact | session summary 注入 | 都有压缩上下文想法 | 算法和触发完全不同 | 直接影响长上下文 skill |
| 52 | Microcompact | 工具结果和历史小型清理 | 未实现 | 无 | 大任务上下文控制差 | 条件影响 |
| 53 | Session memory compact | session memory 文件作为 compact summary | 仅 summary/index | 都有“摘要” | 不等价 session memory | 条件影响 |
| 54 | Large tool result persistence | 大输出写文件，prompt 中保留 preview/tag，resume 决策稳定 | 工具 JSON 写盘，但不做 preview replacement | 都有工具结果文件 | 不控制 prompt 大小和稳定 wire prefix | 直接影响大输出 skill |
| 55 | Token budget | 解析 `+10k` 等预算并自动 continuation | 未实现 | 无 | 长推理预算语义缺失 | 条件影响 |
| 56 | Prompt cache | session-stable cache flags、post-compact cache 标记 | 未实现模型内部 cache；只做 summary | 无 | 性能和长上下文行为不同 | 通常不影响短 skill，影响长任务 |
| 57 | Feature gates | compile flag、USER_TYPE、GrowthBook | 未复刻；按本地文件和参数运行 | 无 | 命令可见性/能力开关不同 | 条件影响 |
| 58 | KAIROS/proactive | 长期后台助手、tick、SleepTool | 未实现 | 无 | 主动/长期 skill 不工作 | 直接影响该类 skill |
| 59 | Cron/scheduled tasks | `.claude/scheduled_tasks.json`、锁、jitter | 未实现 | 无 | 定时 skill 不工作 | 直接影响 |
| 60 | Dream/Auto memory | 后台整合长期记忆 | 未实现 | 无 | 自动记忆 skill 不工作 | 条件影响 |
| 61 | Bridge remote control | claude.ai/手机远程控制、本地执行、权限回复 | 未实现 | 无 | 只影响远程控制工作流 | 通常不影响本地 skill |
| 62 | Voice/IDE/Chrome integrations | 多种 UI/外部集成 | 未实现 | 无 | 依赖这些上下文的 skill 缺输入 | 条件影响 |
| 63 | Worktree/teleport | worktree isolation、session teleport/resume | 未实现 | 无 | 依赖隔离工作树/跨机恢复会不同 | 条件影响 |
| 64 | Marketplace lifecycle | install/trust/update/disable/browse | 只加载本地 plugin 文件 | 本地插件可用 | 生命周期缺失 | 条件影响 |
| 65 | UI/Keybindings | 完整 Ink UI、diff、permissions、menus | CLI 文本和文件证据 | 都能给用户结果 | 交互体验不等价 | 通常不影响非交互 skill，影响需要选择的 skill |
| 66 | Telemetry/logging | 大量事件、perf、feature logging | session events 简化记录 | 都有日志 | 不等价内部遥测 | 通常不影响 skill 产出 |
| 67 | 安全/trust | workspace trust、managed settings、安全弹窗 | `.claude` 写保护、settings deny、hook 阻断 | 有基础安全边界 | trust/policy 不完整 | 条件影响 |
| 68 | 错误恢复 | 中断、恢复、重连、后台任务状态 | 错误写 session，返回 BLOCKED/FAIL | 都能显式失败 | 自动恢复能力弱 | 条件影响到直接影响 |
| 69 | 产物证据 | transcript、tool result、UI 展示、summary | events/tools/strict-result/summary | 都有可审计产物 | 产物结构不同 | 通常不影响产出，但影响复盘和 resume |
| 70 | 项目特定验证层 | 参考客户端无该 runtime 特化 | 当前有项目验证 workflow/gate | 可用于验证迁移效果 | 不是通用 Claude Code 机制 | 不应用来证明所有 skill 等价 |

## 补充机制对照表

上表覆盖了主要机制。本轮继续按源码扩大检查后，下面这些机制也需要单独列出，因为它们不一定被 CCGS 用到，但可能被其他公开 skill、编程团队 skill、一人公司 skill 或插件 skill 用到。

| # | 机制 | `<reference-project>` 的机制 | 当前 runtime 的机制 | 相同点 | 不同点 | 是否影响 skill 执行效果 |
|---:|---|---|---|---|---|---|
| B1 | 隐藏 system prompt / prompt sections | 主循环会组合基础 system prompt、模式附件、agent system prompt、CLAUDE.md、缓存后的 prompt section | `prompts.py` 明文拼接兼容 prompt，把 skill/agent/body/context 放入用户 prompt | 都会给模型一套执行规则 | 参考的隐藏基础指令和动态 section 没有复刻，当前更像“显式说明书” | 直接影响：模型行为、工具使用习惯、边界判断会不同 |
| B2 | Output style | `/output-style` 和常量 outputStyles 会改变回复风格与系统提示 | 未实现 output style，只能靠 skill prompt 自身要求 | 都能通过提示词影响输出 | 缺少可持久切换的输出风格系统 | 条件影响：依赖特定输出风格的 skill 会不一致 |
| B3 | 主模型、agent 模型、effort 选择 | `/model`、`/effort`、skill/agent frontmatter、GrowthBook/远程配置共同决定模型和思考预算 | 只有 CLI `--model`；frontmatter `model/effort` 多数不驱动 Codex CLI 行为 | 都能指定某种模型 | 缺少 per-skill/per-agent/per-mode 的选择链路 | 条件到直接影响：依赖强模型、低成本模型或高 effort 的 skill 产出会变 |
| B4 | Plan/auto/fast/proactive 模式附件 | 模式会改变权限、提示、是否可执行、是否主动循环 | 只有 `--assume-yes`、`--strict-tools` 等轻量开关 | 都能改变执行策略 | 没有 plan mode、auto mode、fast mode 的完整状态机 | 直接影响：要求先计划、自动执行或持续推进的 skill 不等价 |
| B5 | Skill frontmatter `paths` | `paths` 会让 skill 先挂起，直到相关文件被访问或修改时动态激活 | parser 会保留 metadata，但 loader 不按文件触发条件激活 | frontmatter 能被读取 | 缺条件激活和 touched-files 触发器 | 条件影响：语言/框架专用 skill 可能不会在正确时机出现 |
| B6 | Skill frontmatter 可见性与调用语义 | `user-invocable`、`disable-model-invocation`、`when_to_use`、`argument-hint`、`arguments`、`context: fork`、`agent`、`shell` 都会改变命令展示或执行 | 部分字段写入 prompt 或有限使用；不完整控制模型可见性和执行方式 | 基础 metadata 不会丢 | 字段多数不是真正运行时语义 | 条件影响：依赖“只能模型调用/只能用户调用/自动 fork/参数名”的 skill 会不一致 |
| B7 | Bundled skills | `src/skills/bundledSkills.ts` 注册内置 skill，例如 verify、remember、skillify、claude-api、dream 等 | 只加载本地磁盘 skill/plugin skill | 本地已有同名 skill 时可替代 | 没有内置 registry 和 bundled reference extraction | 直接影响：调用内置 skill 的公开工作流会找不到能力 |
| B8 | MCP skills | 参考可把 MCP 提供的 prompt/resource 转成 model-invocable skill/command | 当前 MCP 主要是 tool call，不把远程 prompt/resource 转成 skill | 都能接 MCP server 的一部分能力 | 动态 MCP skill 缺失 | 条件影响：依赖 MCP 暴露 skill 的流程不一致 |
| B9 | 动态 skill 发现与缓存失效 | 会在会话中发现嵌套 `.claude/skills`，处理 gitignored、条件 skill、cache clear、signal 通知 | loader 可递归找本地 `SKILL.md`，但没有同样的会话级动态激活/cache 信号 | 都能发现许多本地 skill | 不按“会话中访问文件后出现新 skill”的方式运行 | 条件影响：大型 monorepo、包内 skill 会不一致 |
| B10 | Legacy commands-as-skills | 参考把 `.claude/commands` 中的 markdown 命令也纳入 SkillTool 可调用范围 | 当前支持 `.claude/commands` 和根 `commands` 的本地命令，但模型侧 registry 不完整 | 常见 markdown command 能跑 | 和 SkillTool/model-invocable 列表不完全一致 | 条件影响 |
| B11 | Settings/config 分层 | user、project、managed policy、additional dirs、bare mode、plugin-only lock 等共同决定加载范围和权限 | 主要读取项目 `.claude/settings.json`、根 hooks、plugin settings | 都有 settings 和 permissions | 缺多来源优先级、策略锁、add-dir、bare mode 完整语义 | 条件到直接影响：企业/受管环境或多目录项目会不同 |
| B12 | Plugin native hooks / SDK callback hooks | 参考支持 SDK callback hooks、native plugin hooks、注册表和状态保存 | 当前支持 command/prompt/skill hook 的子集 | 常见 hook 可执行 | native/callback 机制不完整 | 条件影响：高级插件 hook 不等价 |
| B13 | Prompt queue / control messages | headless/SDK/bridge 中有 prompt queue、control_request、set_model、set_permission_mode、task notification | 当前是单次命令运行，缺常驻队列和控制消息 | 都能处理一个用户命令 | 不能在运行中接收控制指令并改变状态 | 直接影响：远程控制、团队任务通知、长运行调度不一致 |
| B14 | Read file state / seen content cache | resume 时会恢复模型已经看过的文件内容状态，避免重复或错判 | 当前没有等价 readFileState，只写 session evidence | 都能记录读文件结果 | 不知道“模型之前到底看过哪个版本” | 条件影响：长会话代码修改和 resume 容易不一致 |
| B15 | Tool result 折叠与 transcript 清理 | 有 collapseReadSearch、collapseHookSummaries、collapseBackgroundBashNotifications 等清理策略 | 只把工具结果写盘并回填简化 JSON | 都能保存证据 | 缺面向模型上下文的折叠策略 | 条件影响：工具输出很多时影响模型后续判断 |
| B16 | Advisor / 辅助模型 | `/advisor` 可配置额外顾问模型参与 | 未实现 | 无 | 缺辅助模型旁路建议 | 条件影响：使用 advisor 的工作流产出不同 |
| B17 | Agent MCP requirement 过滤 | AgentTool 会按可用 MCP server 过滤满足要求的 agent | 未完整实现 agent mcp requirement 过滤 | 都能列 agent | 可能选择到缺工具的 agent，或不会隐藏不可用 agent | 条件影响 |
| B18 | 远程/SDK 安全命令过滤 | bridge/remote/SDK 模式会限制哪些 slash command 可用 | 当前没有 remote-safe command 集合 | 都能运行本地命令 | 远程安全边界和可用命令集合不同 | 通常不影响本地 skill，影响远程/SDK skill |

## 对 skill 执行效果影响最大的差异

### 1. 子代理不是完整 runtime action-loop

当前主流程可以 strict，子代理却是普通 Codex exec。参考实现的 AgentTool 子代理仍在同一套工具系统、权限系统、hooks、MCP、resume 机制里运行。这个差异会直接影响：

- 需要子代理真实读写代码的 skill。
- 需要子代理触发 PreToolUse/PostToolUse hook 的 skill。
- 需要子代理内部使用 MCP 的 skill。
- 需要后续 SendMessage 继续子代理上下文的 skill。

这是当前通用 skill 等价最大的缺口。

### 2. 隐藏 system prompt、模型/effort、模式状态不一致

参考客户端不是只把 skill markdown 直接发给模型。它会把隐藏基础 prompt、当前模式、输出风格、模型能力、effort、权限模式、agent system prompt、CLAUDE.md、上下文附件组合成最终请求。当前 runtime 主要靠显式兼容 prompt 约束 Codex。

这个差异会影响：

- 模型是否主动调用工具。
- 模型是否先计划再执行。
- 模型是否遵守某种输出风格。
- 子代理是否使用指定模型。
- skill frontmatter 中的 `model` 和 `effort` 是否真正生效。

这类差异不会让所有 skill 立刻失败，但会让“执行路径一致”和“产出稳定一致”变得不可保证。

### 3. Skill/command frontmatter 高级字段不完整

参考实现把 skill frontmatter 当成运行时协议，而不只是说明文字。当前 runtime 对很多字段只是保存或塞进 prompt，没有完整执行。重点缺口是：

- `paths`：缺按文件触发的条件 skill 激活。
- `user-invocable` / `disable-model-invocation`：缺模型可见性控制。
- `arguments` / `argument-hint`：缺完整参数名和提示语义。
- `context: fork`：只有限近似，不等价完整 fork session。
- `shell`：缺参考实现中的完整 shell frontmatter 语义。
- `model` / `effort`：没有稳定映射到 Codex CLI 的每次调用。

这会影响语言专用、框架专用、插件式、自动触发类 skill。

### 4. Agent frontmatter 高级字段不完整

参考实现的 agent frontmatter 不是普通说明文字，而会改变运行时行为。当前 runtime 对这些字段没有完整执行：

- `mcpServers`：缺 agent 私有 MCP。
- `skills`：缺自动 skill preload。
- `memory`：缺 agent 专属长期记忆。
- `permissionMode`：缺 agent 级权限覆盖。
- `hooks`：缺 agent scoped hooks。

如果公开 skill 只是让 agent 扮演角色，影响较小；如果公开 skill 把这些字段当能力声明，影响会很大。

### 5. Bundled skills、MCP skills、动态 skill registry 不完整

参考客户端有三类当前 runtime 没完整复刻的 skill 来源：

- bundled skills：编译进客户端的内置 skill。
- MCP skills：由 MCP server 暴露出来的远程 prompt/resource 转成 skill。
- dynamic/conditional skills：会话过程中根据路径和文件访问动态出现。

这类差异在 CCGS 里不明显，但换成 GitHub 上的通用 skill 集合或公司内部 skill 后会很明显：某个工作流可能不是找不到主 skill，而是找不到主 skill 运行中要调用的另一个内置/远程/动态 skill。

### 6. 远程 MCP OAuth 与 elicitation 不完整

当前 runtime 已有 stdio/HTTP/SSE/WebSocket 的基础桥接，但没有 OAuth provider、token refresh、McpAuthTool、XAA、URL/form elicitation。影响如下：

- 不需要登录的本地 MCP：通常可用。
- headers/token 已经配置好的远程 MCP：可能可用。
- 需要 Claude Code 自动打开授权、刷新 token、处理 URL elicitation 的 MCP：会失败或 BLOCKED。

### 7. 会话恢复和压缩不是同一个系统

参考实现把 transcript、compact、session memory、tool result replacement、invoked skills、prompt cache 等组合成连续会话机制。当前 runtime 只有 session summary/index 近似。影响如下：

- 短 skill：影响较小。
- 超长分析/开发 skill：上下文容易不一致。
- 中断后继续：不能恢复到 Claude Code 那种同一会话状态。
- 大日志/大 grep 输出：缺 preview replacement，容易污染或丢失上下文。

### 8. Coordinator/Worker 缺失

很多“一人公司”“编程团队”类 skill 的核心不是单个 prompt，而是 worker 编排：

- 并发研究。
- 主 agent 综合。
- 给 worker 发具体实现指令。
- SendMessage 继续已有 worker。
- TaskStop 停止错误方向。
- scratchpad 共享知识。

当前 runtime 只有一次性 Task/Agent 启动，缺这些生命周期机制。此类 skill 会明显不等价。

## 哪些差异通常不影响普通 skill

以下差异对“本地一次性 markdown skill”通常不是核心 blocker：

- 私有 UI、keybindings、主题、菜单。
- Bridge 远程控制。
- 语音、Chrome、IDE 状态栏等外部 UI 集成。
- telemetry/perfetto/内部日志。
- marketplace 浏览和安装界面，只要插件已经在本地。
- GrowthBook 是否显示某个隐藏命令，只要目标 skill 文件本身已存在。
- 私有 Bridge UI 和手机/网页遥控界面，只要 skill 本身不依赖远程控制输入。

但注意：一旦 skill 依赖这些输入或交互，它们就会从“通常不影响”变成“条件影响”。

## 按 skill 类型判断可运行性

| Skill 类型 | 当前 runtime 预期效果 |
|---|---|
| 纯提示词类 skill | 高概率一致 |
| 读代码/写代码/跑命令类 skill | 基本可用，但复杂编辑和权限语义不完全一致 |
| skill 调 skill | 基本可用，取决于 skill 是否在同一 root 可发现 |
| 调用 bundled skill 的 skill | 当前不等价，除非本地补了同名 skill |
| 依赖 `paths` 条件激活的 skill | 当前不等价，可能不会在正确上下文自动出现 |
| 依赖 output style/model/effort 的 skill | 当前不等价或只能近似 |
| 使用 Task/Agent 的 skill | 可用但不完整，子代理内部监督、resume、MCP、memory 不等价 |
| 使用本地 stdio MCP 的 skill | 可能可用 |
| 使用无 OAuth 远程 MCP 的 skill | 可能可用，取决于 server 协议复杂度 |
| 使用 OAuth/elicitation 远程 MCP 的 skill | 大概率不等价或 BLOCKED |
| 使用 Coordinator/worker/team 的 skill | 当前不等价 |
| 使用长期记忆/自动调度/proactive 的 skill | 当前不等价 |
| 依赖 UI 权限选择的 skill | 当前不等价，只能 BLOCKED 或 assume_yes 自动化 |
| 依赖 marketplace 安装的 skill | 当前不负责安装，只能运行本地已有文件 |

## 当前代码中仍带项目特化的地方

虽然本报告不以 CCGS 为目标，但当前代码里确实还有项目特化，主要是：

- `state_machines.py` 中对特定命令的 workflow plan。
- `gates.py` 中的 QA gate 规则。
- `godot.py` 中的 Godot smoke/gameplay 测试工具。
- `prompts.py` 中仍有部分 QA 专用 prompt。
- 核心类名已改为 `CodexSkillRuntime`、`SkillRepositoryLoader`，避免把工具本体误解为 CCGS 专用。

这些特化不代表通用机制。它们只能证明某些验证 skill 能跑，不应拿来证明所有 Claude Code skill 执行效果一致。

## 建议的改造优先级

如果目标是让更多 GitHub Claude Code skill 在 Codex runtime 中达到执行效果一致，应按下面顺序做：

1. **把子代理也纳入 strict action-loop**  
   Task/Agent 子会话内部的工具调用也必须由 runtime 执行和记录。

2. **实现隐藏 prompt / 模式 / 模型 effort 的显式兼容层**  
   不需要复制私有 prompt 原文，但必须定义等价的系统指令、plan/auto/fast 状态、output style、per-skill/per-agent model 和 effort 映射。

3. **实现 Skill/command frontmatter 高级语义**  
   先做 `paths` 条件激活、`user-invocable`、`disable-model-invocation`、`arguments`、`argument-hint`、`context: fork`、`shell`。

4. **实现 Agent frontmatter 高级语义**  
   先做 `mcpServers`、`skills` preload、`memory`、`permissionMode`、frontmatter hooks。

5. **实现 bundled skills / MCP skills / dynamic skill registry**  
   让 runtime 不只看本地 `.claude/skills`，还要能注册内置 skill、MCP skill，并按文件路径动态激活。

6. **实现 settings/config 多来源分层**  
   至少补齐 user、project、managed、additional dirs、bare mode、plugin-only lock 的加载和优先级。

7. **实现 worker registry + SendMessage + TaskStop**  
   这是 Coordinator、一人公司、编程团队 skill 的核心。

8. **实现简化 scratchpad**  
   给 worker 一个明确共享目录和权限规则。

9. **增强 MCP OAuth/elicitation**  
   不需要复制 Claude UI，但至少提供 token provider、refresh command、auth callback/URL flow 的可插拔接口。

10. **实现 transcript resume**  
   能从 session 目录恢复主流程、工具结果、子代理状态。

11. **实现 large tool result replacement**  
   大输出写文件，prompt 中只放 preview 和路径，保证长任务稳定。

12. **实现 compact/token budget**  
   为长 skill 提供自动摘要、预算追踪和 continuation nudge。

13. **把项目特化从通用 runtime 中隔离**  
   将验证用 workflow/gate 放到 plugin 或 profile，通用 runtime 只保留通用机制。

## 最终判断

当前代码与 `<reference-project>` 在“普通 skill 执行主链路”上有明显相同点：发现文件、渲染 prompt、调用模型、执行工具、触发基础 hooks、记录证据。  

当前代码与 `<reference-project>` 在“完整 Claude Code 客户端机制”上仍有大量不同点：隐藏 system prompt、输出风格、模型/effort、Skill/command frontmatter 高级语义、子代理生命周期、Agent frontmatter 高级语义、bundled/MCP/dynamic skills、Coordinator、MCP OAuth、session resume、compact/memory、长期后台机制、UI/权限/marketplace 等。

对 skill 执行效果真正有决定性影响的不是 UI，而是这些运行时机制：

- 隐藏 prompt、模式、模型/effort 是否等价。
- skill/command frontmatter 是否真的改变可见性、触发条件和执行上下文。
- 子代理是否真的受控。
- bundled/MCP/dynamic skill 是否能被发现和调用。
- Agent frontmatter 是否真的改变工具和上下文。
- MCP 是否能认证、重连和处理 elicitation。
- 长任务是否能 resume、compact、管理大输出。
- 多 worker 是否能持续通信和共享状态。

只要这些机制没有补齐，就不能说“所有 Claude Code skill 在 Codex runtime 中执行效果 100% 一致”。更准确的说法是：当前 runtime 已经能覆盖普通本地 markdown skill 和一部分公开 plugin/skill 的基础路径；高级协作、远程 MCP、长会话和自动化类 skill 仍会有明显差异。
