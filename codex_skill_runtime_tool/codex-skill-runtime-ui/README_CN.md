# Codex Skill Runtime Web UI

这是一个通用的本地 Web 观察器，不绑定 CCGS。它只读取 Codex Skill Runtime 写出的通用状态文件，并按统一结构展示任意 skill 的执行过程。

## 启动

在工程根目录运行：

```powershell
python -B codex_skill_runtime_tool\runtime-ui.py --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

默认读取：

```text
codex_skill_runtime_tool\config\skill-runtime.env
```

也可以显式指定：

```powershell
python -B codex_skill_runtime_tool\runtime-ui.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env --port 8765
```

## MVP 功能

- 从 UI 查看所有可用 skill，并点击填入 slash command。
- 从 UI 启动任意 slash command，不限制命名空间。
- 可选择 strict tools、QA 模式和最大步骤数。
- 查看历史 session、当前 session 状态、当前 skill、当前 agent、并行 agent 数量。
- 查看任务树，节点类型包括 session、skill、agent、parallel group、tool、gate、question、artifact。
- 查看并行 agent 泳道，并用动画标识运行中或等待用户的状态。
- 查看事件时间线、prompt/stdout/stderr/last-message 等证据文件。
- 预览图片和音频产物。
- 从当前 session 断点继续。
- 回答 pending question 并继续执行。

## 数据来源

UI 优先读取每个 session 内的结构化文件：

```text
session-state.json
task-tree.json
artifacts.json
```

旧 session 如果没有这些文件，UI 会从 `events.jsonl` 尽量重建一棵简化任务树。

## 设计边界

- UI 不知道 CCGS、Godot、美术或音频管线的专有逻辑。
- 工具能力仍由 runtime core、加载的 skill、agent、MCP 和插件决定。
- UI 只负责启动、观察、追溯、继续和回答问题。
- API key 不会从健康检查接口返回；页面只展示配置文件路径和 base URL。
