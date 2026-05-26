# Design: Runtime-Enforced Skill Equivalence

## Definitions

- **Codex 大脑**：Codex CLI。它读取 prompt，做推理，提出下一步。
- **Runtime 运行时**：`codex-skill-runtime-core/`。它读取 Claude Code 风格 skill 仓库，执行工具，触发
  hooks，启动子代理，记录证据，决定 gate。
- **Strict action-loop**：Codex 不直接描述“我已经做了什么”，而是返回 JSON action。
  Runtime 执行 action 后把 observation 交回 Codex，直到 Codex 返回 final 或 blocked。
- **可观察执行效果等价**：同一套 CCGS skill 在 Codex runtime 中表现出同样的流程约束、
  agent 分工、工具副作用、hook 事件、QA gate、Godot 测试和证据产物。

## Architecture

```text
core_cli.py
  -> CLI 参数解析
  -> CodexSkillRuntime
     -> SkillRepositoryLoader 读取 .claude/skills、.claude/agents、.claude/docs、settings
     -> build_workflow_plan 生成 /prototype、/team-qa 等流程计划
     -> CodexCLI 调 codex exec
     -> StrictActionLoop 要求 Codex 返回结构化 action
     -> ToolExecutor 执行文件工具、Bash、Task/Agent、Skill、Todo、Web、Godot、MCP action
     -> HookDispatcher 触发 session/subagent/tool hooks
     -> RuntimeSession 写入 .codex-skill-runtime/sessions/
     -> Gate evaluators 判定 PASS/FAIL/BLOCKED
```

## Action Loop

Codex 每轮收到：

- skill body
- agent body
- CCGS context files
- 可用 action schema
- 前几轮 runtime observations
- 当前 command、arguments、workflow plan

Codex 必须返回：

```json
{
  "status": "action_required | final | blocked",
  "summary": "...",
  "actions": [
    {
      "tool": "read_file | glob | grep | write_file | edit_file | multi_edit | bash | task | agent | ask_user_question | todo_write | skill | web_fetch | web_search | godot_smoke | mcp",
      "reason": "...",
      "parameters": {}
    }
  ],
  "final": "..."
}
```

Runtime 负责校验 JSON、执行 action、记录结果。Codex 不能自己决定跳过 hook、gate
或 Godot 测试。

当前 provider 偶尔会让 `codex exec --output-schema` 返回 502，所以 runtime 保留
prompt-only JSON fallback：先尝试 schema 约束，失败后再用强提示要求只返回 JSON。
这保证功能可以继续运行，同时保留优先使用 schema 的路径。

## Tool Semantics

- `read_file`：读取文件，可限制最大字符数。
- `glob`：按 pattern 找文件，返回相对路径列表。
- `grep`：按正则搜索文件内容，返回路径、行号和片段。
- `write_file`：写文件，自动创建父目录，触发 `PostToolUse:Write`。
- `edit_file`：做一次精确替换，触发 `PostToolUse:Edit`。
- `multi_edit`：按顺序做多次精确替换，作为 MultiEdit 兼容层。
- `bash`：执行 shell 命令，先检查 `.claude/settings.json` deny 规则，触发
  `PreToolUse:Bash` 和 `PostToolUse:Bash`。
- `task` / `agent`：启动独立 Codex 子代理，触发 SubagentStart/SubagentStop。
- `ask_user_question`：无 `--assume-yes` 时 BLOCKED；有 `--assume-yes` 时记录问题并选择
  第一个选项或默认值。
- `todo_write`：记录 TodoWrite 兼容清单到 session。
- `skill`：加载同一 runtime root 中另一个 skill 的 body 和 supporting file 清单。
- `web_fetch` / `web_search`：提供轻量 WebFetch/WebSearch 兼容 action。
- `godot_smoke`：启动 Godot headless，并在存在时执行 `scripts/gameplay_test.gd`。
- `mcp`：读取项目 `.mcp.json`、插件根 `.mcp.json`、manifest 内联或路径型
  `mcpServers`，桥接 stdio、HTTP、SSE 和 WebSocket MCP server。HTTP/SSE/WS
  会展开 `${ENV}`、`${CLAUDE_PLUGIN_ROOT}`，合并静态 `headers` 与
  `headersHelper` 输出；远程服务返回 401/403 时记录明确认证边界，而不是伪造成功。

Runtime 允许读取 `.claude`，但拒绝任何写入 `.claude` 的 action。
若 skill frontmatter 声明了 `allowed-tools`，runtime 将其作为预批准提示记录，
不再当成硬白名单。真正的阻断来自 settings 中匹配到的 `deny` 或非自动化状态下的
`ask` 规则；`allow` 用于显式通过。

## Hook Semantics

- SessionStart 和 Stop 包住一次 runtime 命令。
- SubagentStart 和 SubagentStop 包住 Task/agent 调用。
- PreToolUse 和 PostToolUse 包住 runtime-owned Bash/Write/Edit。
- Windows 上的 CRLF `.sh` hook 会被复制到 session-local LF shim 后执行。
- 原始 `.claude/hooks` 文件不被修改。
- 插件 `hooks/hooks.json` 会合并进 dispatcher。
- Hook command 中的 `${CLAUDE_PLUGIN_ROOT}` 会按插件根目录替换。

## Plugin / Command Semantics

Runtime 发现以下入口：

- `.claude/commands/**/*.md`
- `commands/**/*.md`
- 插件 `commands/**/*.md`
- 插件 `skills/**/SKILL.md`
- 插件 `agents/**/*.md`
- 插件 `hooks/hooks.json`

插件由 `.claude-plugin/plugin.json` 定位。运行时会使用插件名生成 namespace，
例如 `/start:review`、`/team:api-contract-design`、`/arc:using-arc`。

Skill/command body 在 prompt 构造前会处理：

- `$ARGUMENTS` 和 `$ARGUMENTS[index]`
- `${CLAUDE_PLUGIN_ROOT}`
- `` !`command` `` 动态上下文注入

如果 `Skill` action 加载的 skill 声明 `context: fork`，runtime 会启动独立 Codex
子会话，并把父命令 arguments 带入 fork prompt。

## Task Semantics

非 strict 模式下，主 Codex 如果需要 Task，必须输出：

```text
RUNTIME_TASK_REQUEST: agent=<agent-name>; purpose=<short purpose>; inputs=<paths or concise context>
```

Runtime 解析该约定并启动独立 Codex 会话。Strict 模式下，Codex 直接返回
`task` action。若同一轮 action 全部是 Task，runtime 可以并发执行，最多 4 个 worker。

## Gate Semantics

QA gate 只有在下面条件满足时才通过：

- 输出包含 `VERDICT: PASS` 或可接受的 warning verdict。
- PASS 必须包含 `EVIDENCE MATRIX`。
- 证据必须来自可追踪的运行命令、文件检查或测试输出。

Godot gate 只有在真实进程退出码为 0 时通过。若存在 `scripts/gameplay_test.gd`，
该脚本必须执行并返回 0。

## Evidence Model

每次运行创建 `.codex-skill-runtime/sessions/<timestamp>-<name>/`：

- `events.jsonl`：session/tool/hook/gate 事件。
- `workflow-plan.json`：本次命令的流程计划。
- `prompt.md`、`stdout.txt`、`stderr.txt`：Codex 调用证据。
- `tools/*.json`：runtime-owned tool action 结果。
- `strict-result.json`：strict action-loop 汇总。
- `godot-*` 子目录：Godot stdout/stderr。
- `summary.json`：本 session 的压缩摘要，包括 command、status、recent events、
  recent tools、gate outcomes 和 notes。
- `.codex-skill-runtime/sessions-index.json`：跨 session 的 bounded 摘要索引，供后续 prompt
  注入 “Runtime Memory / Compacted Session Context”。

## Risk

这个实现不复制 Claude Code 隐藏内部，只还原 CCGS 关心的可观察执行效果。
因此它不能保证：

- Claude 和 Codex 两个模型逐字输出一致。
- Claude Code 私有 UI、缓存、内部 prompt 和权限交互一致。
- Claude Code 完整 OAuth 浏览器流程、动态客户端注册、token 刷新和 needs-auth UI
  完全等价；当前 runtime 支持 headers/token/headersHelper 驱动的远程 MCP，并在缺认证时显式 BLOCKED。
- fork 子会话内部由 Codex CLI 执行时，每一个微观工具事件都能被 runtime 拦截。

这些不是本 change 的目标。对 CCGS 来说，关键是流程必须被运行时强制执行，QA
必须真的跑，Godot 必须真的测，证据必须能追溯。
## 2026-05-23 Compatibility Addendum

本轮补齐的是公开 Claude Code plugin/command/hook 文档暴露出来的通用机制，不依赖私有或泄露源码。

- 命令正文预处理现在覆盖 `$ARGUMENTS`、`$ARGUMENTS[index]`、`$1/$2/...`、`@file`、`@$1`、`@${CLAUDE_PLUGIN_ROOT}/file` 和 `` !`command` ``。文件引用会在 prompt 中注入带边界的文件内容；找不到文件时注入显式诊断，而不是静默丢失上下文。
- plugin manifest 中的 `commands`、`skills`、`agents`、`hooks` 自定义路径按“补充默认目录”处理。也就是说，配置了 `custom-commands` 后，默认 `commands/` 仍然会被加载。
- MCP 发现来源扩展为项目 `.mcp.json`、插件根 `.mcp.json`、manifest 内联 `mcpServers`、manifest 指向的 `.mcp.json` 路径。stdio、HTTP、SSE 和 WebSocket MCP 现在都有真实桥接路径；远程 OAuth 浏览器生命周期不内置，缺少可用 headers/token 时返回显式 BLOCKED。
- hook 输入 payload 现在包含 `session_id`、`transcript_path`、`cwd`、`permission_mode`、`hook_event_name`，并为工具事件提供 `tool_name`、`tool_input`、`tool_result`/`tool_response`。
- hook 输出现在解释 `continue: false`、`decision: block|deny`、`hookSpecificOutput.permissionDecision`、`hookSpecificOutput.updatedInput` 和退出码 `2`。PreToolUse 可以阻断工具，也可以改写工具输入；改写后 runtime 会重新检查 settings 权限规则。
- runtime 现在调度 `UserPromptSubmit` 和 `SessionEnd`。Stop/SubagentStop 的 block 决策会进入 gate 或退出码，而不只是日志。
- `type: prompt` hook 通过 Codex prompt-hook runner 近似执行，要求返回 JSON 决策；selftest 使用 fake runner 验证协议，真实 runtime 使用 Codex CLI 执行该 hook prompt。

仍然保留的边界：完整远程 MCP OAuth 浏览器/刷新生命周期、Claude Code 私有 UI、企业 managed policy、内部 system prompt 和模型内部缓存不在本 change 目标内。模型缓存与上下文压缩通过 session summary/index 做可观察近似，不声称等同 Claude Code 私有缓存实现。

## 2026-05-23 Remote MCP And Memory Addendum

- HTTP MCP 采用 JSON-RPC POST，发送 `Accept: application/json, text/event-stream`、`MCP-Protocol-Version`，并保留 `Mcp-Session-Id` 给后续请求。
- SSE MCP 采用 GET 读取 `endpoint` 事件，再向 endpoint POST `initialize`、`notifications/initialized` 和 `tools/call`，从 SSE message 中匹配 JSON-RPC id。
- WebSocket MCP 使用 `websocket-client` 包做 best-effort 桥接；未安装时返回明确依赖缺失，不静默降级。
- `headers` 支持 `${ENV}`、`${CLAUDE_PROJECT_DIR}`、`${CLAUDE_PLUGIN_ROOT}` 展开；`headersHelper` 会获得 `CLAUDE_CODE_MCP_SERVER_NAME` 和 `CLAUDE_CODE_MCP_SERVER_URL`。
- 401/403 被归类为认证/授权边界，提示配置 headers、headersHelper 或 OAuth/token；当前不实现 Claude Code 私有 OAuth UI。
- Runtime memory 写入 `summary.json` 与 `.codex-skill-runtime/sessions-index.json`，并在后续 prompt 中注入 bounded previous summaries，用来近似隐藏 prompt/cache/compact 对执行连续性的贡献。
