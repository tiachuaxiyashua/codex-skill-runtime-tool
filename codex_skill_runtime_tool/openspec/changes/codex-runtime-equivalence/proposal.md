# Change: Codex Runtime Execution Equivalence

## Why

第一次 Codex 化只能让 Codex “看到” CCGS 的 skill/agent 文本，但没有完整还原
Claude Code 给这套技能组提供的运行机制。瓦片地图小游戏暴露了这个问题：
QA 的角色文本存在，但 QA 子代理没有被强制作为独立 gate 执行，所以“每一步移动
后 HUD 步数是否刷新”这种中间态 bug 没有被发现。

因此需要把 Codex 放进一层运行时里：Codex 负责推理和提出下一步，运行时负责
状态机、工具执行、hook、Task 子代理、AskUserQuestion、Godot 实测、QA gate 和证据。

## What Changes

建立 `codex-skill-runtime-core/`，并把剩余机制全部纳入 OpenSpec change：

- 以原始 `.claude/skills`、`.claude/agents`、`.claude/docs`、`.claude/settings.json`
  作为只读事实源。
- 按 skill frontmatter 的 `agent:` 字段进行主代理路由，例如 `/prototype -> prototyper`。
- 增加 strict action-loop：Codex 每轮只能返回结构化 action，runtime 再执行工具。
- 支持 Read、Glob、Grep、Write、Edit、MultiEdit、Bash、Task、Agent、
  AskUserQuestion、TodoWrite、Skill、WebFetch、WebSearch、Godot smoke/test，
  并桥接 stdio、HTTP、SSE、WebSocket MCP；远程 MCP 缺少认证材料时返回显式 BLOCKED。
- 读取 repo-root `skills/`、repo-root `agents/` 和根目录 `<skill>/SKILL.md`
  形式的 GitHub Claude skill 仓库。
- 读取 `.claude/commands`、repo-root `commands/`、插件默认 `commands/`/`skills/`/
  `agents/` 目录和 `.claude-plugin/plugin.json` 所定义的插件根。
- 列出 supporting files，渲染 `$ARGUMENTS`，并支持 `hooks/hooks.json`
  中的 skill hook 注入。
- 渲染 `` !`command` `` 动态上下文，解析 `${CLAUDE_PLUGIN_ROOT}`，并让
  `context: fork` skill 的 Skill action 进入独立 Codex 子会话。
- runtime 层禁止写入 `.claude`，避免为了迁移而篡改原 Claude Code skill。
- 按 `.claude/settings.json` 触发 SessionStart、Stop、SubagentStart、SubagentStop、
  PreToolUse、PostToolUse。
- 将 skill `allowed-tools` 视为预批准提示而不是硬白名单，并用 settings
  `deny`/`ask`/`allow` 做 runtime 权限决策。
- 读取 `.mcp.json` 和插件 `mcpServers`，支持 `mcp__server__tool` 与
  `mcp__plugin_<plugin>_<server>__tool` 的 stdio/HTTP/SSE/WebSocket 调用。
- 在 Windows 上为 CRLF `.sh` hook 生成 session-local LF shim，不修改原 hook 文件。
- 用独立 Codex 会话模拟 Task 子代理；同一轮多个 Task action 可以并发执行。
- 对 `/prototype` 和 `/team-qa` 增加显式 workflow plan/state-machine 入口。
- 对 Godot/engine 原型强制追加 `qa-tester`，并要求中间态测试证据。
- QA gate 拒绝没有 `VERDICT` 或没有 `EVIDENCE MATRIX` 的 PASS。
- Godot gate 必须真实运行 headless 和 `scripts/gameplay_test.gd`。
- 所有 prompt、命令、工具结果、hook 结果、gate 结果落入 `.codex-skill-runtime/sessions/`。
- 每个 session 写入 `summary.json`，并维护 `.codex-skill-runtime/sessions-index.json`，
  后续 prompt 注入 bounded runtime memory，近似 Claude Code 的上下文压缩/记忆效果。
- 增加 selftest，覆盖 loader、frontmatter、Task、gate、tool proxy、hooks、Godot、
  strict action-loop、真实 Codex QA 和 `.claude` clean 检查。

## Non-Goals

- 不克隆 Claude Code 客户端内部源码。
- 不使用泄露或未授权源码。
- 不承诺隐藏 system prompt、模型 token 级输出、私有 UI、缓存策略完全一致。
- 不修改原始 `.claude` 内容来“打补丁”。
- 不把 Codex 模型和 Claude 模型的自然语言判断伪装成完全相同。

## Success Criteria

- `openspec validate codex-runtime-equivalence --strict` 返回成功。
- `python -B .\codex-skill-runtime-core\core_cli.py ... selftest ... --live-strict-target ... --live-qa-target ...`
  返回 0。
- strict action-loop 至少有一次真实执行 `read_file` 并到达 FINAL/PASS。
- Godot fixture 由 runtime 启动 headless，并执行 gameplay test。
- 真实 `qa-tester` 子代理输出 `VERDICT` 和 `EVIDENCE MATRIX`，gate 通过。
- `.claude` 在完整自测后 `git diff -- .claude` 为空。
- GitHub Claude skill 仓库的代表 skill 可由 strict runtime 实际运行：
  superpowers、Go skills、Sentry skills、daymade skills、Claude Code superpowers。
- 扩展样本覆盖创业流程、编程命令、官方插件示例、插件 MCP 配置和 Arc 命令。
## 2026-05-23 追加范围

## 2026-05-23 远程 MCP 与记忆补齐

用户确认交互视觉体验不需要完全一致，但如果 skill 依赖远程 MCP，则执行效果仍然要做。公开样本中 DeepBits 插件包含 `https://mcp.deepbits.com/mcp`，官方 plugin-dev 文档也覆盖 SSE/HTTP/WebSocket 与 headersHelper，因此本 change 继续补齐：

- HTTP MCP JSON-RPC POST、SSE endpoint/message、WebSocket best-effort。
- 静态 headers、headersHelper、`${ENV}` 与 `${CLAUDE_PLUGIN_ROOT}` 展开。
- 401/403 认证边界显式 BLOCKED；不伪造 OAuth 成功。
- `summary.json`、`sessions-index.json` 和 prompt memory 注入，用稳定文件机制近似隐藏 system prompt/cache/compact 对连续执行的影响。
- marketplace 完整安装生命周期仍然是非目标。

继续按公开 Claude Code plugin-dev 文档补齐非 CCGS 专用机制：

- 命令/skill body 预处理从 `$ARGUMENTS` 扩展到 `$1/$2`、`@file`、plugin-root 文件引用和动态命令组合。
- plugin manifest 自定义 component 路径按“补充默认目录”实现，覆盖 commands、skills、agents、hooks。
- MCP 发现覆盖插件根 `.mcp.json`、manifest 内联配置和 manifest path 配置。
- hook 执行从“记录命令结果”升级为“解释并执行 hook 决策”：阻断、要求确认、改写输入、exit code 2、prompt hook。
- runtime 生命周期事件补齐 `UserPromptSubmit` 和 `SessionEnd`，Stop/SubagentStop block 进入 gate/exit code。

这仍然不是 Claude Code 私有客户端复刻；目标是让公开 skill/plugin 依赖的可观察执行机制在 Codex runtime 中有同等产出路径。
