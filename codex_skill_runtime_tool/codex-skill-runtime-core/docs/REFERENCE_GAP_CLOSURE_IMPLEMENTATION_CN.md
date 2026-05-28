# 参考项目机制差距补全实现记录

生成时间：2026-05-23

## 本轮目标

根据 `<reference-project>` 中的 Claude Code 客户端机制，把当前 Codex runtime 从“能跑 CCGS 验证 skill”推进到“更接近通用 Claude Code skill 执行环境”。本轮优先补会直接影响 skill 执行产出的机制，不实现私有 UI、marketplace 浏览安装界面、Claude Code 私有隐藏 prompt 原文。

## 已补齐的机制

| 机制 | 实现位置 | 当前效果 |
|---|---|---|
| YAML frontmatter 完整解析 | `runtime/frontmatter.py` | 优先使用 PyYAML，支持 nested dict/list，可解析 `hooks`、`mcpServers`、`skills` 等复杂字段 |
| 运行 profile / 隐藏 prompt 近似层 | `runtime/compat.py`、`runtime/prompts.py` | 为 skill/agent prompt 注入 clean-room 兼容指令、output style、permission mode、model/effort、coordinator/scratchpad 说明 |
| per-run model / effort | `runtime/codex_cli.py`、`runtime/runtime.py` | skill/agent frontmatter 的 `model`、`effort` 可映射到 Codex CLI 单次调用；Claude 别名通过环境变量映射 |
| 多来源 skill/agent/config | `runtime/loaders.py` | 支持项目、父级 `.claude`、用户 `.claude`、managed 目录、`--add-dir`、plugin、bundled skills |
| bundled skill registry | `runtime/compat.py`、`runtime/loaders.py` | 提供 `verify`、`remember`、`skillify`、`simplify`、`stuck`、`batch`、`loop`、`claude-api`、`dream` 的本地兼容 stub |
| model-invocable skill registry | `runtime/loaders.py`、`runtime/runtime.py` | 识别 `disable-model-invocation`、`paths`，并在上下文中列出当前可见 skill |
| `paths` 条件匹配 | `runtime/compat.py` | 可按 touched path 判断条件 skill 是否可见 |
| skill/agent inline hooks | `runtime/hooks.py`、`runtime/runtime.py` | skill/agent frontmatter 中的 `hooks` 会作为 inline hook source 合并进 HookDispatcher |
| agent `skills` preload | `runtime/runtime.py` | 子代理 prompt 会预加载 agent frontmatter 声明的 skills |
| agent `memory` | `runtime/memory.py`、`runtime/runtime.py`、`runtime/tool_executor.py` | 支持 user/project/local agent memory 读取与写入工具 |
| agent `mcpServers` | `runtime/mcp.py`、`runtime/tool_executor.py`、`runtime/runtime.py` | agent 可声明私有 MCP server，MCP 调用时合并进可用 server 列表 |
| 子代理 strict tool loop | `runtime/runtime.py` | strict 主流程中 Task/Agent 启动的子代理可进入 runtime-owned strict action-loop |
| Worker registry | `runtime/workers.py`、`runtime/tool_executor.py` | Task/Agent 返回 worker id，可被后续 SendMessage / TaskStop 定位 |
| SendMessage | `runtime/tool_executor.py` | 可继续已有 worker，保留最近 worker prompt/output 作为上下文 |
| TaskStop | `runtime/tool_executor.py` | 可标记 worker stopped，并允许后续 SendMessage 再继续 |
| 大工具结果落盘 | `runtime/large_results.py`、`runtime/tool_executor.py` | 大字符串结果写入 session 文件，模型只收到 preview 和完整路径 |
| readFileState 近似 | `runtime/session.py`、`runtime/tool_executor.py` | Read 工具记录模型看到过的文件内容快照 |
| 远程 MCP auth command | `runtime/mcp.py` | 支持 `authCommand` / `oauthRefreshCommand` / `tokenCommand` 动态返回 headers 或 token |
| CLI add-dir/output-style | `runtime/cli.py` | 支持 `--add-dir` 扩大加载范围，支持 `--output-style` 改变 prompt profile |

## 新增自测

本轮新增并通过的自测：

- `compat-gap-contract`：验证 nested YAML frontmatter、`paths`、bundled skill registry。
- `worker-registry-contract`：验证 Task -> worker id -> SendMessage -> TaskStop。
- `large-tool-result-contract`：验证大 Read 结果写盘并替换为 preview。
- `model-effort-command-contract`：验证 per-run model 和 reasoning effort 被写入 Codex CLI 命令。
- `mcp-bridge-contract` 扩展：验证 HTTP MCP `authCommand` 动态 token。

## 验证结果

已运行：

```text
python -B -m compileall .\codex-skill-runtime-core
python .\codex-skill-runtime-core\core_cli.py --dry-run --assume-yes --qa off selftest
python .\codex-skill-runtime-core\core_cli.py --assume-yes --qa off strict-smoke README.md
```

结果：

```text
compileall: PASS
selftest: SELFTEST_SUMMARY total=22 failed=0
live strict-smoke: STRICT-SMOKE PASS
```

最近 live strict-smoke session：

```text
.codex-skill-runtime/sessions/20260523-172638-strict-smoke
```

## 仍不能称为 100% 等同的部分

以下机制仍不能客观宣称与 `<reference-project>` 完全一致：

- Claude Code 私有隐藏 system prompt 原文：当前是 clean-room 兼容 prompt，不是私有 prompt 克隆。
- 完整 OAuth UI / browser callback / keychain 生命周期：当前用 `authCommand` 等可插拔命令覆盖执行层，不做私有 UI。
- 完整 transcript resume：当前有 summary、read-state、tool result evidence，但不是 Claude Code JSONL transcript replay。
- 完整 microcompact / cache edit API：当前实现大结果替换和 session summary，不等价参考项目的 cached microcompact。
- 真异步后台 worker：当前 worker registry 是同步执行后可继续，不是完整后台 event loop。
- Bridge、Voice、Chrome、IDE、marketplace 生命周期：这些主要是客户端交互和安装生态，本轮没有实现。

## 当前客观结论

当前 runtime 已经补齐了大量会影响普通公开 skill、编程 skill、一人公司 skill 执行效果的机制：frontmatter、agent preload、agent memory、agent MCP、worker continuation、strict subagent、大输出、model/effort、多来源加载和 MCP auth command。

但它仍不是 Claude Code 客户端完整复刻。更准确的定位是：一个面向 Codex CLI 的 Claude Code skill 执行兼容 runtime，已经覆盖主要执行链路和多代理协作链路；私有 UI、完整 OAuth 生命周期、真实 transcript replay、cached microcompact 和远程 bridge 仍是近似或未实现。

## 2026-05-29 通用平台补齐

本轮按照参考工程的机制分层继续补齐 1-9 条差距，目标是不让 runtime 退化为游戏专用工具。

| 差距 | 当前实现 | 影响 |
|---|---|---|
| `target_workspace` 与 `skill_repos` 分离 | `RuntimeConfig.target_workspace`、`RuntimeConfig.skill_repos`、`SKILL_RUNTIME_TARGET_WORKSPACE`、`SKILL_RUNTIME_SKILL_REPOS` | Codex 在目标项目中执行，skill/plugin 从独立仓库加载 |
| 通用 capability registry | `runtime/capabilities.py`、`capability_list` action、UI `/api/capabilities` | Forge/Comfy/Godot 等能力通过配置或 plugin 暴露，不写死 core |
| persistent job lifecycle | `runtime/jobs.py`、UI `/api/jobs` | UI 启动任务后可追溯 pid、stdout、stderr、状态和取消请求 |
| SkillTool/frontmatter 更接近参考工程 | touched paths、短列表预算、`allowed-tools` 前置批准、invoked skill preservation | 模型先看短列表，需要时 nested 调用 skill；路径型 skill 不再总是暴露 |
| 本地 plugin lifecycle | `runtime/plugins.py`、CLI `plugins/plugin enable/disable`、UI 插件启停 | 支持启停本地 plugin，不实现 marketplace |
| permission/hook 兼容 | `PostToolUseFailure`、`allowed-tools` 暂停、hook updated input 继续生效 | 工具失败和权限暂停更接近 Claude Code 执行链 |
| context/memory/resume | invoked skill 落盘、project/agent memory、summary index、microcompact | 长任务和恢复时更能保留已加载 skill 与项目记忆 |
| 通用 UI 控制台 | Web UI 显示 target、skills、sessions、task tree、jobs、capabilities、plugins | 不再只是观察器，可以启动、回答、继续、停止、启停插件 |
| 通用兼容性基准 | selftest `generic-platform-contract` | 不依赖 CCGS，验证任意 skill/plugin 仓库的核心机制 |

本轮实测：

```text
python -B -m compileall codex_skill_runtime_tool\codex-skill-runtime-core codex_skill_runtime_tool\codex-skill-runtime-ui\backend
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env inspect
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env selftest --godot-project game_projects\marble_spiral_runtime
```

结果：

```text
SELFTEST_SUMMARY total=35 failed=0
```

Web UI 烟测：

```text
GET /api/health
GET /api/capabilities
GET /api/jobs
```

结果：

```text
ok=true, target=E:\chuan_project\claude_code_game_sutdio, capabilities=2, jobs=0
```

剩余边界：

- marketplace 完整生命周期仍不做。
- 私有 system prompt 原文不复制，只保留 clean-room 行为规则。
- Claude Code 私有 UI 不复制。
- 远程 MCP 浏览器 OAuth/keychain 的完整体验不复制，但执行层 token/header/OAuth 命令与 BLOCKED 生命周期已存在。
