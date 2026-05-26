# Claude Code Skill 兼容性审计

## 结论

审计前的 runtime 确实带有 CCGS 硬编码，不能诚实地说“只要是 Claude Code
skill 都能跑”：

- loader 只认 `.claude/skills`。
- loader 强制要求 `.claude/agents` 存在。
- settings 只认 `.claude/settings.json`。
- strict schema path 假设 runtime 位于被测项目根目录。
- prompt 文案把所有 skill 都称为 CCGS。
- runtime action 只覆盖了 CCGS 首轮 Godot 验证用到的主要工具，没有覆盖
  `Skill`、`Agent`、`TodoWrite`、`WebFetch`、`WebSearch`、`MultiEdit`。
- plugin `hooks/hooks.json` 中的 `type: skill` hook 没有注入目标 skill。
- fallback JSON action 只认 `{tool, parameters}`，不认一些模型会返回的
  `{type, path, ...}` 形状。
- skill frontmatter 的 `allowed-tools` 没有被 strict tool executor 执行层限制。

本轮已经把这些点中可直接泛化的部分补上。当前 runtime 已不只支持 CCGS
这一套 skill，它能加载并运行多种 GitHub Claude skill 布局。

## 本轮下载的 GitHub Claude Skill 仓库

所有仓库都下载在：

```text
.codex-skill-runtime/external-repos/
```

| 本地目录 | GitHub 仓库 | 发现的 skill 数 | 主要布局 |
|---|---|---:|---|
| `superpowers` | `obra/superpowers` | 14 | `skills/<name>/SKILL.md` + supporting files + hooks |
| `claude-code-superpowers` | `TechyMT/claude-code-superpowers` | 16 | `skills/<name>/SKILL.md` + `hooks/hooks.json` 的 skill hooks |
| `daymade-claude-code-skills` | `daymade/claude-code-skills` | 55 | 根目录和多层子目录直接放 `<skill>/SKILL.md` |
| `cc-skills-golang` | `samber/cc-skills-golang` | 42 | `skills/<name>/SKILL.md` + 大量 references + 参数化 allowed-tools |
| `getsentry-skills` | `getsentry/skills` | 27 | `skills/<name>/SKILL.md` + repo-root `agents/` |

OpenAI curated skill 没有用于本结论。先前为了基线临时安装的三个 OpenAI
skill 已删除。

## 外部 Skill 暴露出的机制

### 1. 布局不止 `.claude/skills`

CCGS 使用：

```text
.claude/
  skills/
  agents/
  docs/
  settings.json
```

外部仓库常见布局还有：

```text
skills/<name>/SKILL.md
agents/<agent>.md
hooks/hooks.json
```

以及：

```text
<name>/SKILL.md
group/<name>/SKILL.md
```

如果 loader 只认 CCGS 布局，它无法发现 superpowers、Go skills、Sentry skills
和 daymade skills。

### 2. 有些 skill 没有单独 agent

superpowers 和 Go skills 可以只有 skill，没有 `.claude/agents`。这时 Claude Code
仍可执行 skill，runtime 也必须给它一个通用主代理壳，而不能因为 agents 目录缺失
直接报错。

### 3. Skill 会引用 supporting files

示例：

- superpowers 的 `systematic-debugging` 引用 `root-cause-tracing.md`、
  `defense-in-depth.md` 等文件。
- Go skills 的 `golang-error-handling` 引用
  `references/error-handling.md`。
- Sentry skills 有 `SPEC.md`、`SOURCES.md`、references 等维护文件。

Runtime 现在会在 prompt 中列出当前 skill 旁边的 supporting files，并提供
`read_file` 读取这些文件。

### 4. Skill 会再调用 Skill

superpowers 的 `using-superpowers` 明确要求先调用 Skill tool。本轮新增了
`skill` runtime action。真实测试中：

1. `/using-superpowers` 第一步返回 `skill` action。
2. runtime 加载 `systematic-debugging`。
3. 返回该 skill body 和 supporting file 列表。
4. Codex 第二步给出 FINAL。

### 5. 有些仓库把 hook 作为 skill 注入器

`TechyMT/claude-code-superpowers/hooks/hooks.json` 的 hook type 是：

```json
{
  "type": "skill",
  "skill": "claude-code-superpowers/domain-model"
}
```

这不是 CCGS 使用的 command hook。Runtime 现在能在 SessionStart matcher
命中时记录该 hook，并把目标 skill body 注入 prompt。

### 6. 工具名不只 CCGS 首轮工具

CCGS 73 个 skill 的 frontmatter 工具出现次数：

| Tool | 出现次数 |
|---|---:|
| `Read` | 73 |
| `Glob` | 73 |
| `Grep` | 73 |
| `Write` | 64 |
| `AskUserQuestion` | 46 |
| `Task` | 39 |
| `Bash` | 32 |
| `Edit` | 28 |
| `TodoWrite` | 10 |
| `WebSearch` | 2 |
| `WebFetch` | 1 |

CCGS Godot 验证首轮主要覆盖 Read/Glob/Grep/Write/Edit/Bash/Task/
AskUserQuestion/Godot。它没有充分证明 TodoWrite 和 Web 工具。

外部仓库又暴露出：

- `Skill` tool。
- `Agent` tool。
- `MultiEdit` 风格批量替换。
- `mcp__...` 工具声明。
- `allowed-tools: Bash(go:*)`、`WebFetch(domain:...)` 这类参数化限制。

## 本轮泛化补丁

### Loader

已支持：

- `.claude/skills/<name>/SKILL.md`
- `skills/<name>/SKILL.md`
- `<name>/SKILL.md` under root
- root `agents/` 和 `.claude/agents/`
- root `CLAUDE.md`、`AGENTS.md`、`GEMINI.md`、`README.md`
- `.claude/settings.json` 和 `hooks/hooks.json`

### Prompt / Invocation

已支持：

- 通用 Claude skill 文案，不再把外部 skill 强行叫 CCGS。
- supporting file manifest。
- `$ARGUMENTS` 和 `$ARGUMENTS[index]` 的基础替换。
- 无 agent 时 synthetic main agent。

### Strict Actions

已支持：

- `read_file`
- `glob`
- `grep`
- `write_file`
- `edit_file`
- `multi_edit`
- `bash`
- `task`
- `agent`
- `ask_user_question`
- `todo_write`
- `skill`
- `web_fetch`
- `web_search`
- `godot_smoke`
- `mcp` 占位 action

另外增加 Claude 工具名别名，例如 `Read`、`Task`、`Skill`、`TodoWrite`
即使出现在 fallback action 中也能映射到 runtime action。

### Enforcement

已补：

- `.claude` 写保护。
- `.claude/settings.json` Bash deny 规则。
- strict 主 skill 的 `allowed-tools` 基础限制。
- `Bash(cmd:*)`、`WebFetch(domain:...)`、`mcp__...` 的基础匹配。

## 实跑验证

### Superpowers

命令：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py --root .codex-skill-runtime\external-repos\superpowers --strict-tools --assume-yes --qa off --max-steps 2 run /using-superpowers "Start a conversation and identify which skill applies to fixing a flaky test. Do not modify files."
```

结果：

- Session：`20260523-003317-strict-using-superpowers`
- 第一步执行 `skill` action 加载 `systematic-debugging`
- STRICT PASS

### Go Skills

命令：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py --root .codex-skill-runtime\external-repos\cc-skills-golang --strict-tools --assume-yes --qa off --max-steps 3 run /golang-error-handling "Explain the single handling rule; do not modify files."
```

结果：

- Session：`20260523-003813-strict-golang-error-handling`
- runtime 实际读取 `skills/golang-error-handling/references/error-handling.md`
- STRICT PASS

### Sentry Skills

命令：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py --root .codex-skill-runtime\external-repos\getsentry-skills --strict-tools --assume-yes --qa off --max-steps 2 run /code-review "Review README.md for one concrete documentation risk only. Do not modify files."
```

结果：

- Session：`20260523-002248-strict-code-review`
- runtime 实际读取 `README.md`
- STRICT PASS

### Daymade Skills

命令：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py --root .codex-skill-runtime\external-repos\daymade-claude-code-skills --strict-tools --assume-yes --qa off --max-steps 2 run /prompt-optimizer "Give one concise rule for improving an evaluation prompt. Do not modify files."
```

结果：

- Session：`20260523-002828-strict-prompt-optimizer`
- 直接子目录/分组子目录 layout 被发现
- STRICT PASS

### Claude Code Superpowers Hook Skill

命令：

```powershell
python -B .\codex-skill-runtime-core\core_cli.py --root .codex-skill-runtime\external-repos\claude-code-superpowers --strict-tools --assume-yes --qa off --max-steps 2 run /skill-and-command-dispatch "Explain how a tool capability should choose between a slash command and a skill. Do not modify files."
```

结果：

- Session：`20260523-002828-strict-skill-and-command-dispatch`
- SessionStart 记录了 hook skill：
  - `domain-model`
  - `build-tool-factory`
  - `skill-and-command-dispatch`
- Prompt 中出现 `Hook-Injected Skill`
- STRICT PASS

## 本轮新增 GitHub 样本与机制覆盖

本轮继续从 GitHub 下载并验证了更多 Claude Code skill/plugin 仓库：

| 本地目录 | GitHub 仓库 | 新暴露机制 | 当前结果 |
|---|---|---|---|
| `the-startup` | `rsmdt/the-startup` | `.claude-plugin/plugin.json`、插件 namespace、嵌套 skills、递归 agents、一人公司/创业公司工作流 | `/start:review` live strict PASS |
| `arc` | `howells/arc` | repo-root `commands/`、命令包装器、`context: fork`、大量编程/审计 agent | `/arc:using-arc` live strict PASS；`/arc:audit` 证明会启动 fork 子会话，但完整 audit 超出短回归时长 |
| `coderabbit-skills` | `coderabbitai/skills` | 编程 review command、`` !`command` `` 动态上下文、参数化 `allowed-tools` | live strict 正确 BLOCKED：本机缺 CodeRabbit CLI，runtime 已执行前置 Bash 检查 |
| `deepbits-claude-plugins` | `DeepBitsTechnology/claude-plugins` | 插件命令、agent、远程 MCP 配置 | loader PASS；远程 HTTP MCP 配置已可进入真实 bridge，缺认证/网络时明确 BLOCKED |
| `anthropic-claude-code-public/plugins/*` | `anthropics/claude-code` | 官方插件样本、hook `${CLAUDE_PLUGIN_ROOT}`、command/agent/skill/plugin-dev/MCP 文档 | plugin hook、stdio/HTTP/SSE MCP bridge、headersHelper selftest PASS |

新增 runtime 能力：

- 发现 `.claude/commands/**/*.md`、repo-root `commands/**/*.md` 和插件 `commands/**/*.md`。
- 发现 `.claude-plugin/plugin.json`，按插件名生成 `/plugin:command` 与 `/plugin:skill` namespace。
- 发现插件 `skills/**/SKILL.md`、插件 `agents/**/*.md`、插件 `hooks/hooks.json`。
- 合并插件 hook，并替换 `${CLAUDE_PLUGIN_ROOT}`。
- 渲染 `$ARGUMENTS`、`$ARGUMENTS[index]` 和 `` !`command` `` 动态上下文。
- 修正 `allowed-tools` 语义：它是预批准提示，不再作为硬白名单；runtime 阻断来自 settings `deny`/`ask`。
- `Skill` action 加载 `context: fork` skill 时启动独立 Codex 子会话，并把父命令 arguments 传入 fork prompt。
- 读取 `.mcp.json` 和插件 `mcpServers`，真实桥接 stdio、HTTP、SSE、WebSocket MCP；远程 OAuth 缺少 headers/token 时明确 BLOCKED。

## 仍然存在的边界

### 1. MCP 已桥接远程传输，但不复刻完整 OAuth UI

Runtime 现在可以读取 `.mcp.json` 和插件 `mcpServers`，并用 stdio JSON-RPC、
HTTP JSON-RPC、SSE endpoint/message 和 WebSocket best-effort 真实调用
`mcp__server__tool`。它支持静态 `headers`、`${ENV}` 展开、`${CLAUDE_PLUGIN_ROOT}`
展开和 `headersHelper`。仍不复刻 Claude Code 私有 OAuth 浏览器 UI、动态客户端注册、
token 刷新和 needs-auth 菜单；缺少可用认证材料时会明确 BLOCKED。

### 2. Plugin 生命周期仍未完整模拟

Claude Code marketplace/plugin 还包含安装、启用、命名空间、插件根路径环境变量、
版本、依赖、缓存、marketplace 安装和启停生命周期。本 runtime 已能读取插件默认
component 目录、namespace、hook、agent、command 和 skill，但不是完整 plugin manager。

### 3. `context: fork` 是可观察近似

Runtime 的每次 `core_cli.py run` 本来就是单独 Codex exec，所以天然有隔离。
Skill action 遇到 `context: fork` 时会启动独立 Codex 子会话，并把父命令 arguments
带入。边界是：fork 子会话内部如果由 Codex CLI 自己运行 shell/build，runtime
无法逐微事件拦截，只能记录子会话 prompt/stdout/stderr。

### 4. 权限模型仍没有 UI

Runtime 已有 `.claude` 写保护、settings `deny`/`ask`/`allow`、`--assume-yes`
自动通过 ask，以及 skill `allowed-tools` 预批准记录。它没有完整复制 Claude Code
的交互式 permission UI、企业 managed policy 和所有 provider 级策略。

### 5. 自动 skill discovery 仍是显式运行时入口

Runtime 能通过 `skill` action 加载另一个 skill，也能执行 hook-injected skill。
但它不是 Claude Code 原生 Skill tool 的全局索引器，不会自动把用户机器上所有
Claude Code skill 与 plugin 全部注册进当前运行根。

## 当前判断

- **不是仅支持 CCGS**：已经实跑/探测 10 个 GitHub Claude skill/plugin 仓库中的代表入口。
- **仍保留 CCGS 特化**：Godot prototype QA gate、`/prototype`、`/team-qa`
  workflow plan 是为了 CCGS 质量闭环保留的特化路径。
- **泛化方向正确**：结构、commands、plugin components、Skill tool、supporting files、
  Agent/Task、TodoWrite、Web actions、hook skill、权限规则、stdio/HTTP/SSE/WebSocket MCP 和
  `context: fork` 都已经补到 runtime 层。
- **不能夸大**：涉及完整 marketplace 安装生命周期、远程 MCP OAuth 浏览器/刷新生命周期、Claude Code 私有 UI、
  企业权限策略和模型内部 prompt 时，还不能说已完全等价。
## 2026-05-23 继续补齐后的审计结论

本轮继续以公开 GitHub skill/plugin 和 `anthropics/claude-code` public plugin-dev 文档为白盒参考，补的是协议层，不改原始 `.claude` skill。

新增覆盖：

1. **命令预处理**：`$1/$2`、`$ARGUMENTS[index]`、`@file`、plugin-root 文件引用、动态命令上下文。
2. **插件 manifest**：自定义 `commands/skills/agents/hooks` 路径补充默认目录，不替代默认目录。
3. **MCP**：插件根 `.mcp.json`、manifest 内联 `mcpServers`、manifest 路径型 `mcpServers`，以及 stdio/HTTP/SSE/WebSocket bridge。
4. **Hook 决策**：PreToolUse 可以通过 `permissionDecision` 阻断或要求确认，可以用 `updatedInput` 改写工具输入；exit code `2`、`continue:false`、`decision:block|deny` 都会被 runtime 执行。
5. **Hook 事件**：新增 `UserPromptSubmit`、`SessionEnd`，并补齐 Claude-like hook payload 字段。
6. **Prompt hook**：`type: prompt` 有 Codex runner 入口，要求返回 JSON 决策。

新增 selftest：

- `command-preprocessing-contract`
- `plugin-manifest-contract`
- `hook-decision-contract`

当前普通 selftest 结果：`SELFTEST_SUMMARY total=18 failed=0`。

仍然不承诺的边界：

- 不复刻 Claude Code 私有客户端源码、隐藏 system prompt、私有 UI、模型内部缓存。
- 不复刻远程 MCP 的完整 OAuth 浏览器/刷新生命周期；HTTP/SSE/WebSocket 已桥接，缺少 headers/token/OAuth 成果时仍明确 BLOCKED。
- 不实现完整 marketplace 安装/启停/版本管理，只加载本地已存在的公开 plugin 布局。

## 2026-05-23 远程 MCP 与记忆补齐审计

公开样本显示远程 MCP 不是理论问题：DeepBits 插件有 `https://mcp.deepbits.com/mcp`，官方 plugin-dev 文档也写了 SSE、HTTP、WebSocket 与 headersHelper。因此 runtime 继续补齐：

- HTTP MCP：JSON-RPC POST、`Accept: application/json, text/event-stream`、`Mcp-Session-Id`。
- SSE MCP：GET stream 获取 `endpoint`，POST initialize/tool-call，按 JSON-RPC id 匹配响应。
- WebSocket MCP：如果本机安装 `websocket-client`，按 JSON-RPC over WS 调用；未安装则明确 BLOCKED。
- headersHelper：传入 `CLAUDE_CODE_MCP_SERVER_NAME`、`CLAUDE_CODE_MCP_SERVER_URL`，并校验输出是字符串值 JSON object。
- 记忆/压缩近似：每个 session 写 `summary.json`，全局写 `.codex-skill-runtime/sessions-index.json`，后续 prompt 注入 bounded runtime memory。这个机制不声称等同 Claude Code 私有缓存，但能覆盖 skill 执行连续性需要的可观察效果。
