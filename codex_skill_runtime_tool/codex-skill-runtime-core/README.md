# Codex Skill Runtime Core

这是一个通用的 Claude Code skill 兼容运行时。它把 Claude Code 风格的 skill、slash command、agent、plugin、hook、MCP、Task、AskUserQuestion 和 session 记忆机制接到 Codex CLI 上。

它不是 CCGS 专用工具。CCGS、2D 美术管线、音频管线、Godot Tool Bridge 都只是可加载的 skill/plugin 仓库。

## 核心概念

- `target_workspace`：Codex 真正工作的目录。读写文件、执行 bash、保存项目记忆，都以它为工作区。
- `skill_repos`：被加载的 skill/plugin 仓库目录。可以同时加载多个，例如 `ccgs`、`art`、`audio`、`godot`。
- `runtime_state_root`：runtime 自己的状态目录，保存 session、memory、job、MCP token、bridge、voice、IDE 等运行状态。
- `Capability Registry`：通用能力注册表。外部服务通过 env、`.codex-skill-runtime/capabilities.json` 或 plugin manifest 暴露给模型，不写死在 core。
- `Job Registry`：Web UI 启动的任务会持久记录到 `jobs/jobs.json`，用于异常关闭后的追溯。

## 运行示例

从工程根目录运行：

```powershell
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env inspect
```

启动一个 skill：

```powershell
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env run /ccgs:start "从零开始制作一个小游戏"
```

指定目标工作区：

```powershell
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env --target-workspace game_projects\my_game run /ccgs:start "从零开始制作一个小游戏"
```

只生成 prompt，不调用 Codex：

```powershell
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env --dry-run run /ccgs:start "测试"
```

## 已实现的通用机制

- 多 skill 仓库同时加载，支持命名空间，例如 `ccgs:start`、`art:2d-art-pipeline`、`audio:game-audio-pipeline`。
- `target_workspace` 与 `skill_repos` 分离，避免 runtime 被某一个 skill 仓库绑定。
- Claude Code plugin manifest 加载，支持 commands、skills、agents、hooks、mcpServers、capabilities。
- 本地插件启停状态持久化，不实现 marketplace 安装生命周期。
- SkillTool 风格短列表预算机制：模型先看到短 skill 列表，需要时再通过 `skill` action 加载完整内容。
- nested skill invocation：skill 内可以继续通过 `skill` action 调用其他 skill。
- agent frontmatter 的 `skills`、`memory`、`mcpServers`、`model`、`effort`、`hooks` 等字段会影响运行。
- `paths` frontmatter 会按 session 中触达过的文件路径过滤 model-invocable skill。
- `allowed-tools` 会作为前置批准集合；未批准工具在非 `assume_yes` 模式会暂停。
- PreToolUse、PostToolUse、PostToolUseFailure、SessionStart、SessionEnd、SubagentStart、SubagentStop、Stop 等 hook 事件。
- `Task`/`Agent` 子代理、并行 Task、SendMessage、TaskStop。
- AskUserQuestion 提问暂停生命周期：问题、选项、回答、resume hint 都会落盘。
- session memory、project memory、agent memory、asset manifest、invoked skill preservation。
- transcript resume 近似：通过 transcript、summary、pending question、memory 重建继续上下文。
- microcompact 和大工具结果落盘，避免长结果反复塞回模型上下文。
- stdio、HTTP、SSE、WebSocket MCP；远程 MCP OAuth/token/header 命令可插拔。
- Bridge、Voice、IDE 的通用本地状态接口。
- Web UI 可观察和控制通用 session/job/plugin/capability。

## 自测

完整自测：

```powershell
python -B codex_skill_runtime_tool\codex-skill-runtime-core\core_cli.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env selftest
```

本次基线结果：

```text
SELFTEST_SUMMARY total=35 failed=0
```

其中 `generic-platform-contract` 专门验证：

- 目标工作区和 skill 仓库分离。
- 多 skill repo 发现。
- `paths` 条件可见性。
- capability registry。
- 本地 plugin enable/disable。
- `allowed-tools` 暂停。
- nested skill action。
- invoked skill 落盘。
- persistent job lifecycle。

## 客观边界

当前 runtime 追求“公开 skill 执行效果层面兼容”，不复制 Claude Code 私有实现。

仍不做的部分：

- Claude Code 私有 UI。
- marketplace 完整安装生命周期。
- 私有隐藏 system prompt 原文照搬。

已经有 clean-room 近似或可插拔替代的部分：

- system prompt 行为规则。
- transcript resume。
- memory / context compression。
- remote MCP token/OAuth 命令层。
- bridge / voice / IDE 本地状态。

因此更准确的表述是：它已经是一个通用 Codex Skill Runtime，可以加载和运行多种 Claude Code skill/plugin 仓库；但不是 Claude Code 客户端源码级复刻。
