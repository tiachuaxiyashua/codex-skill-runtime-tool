# Runtime 与参考工程执行机制缺口审计

日期：2026-06-04

本文记录当前 `codex-skill-runtime-core` 与本地参考工程在“执行效果”层面的机制对照。目标不是复刻参考工程的私有 UI 或 marketplace，而是确认任意 Claude Code 风格 skill 在 Codex runtime 中运行时，关键工具、状态、记忆、agent、MCP、hook、恢复机制是否仍有会影响产出的缺口。

## 使用的地图

- Runtime 地图：使用工程内临时安装的 Python `codegraph` 生成 `runtime-codegraph.csv`，共 502 条模块、类、函数节点。
- 参考工程地图：参考工程主体是 TypeScript/TSX，当前 Python `codegraph` 不能完整解析 TS，因此使用目录扫描、工具常量扫描、关键机制关键词扫描补齐。
- 工具对照：抽取参考工程 `src/tools` 下的工具目录和工具名，再和 runtime 的 schema、executor alias、strict prompt、ToolSearch 记录对照。

## Runtime 结构图结论

Runtime 当前关键模块分布：

- `runtime.py`：主 skill/agent 执行编排，连接 loader、prompt、hook、worker、QA、strict action loop。
- `action_loop.py`：strict JSON action loop，控制模型每轮只能请求 runtime action。
- `tool_executor.py`：工具执行器，负责 schema action 到实际工具、权限、hook、transcript、artifact 的统一入口。
- `tool_search.py`：模型可见工具/skill/capability/MCP 搜索索引。
- `loaders.py`：加载 `.claude/skills`、`.claude/agents`、插件、额外 skill repo。
- `workers.py`：后台 worker registry、TaskOutput、SendMessage、TaskStop 的状态基础。
- `task_list.py`：TaskCreate/TaskGet/TaskList/TaskUpdate 的任务清单状态。
- `memory.py`、`session_memory.py`、`memdir.py`、`microcompact.py`、`token_budget.py`、`compact_state.py`：长期记忆、会话滚动笔记、topic memory、抽取、整理、预算和压缩。
- `mcp.py`、`mcp_oauth.py`：MCP stdio/http/sse/websocket、OAuth start/complete/refresh、资源读取、动态工具名。
- `hooks.py`：Claude Code 风格 hook 配置、权限、stdin payload、bash shim。
- `bridge.py`、`voice.py`、`ide.py`：Bridge/Voice/IDE 通用状态入口。

Codegraph 中被引用最多的 runtime 模块包括：

- `state_paths.py`：15 个入边，说明状态路径是共享基础设施。
- `session.py`：8 个入边，负责 session event/tree/artifact 的公共落盘。
- `loaders.py`、`mcp.py`、`memdir.py`、`session_memory.py`：都处在多模块调用路径上。
- `tool_executor.py`、`runtime.py`、`selftest.py` 是最大出边模块，符合“编排/执行/验证”职责。

## 参考工程结构图结论

参考工程主要目录规模：

- `src/tools`：工具系统，约 199 个 TS/TSX 文件。
- `src/commands`：CLI slash commands、skills、resume、memory、mcp、permissions、hooks 等命令入口。
- `src/hooks`：Claude Code hook 生命周期。
- `src/skills`：skill 加载、索引、搜索、动态 skill。
- `src/tasks`：任务/agent/team 状态。
- `src/memdir`：长期记忆目录机制。
- `src/bridge`、`src/voice`、`src/remote`、`src/server`、`src/ssh`：外部桥接、语音、远程/服务化能力。
- `src/components`、`src/ink`、`src/screens`：私有 UI/交互层。

这些目录说明参考工程不仅是 skill runner，还包含完整产品级 CLI/UI、远程、交互、缓存、插件、命令、marketplace 生命周期。当前 runtime 只实现和 skill 执行效果直接相关的通用机制，不追求 UI 等产品交互完全一致。

## 工具覆盖结论

扫描结果：

- Runtime schema 可接受工具/别名：130 个。
- Runtime Claude canonical tool name：55 个。
- Runtime strict prompt 工具说明行：56 条。
- 参考工程工具名扫描：53 个。
- 参考工程工具目录扫描：53 个。

已覆盖的关键工具族：

- 文件：Read、Write、Edit、MultiEdit、Glob、Grep、NotebookEdit、Snip、SendUserFile。
- 终端：Bash、PowerShell、TerminalCapture、REPL、Sleep。
- 计划/问题/todo：EnterPlanMode、ExitPlanMode、VerifyPlanExecution、AskUserQuestion、TodoWrite。
- 任务清单：TaskCreate、TaskGet、TaskList、TaskUpdate。
- agent/worker：Agent/Task、TaskOutput、SendMessage、TaskStop。
- skill：Skill、DiscoverSkills、ToolSearch，支持 nested skill invocation。
- MCP：mcp、McpAuth、ListMcpResources、ReadMcpResource、McpElicitation、动态 `mcp__server__tool`。
- 状态记录：Brief、ReviewArtifact、StructuredOutput、RemoteTrigger、Workflow、TeamCreate、TeamDelete。
- 调度/工作树：CronCreate、CronList、CronDelete、EnterWorktree、ExitWorktree。
- 上下文：Monitor、Config、LSP、WebFetch、WebSearch、WebBrowser。
- runtime 扩展：project memory、asset register、capability list、agent memory、bridge、voice、ide。

本轮新修复：

- `ToolSearch` 原先漏列一部分已经能执行的 runtime 工具，例如 `ask_user_question`、`todo_write`、`project_memory_read`、`web_fetch`、`voice`、`ide`。现在 ToolSearch 记录已经和 executor/schema 对齐。
- Windows bash hook shim 原先通过 Python pipe 传 stdin，在 WSL/Windows 下短 timeout 不稳定。现在改为 session payload 文件作为 stdin，并为 Windows bash shim 提供可配置启动宽限，同时 timeout 会返回 HookResult，而不是抛出未捕获异常。

## 剩余差距

### 1. `tungsten`

参考工程中存在 `tungsten` 工具，但其 `isEnabled()` 返回 `false`，描述是 restored development build 中不可用。当前 runtime 没有实现它。

执行影响：低。它在参考工程里也是禁用工具，不应影响普通 skill 执行。

是否建议补：暂不建议。除非后续发现某个 skill 明确依赖 `tungsten` 且有公开可实现的协议。

### 2. `ship-audit` 与 `migration-review`

这两个名字来自参考工程 AgentTool prompt 的示例：

- `ship-audit` 是 fork 示例中的 `name`。
- `migration-review` 是 delegation 示例中的 `name`。

它们不是工具名，也不是必须存在的 agent type。runtime 支持 Agent/Task 的 `name` 字段，也支持找不到 agent 文件时生成 synthetic agent。

执行影响：低。模型可以用这些名字启动工作，runtime 会把它们当 worker name/agent name 处理。

是否建议补：不需要作为内置 agent 固化。若某个 skill 要求具体 agent，应由 skill 仓库提供 `.claude/agents/*.md`。

### 3. 私有 UI / marketplace 完整生命周期

参考工程有大量 Ink UI、screens、components、marketplace/插件交互生命周期。当前 runtime 提供 Web UI MVP 和 CLI，不复刻私有 UI。

执行影响：低到中。对 skill 产出本身影响不大；对人类交互体验、可视化、marketplace 安装体验有影响。

是否建议补：除 marketplace 已明确不做外，UI 只需要继续做状态可视化，不应该让 runtime 特化为游戏或某个 skill。

### 4. WebBrowser 不是完整浏览器

runtime 的 WebBrowser 是轻量 HTTP/HTML 状态浏览器，不是完整 JS 浏览器。

执行影响：中。依赖复杂前端页面交互的 skill 可能需要 Playwright/browser plugin，而不是核心 runtime 硬编码。

是否建议补：建议做成通用 browser plugin/capability，不建议直接把完整浏览器塞进 core。

### 5. Cron 与 worker 的进程边界

Cron fire queue 只在 runtime 进程存活时触发。进程异常退出后，running worker 会在 reload 时标记为 interrupted，而不是恢复旧线程。

执行影响：中。长时间 unattended 任务需要常驻 UI/runtime 进程。异常恢复可追溯，但不能让已死进程继续运行。

是否建议补：如果要做长时间自动开发，下一阶段应增加常驻 daemon/worker supervisor，而不是让短命令 CLI 承担。

## 当前状态判断

对“任意 Claude Code 风格 skill 的执行效果”而言，当前 runtime 已覆盖公开参考工程中最关键的 skill 执行机制：

- skill/agent 加载与路由；
- nested skill invocation；
- 多 skill 仓库和 namespace；
- agent/worker 生命周期；
- Task list 与 background agent 分离；
- ToolSearch/DiscoverSkills；
- MCP 远程与 OAuth；
- hook 生命周期；
- question pause；
- session memory、memdir、side-query、extract、consolidation、microcompact、token budget；
- transcript resume；
- runtime session/tree/artifact/monitor；
- bridge/voice/ide 通用入口。

仍不能称为参考工程“完整产品复刻”，但在 skill 执行机制层面，当前可见差距已经收敛到：

- 参考工程禁用/实验工具；
- 示例 agent 名；
- UI/marketplace 交互生命周期；
- 完整 JS 浏览器；
- 常驻 daemon 级别的长时间运行保障。

这些剩余项中，只有“完整浏览器能力”和“常驻 daemon/supervisor”可能影响复杂长期任务的稳定性；它们应作为通用 plugin/capability 继续补，而不是写成某个具体 skill、项目、引擎、素材服务或本机路径特例。
