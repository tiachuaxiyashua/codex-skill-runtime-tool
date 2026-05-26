# Codex Skill Runtime Core

这是一个干净实现的轻量运行时，用来把 Claude Code skills 接到 Codex CLI 上。
它加载 `.claude/skills`、`.claude/agents`、hooks、Task 子代理、
AskUserQuestion、QA gate 和 Godot 测试流程，同时也支持常见 GitHub
Claude skill 仓库布局。Claude Code Game Studios 只是一个可加载的验证仓库。

这里的“100% 还原”指被加载 skill 仓库的可观察执行效果等价，不是复制
Claude Code 隐藏源码或私有 UI。也就是说：同一个 slash command 会读取同一个
skill，路由到同一个 agent，触发同类 Task/hook/gate/test，并留下可审计证据。

## 已完成能力

- 读取原始 `.claude/skills/<name>/SKILL.md`。
- 读取 repo-root `skills/<name>/SKILL.md` 和根目录 `<name>/SKILL.md` 风格 skill。
- 读取 `.claude/commands`、repo-root `commands/`、插件 `commands/` 和插件
  `skills/` 风格入口。
- 读取原始 `.claude/agents/<agent>.md`。
- 读取 repo-root `agents/<agent>.md`、递归 agent 目录和插件 `agents/`，没有 agent
  时使用通用 agent 壳。
- 发现 `.claude-plugin/plugin.json`，支持插件 namespace、默认组件目录和
  `${CLAUDE_PLUGIN_ROOT}`。
- 按 skill frontmatter 的 `agent:` 字段选择主代理。
- 保持 `.claude/` 只读，runtime 工具拒绝写入 `.claude`。
- 用 Codex CLI 作为推理后端。
- 用 strict action-loop 让 Codex 返回结构化 action，由 runtime 执行工具。
- 支持 `read_file`、`glob`、`grep`、`write_file`、`edit_file`、`multi_edit`、
  `bash`、`task`、`agent`、`ask_user_question`、`todo_write`、`skill`、
  `web_fetch`、`web_search`、`godot_smoke` 和 stdio/HTTP/SSE/WebSocket MCP action。
- 用独立 Codex 会话模拟 `Task` 子代理。
- 同一轮多个 Task action 可并发执行。
- 调用 `.claude/settings.json` 中声明的 session/subagent/tool hooks。
- 合并插件 `hooks/hooks.json`，并替换 hook command 中的 `${CLAUDE_PLUGIN_ROOT}`。
- 在 Windows 上为 CRLF `.sh` hook 创建 session-local LF shim。
- 渲染 `$ARGUMENTS`、`$ARGUMENTS[index]` 和 `` !`command` `` 动态上下文。
- 对 `context: fork` skill 的 Skill action 启动独立 Codex 子会话。
- 将 skill `allowed-tools` 作为预批准提示，不再误当成硬白名单；settings
  `deny`/`ask`/`allow` 才是 runtime 权限决策来源。
- 读取 `.mcp.json` 和插件 `mcpServers`。stdio、HTTP、SSE、WebSocket MCP 会真实调用；
  远程 OAuth 缺少 headers/token 时明确 BLOCKED。
- 对 Godot/engine 原型强制追加 `qa-tester`。
- QA gate 要求 `VERDICT` 和 `EVIDENCE MATRIX`。
- Godot gate 真实运行 headless，并在存在时执行 `scripts/gameplay_test.gd`。
- 将 prompt、stdout/stderr、tool result、hook result、gate result 写入
  `.codex-skill-runtime/sessions/`。

## 快速检查

```powershell
cd <skill-repo-root>
python .\codex-skill-runtime-core\core_cli.py inspect
```

## 干跑一次 /prototype

干跑只生成 prompt 和调度记录，不消耗 Codex token。

```powershell
python .\codex-skill-runtime-core\core_cli.py --dry-run --assume-yes --qa required run /prototype "Godot tile map coin collection prototype" --path engine --spike
```

## 真实运行 /prototype

```powershell
python .\codex-skill-runtime-core\core_cli.py --assume-yes --qa required --godot <godot-executable-or-dir> run /prototype "Godot tile map coin collection prototype" --path engine --spike
```

## 严格工具代理模式

严格模式下，Codex 先返回结构化 action，runtime 再执行工具。这样
Write/Edit/Bash/Task/AskUserQuestion/Godot smoke 都能被 runtime 记录、触发 hook、
进入 gate。

```powershell
python .\codex-skill-runtime-core\core_cli.py --strict-tools --assume-yes --qa required --godot <godot-executable-or-dir> run /prototype "Godot tile map coin collection prototype" --path engine --spike
```

最小 strict action-loop 烟测：

```powershell
python .\codex-skill-runtime-core\core_cli.py strict-smoke README.md
```

## 单独运行 QA 代理

```powershell
python .\codex-skill-runtime-core\core_cli.py agent qa-tester "请检查 <godot-project> 的 Godot 原型，重点验证移动计数和 HUD 是否每步更新。"
```

## 单独运行 Godot 烟测

```powershell
python .\codex-skill-runtime-core\core_cli.py --godot <godot-executable-or-dir> godot-smoke <godot-project>
```

## 运行完整自测

普通自测：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py --godot <godot-executable-or-dir> selftest --godot-project <godot-project>
```

完整 live 自测，包括 strict action-loop、Godot 和真实 `qa-tester`：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B .\codex-skill-runtime-core\core_cli.py --godot <godot-executable-or-dir> selftest --godot-project <godot-project> --live-strict-target README.md --live-qa-target <godot-project>
```

自测覆盖：skill/agent/command/plugin 发现、frontmatter 路由、Task 解析、QA gate、
Codex dry-run 命令形态、strict action-loop、runtime tool executor、权限规则、stdio/HTTP/SSE MCP、
hook shim、Godot headless/gameplay 测试、真实 `qa-tester`、以及 `.claude` 是否保持未修改。

## OpenSpec

本 runtime 对应的 OpenSpec change：

```powershell
openspec validate codex-runtime-equivalence --strict
```

详细设计和实测证据位于：

- `openspec/changes/codex-runtime-equivalence/`
- `openspec/changes/codex-runtime-equivalence/evidence.md`
- `codex-skill-runtime-core/docs/MECHANISM_NOTES_CN.md`
- `codex-skill-runtime-core/docs/VALIDATION_EVIDENCE_CN.md`
- `codex-skill-runtime-core/docs/CLAUDE_SKILL_COMPAT_AUDIT_CN.md`

## 边界

这个 runtime 不复制 Claude Code 的隐藏 system prompt、私有 UI、模型上下文缓存、
权限确认界面、完整远程 MCP OAuth 浏览器/刷新生命周期或模型内部策略。它完成的是通用 Claude Code skill 仓库通常依赖的运行闭环：
命令路由、agent 路由、Task 子代理、工具代理、hook、AskUserQuestion、QA gate、
Godot 实测和证据落盘。
## 2026-05-23 新增兼容机制

本轮补齐的是公开 Claude Code plugin/command/hook 文档中会影响其他 GitHub skill/plugin 的通用运行机制：

- 命令正文支持 `$1/$2/...`、`$ARGUMENTS[index]`、`@file`、`@$1`、`@${CLAUDE_PLUGIN_ROOT}/file`、`` !`command` ``。文件引用会把文件内容注入 prompt，缺失时写入明确诊断。
- plugin manifest 中的 `commands`、`skills`、`agents`、`hooks` 自定义路径现在是“补充默认目录”，不会覆盖默认 `commands/`、`skills/`、`agents/`、`hooks/hooks.json`。
- MCP 来源支持项目 `.mcp.json`、插件根 `.mcp.json`、manifest 内联 `mcpServers`、manifest 指向的 MCP JSON 文件；stdio、HTTP、SSE、WebSocket 真实桥接；远程 OAuth 缺少 headers/token 时显式 BLOCKED。
- hook payload 补齐 `session_id`、`transcript_path`、`cwd`、`permission_mode`、`hook_event_name`、`tool_name`、`tool_input`、`tool_result`。
- hook 输出会被 runtime 执行：`permissionDecision: deny|ask`、`updatedInput`、`continue:false`、`decision:block|deny`、退出码 `2` 都会影响工具执行或 gate。
- 新增 `UserPromptSubmit` 和 `SessionEnd` 调度；Stop/SubagentStop 的 block 决策不再只写日志，会进入 gate 或退出码。
- `type: prompt` hook 现在有 Codex CLI runner；selftest 用 fake runner 验证协议，真实运行时用 Codex 执行 hook prompt 并解释 JSON 决策。

普通 selftest 已扩展为 18 项，新增 `command-preprocessing-contract`、`plugin-manifest-contract`、`hook-decision-contract`、`memory-compaction-contract`；`mcp-bridge-contract` 覆盖 stdio、HTTP、SSE 与 headersHelper。

## 2026-05-23 远程 MCP 与记忆补齐

- HTTP MCP 使用 JSON-RPC POST，并保留 `Mcp-Session-Id`。
- SSE MCP 会读取 `endpoint` 事件，再向 endpoint POST MCP 消息。
- WebSocket MCP 使用 `websocket-client` 包做 best-effort 桥接；缺包时明确提示。
- `headers` 支持环境变量展开，`headersHelper` 会收到 `CLAUDE_CODE_MCP_SERVER_NAME` 和 `CLAUDE_CODE_MCP_SERVER_URL`。
- 每个 session 会写 `summary.json`，并维护 `.codex-skill-runtime/sessions-index.json`；后续 prompt 会注入 bounded runtime memory，近似 Claude Code 的上下文压缩/记忆效果。
