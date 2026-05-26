# CCGS 到 Codex 的机制迁移说明

## 不能走的路线

不要使用泄露或未授权的 Claude Code 源码。公开可访问不等于开源授权。
本运行时采用干净实现：只使用官方文档、黑盒行为观察，以及本仓库中用户拥有的
`.claude/` 技能和代理文本。

## Claude Code 对 CCGS 的关键能力

从这套源码可以看出，CCGS 依赖的不是单个 prompt，而是一套运行机制：

1. Slash command 选择技能，例如 `/prototype`。
2. 技能 frontmatter 指定主代理，例如 `agent: prototyper`。
3. 主代理读取 skill 的阶段说明，并在需要时调用 `Task`。
4. `Task` 启动独立子代理，例如 `qa-tester`。
5. `AskUserQuestion` 是流程暂停点，不是普通文本。
6. `.claude/settings.json` 中 hooks 在会话、工具调用、子代理开始/结束等时机触发。
7. QA、gate、report、session-state 文件共同构成流程闭环。

## Codex 化时的正确分层

Codex CLI 只适合做“大脑”。外层 runtime 必须负责状态机和副作用：

```text
用户命令
  -> runtime 解析 /prototype
  -> 读取 .claude/skills/prototype/SKILL.md
  -> 读取 .claude/agents/prototyper.md
  -> 组合 prompt 调 codex exec
  -> strict 模式下要求 Codex 返回 JSON action
  -> runtime 执行 Read/Write/Bash/Task/Godot 等 action
  -> 解析或执行 Task，启动 qa-tester 等子代理
  -> 运行 Godot/测试命令
  -> gate 判定 PASS/FAIL/BLOCKED
  -> 写入 .codex-skill-runtime/sessions/
```

## 为什么静态 skill 包装不够

静态包装只能让 Codex “看到” qa-tester 的文本，但不能保证它真的启动 QA。
之前瓦片地图小游戏的移动计数 bug 正是这个问题：QA 角色没有作为独立 gate 执行，
所以只验证了终态，漏掉了“每一步 HUD 是否刷新”这种中间态问题。

## 当前版本如何修复机制缺口

- `/prototype --path engine` 会在主 Codex 运行后强制追加 `qa-tester`。
- QA prompt 明确要求检查中间态：空地移动、墙阻挡、金币、HUD、重启、胜利。
- QA 结果必须写 `VERDICT` 和 `EVIDENCE MATRIX`。
- runtime 用 gate 检查 QA 是否真的给了证据。
- strict action-loop 要求 Codex 返回结构化 action，由 runtime 执行工具。
- runtime 工具代理层支持 Read/Glob/Grep/Write/Edit/Bash/Task/AskUserQuestion/Godot smoke。
- runtime-owned Write/Edit/Bash/Task/session 事件会触发对应 hooks。
- 同一轮多个 Task action 可并发执行。
- Godot 项目可通过 `godot-smoke` 直接跑 headless 和 `scripts/gameplay_test.gd`。
- 完整 live selftest 覆盖 strict action-loop、Godot headless 和真实 `qa-tester`。

## 100% 的定义

这里的 100% 是 CCGS 的可观察执行效果等价：

- 同一个 slash command 读取同一份 skill。
- 同一个 skill 路由到同一个主 agent。
- 需要 Task 的地方会启动独立子代理，而不是只把 agent 文本塞进 prompt。
- 需要 AskUserQuestion 的地方会形成 runtime 暂停或自动化记录。
- 需要 hook 的地方由 runtime 触发。
- 需要 QA gate 的地方必须跑 QA 并给证据。
- 需要 Godot 测试的地方必须真实运行 Godot。
- 所有过程都有 session evidence。

它不是下面这些内容的 100%：

- Claude Code 隐藏 system prompt。
- Claude Code 私有 UI。
- Claude Code 模型上下文缓存。
- Claude 模型和 Codex 模型的逐字输出。
- Claude Code 内部每一次微观工具事件。

这些隐藏部分没有合法、稳定、可验证的复制边界；对 CCGS 的产出质量来说，真正要还原的是流程强制力和证据闭环。

## 已完成的 OpenSpec 项目

这些项目已经在 OpenSpec change `codex-runtime-equivalence` 中完成：

1. `/prototype` 和 `/team-qa` 具备显式 workflow plan/state-machine 入口。
2. Codex 严格模式每轮返回结构化 action；当当前 provider 的 `--output-schema` 失败时，runtime 自动降级为 prompt-only JSON 模式。
3. runtime 工具代理层已支持 Read/Glob/Grep/Write/Edit/Bash/Task/AskUserQuestion/Godot smoke。
4. runtime-owned Write/Edit/Bash/Task/session 事件会触发对应 hooks。
5. 同一轮多个 Task action 可并发执行。
6. 完整 live selftest 已覆盖 strict action-loop、Godot headless 和真实 `qa-tester`。
## 2026-05-23 机制补齐说明

这轮补齐后的运行时更像一个“Claude Code 公开协议适配层”：

- Loader 负责发现 `.claude`、repo-root skill/agent/command、plugin 默认目录和 manifest 自定义目录。
- Prompt renderer 负责把 command body 里的参数、文件引用、插件根目录变量、动态 shell 上下文变成 Codex 能直接看到的 prompt。
- HookDispatcher 负责把 Claude-like 事件 payload 送给 command hook 或 prompt hook，并把 hook 输出解释成 runtime 决策。
- ToolExecutor 负责在工具执行前应用 settings 权限、PreToolUse hook、`updatedInput`，工具执行后触发 PostToolUse。
- Runtime 负责生命周期：`UserPromptSubmit`、`SessionStart`、主 Codex 执行、Task/Subagent、Stop、SessionEnd、gate、证据落盘。

这仍然是 clean-room 实现：参考的是公开文档和公开 GitHub 插件样本暴露出的输入输出契约，不复制 Claude Code 私有客户端实现。

## 2026-05-23 远程 MCP 与记忆机制说明

远程 MCP 是否需要实现，取决于公开 skill/plugin 是否使用。当前已确认 DeepBits 插件使用 HTTP MCP，官方 plugin-dev 文档也要求 SSE、HTTP、WebSocket 和 headersHelper，因此 runtime 不能继续只返回“远程 MCP 不支持”。

当前实现方式：

- `stdio`：继续使用本地子进程 stdin/stdout JSON-RPC。
- `http`：向配置 URL POST JSON-RPC，发送 MCP streamable HTTP 需要的 Accept 头，保存 `Mcp-Session-Id` 并用于后续请求。
- `sse`：GET SSE stream，等待 `endpoint` 事件，随后向 endpoint POST MCP 请求，并从 SSE message 中按 JSON-RPC id 找响应。
- `websocket`：如果安装了 Python `websocket-client`，使用 WebSocket 发送/接收 JSON-RPC；未安装时明确 BLOCKED。
- `headers` / `headersHelper`：支持 `${ENV}`、`${CLAUDE_PROJECT_DIR}`、`${CLAUDE_PLUGIN_ROOT}` 展开，并给 helper 注入 `CLAUDE_CODE_MCP_SERVER_NAME`、`CLAUDE_CODE_MCP_SERVER_URL`。
- OAuth：不复刻 Claude Code 私有浏览器授权、动态客户端注册、token 刷新和 `/mcp` UI。远程服务返回 401/403 时，runtime 明确告诉用户需要配置 headers、headersHelper 或 token/OAuth 成果。

隐藏 system prompt、内部缓存和上下文压缩不能按 Claude Code 私有实现复制。runtime 做的是可观察近似：

- 每个 session 结束时写 `.codex-skill-runtime/sessions/<session>/summary.json`。
- 写 `.codex-skill-runtime/sessions-index.json` 作为最近 session 的 bounded 索引。
- 下一次 prompt 构造时注入 `Runtime Memory / Compacted Session Context`，包含最近 command、状态、事件、工具摘要和 gate 结论。
- 这让长流程和跨 session 继续执行有稳定上下文来源，但不声称与 Claude Code 模型缓存命中、私有压缩 prompt 或记忆 daemon 完全相同。
