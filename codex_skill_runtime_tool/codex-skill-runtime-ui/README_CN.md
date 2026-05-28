# Codex Skill Runtime Web UI

这是通用 Web 控制台，不绑定 CCGS、Godot、美术或音频管线。它读取 runtime 的通用状态文件，并通过 HTTP API 启动、观察、继续、停止任务。

## 启动

在工程根目录运行：

```powershell
python -B codex_skill_runtime_tool\runtime-ui.py --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

默认配置文件：

```text
codex_skill_runtime_tool\config\skill-runtime.env
```

显式指定配置：

```powershell
python -B codex_skill_runtime_tool\runtime-ui.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env --port 8765
```

## UI 能看到什么

- 当前目标工作区。
- 当前加载的 skill 列表。
- 当前 session 状态、当前 skill、当前 agent、并行 agent 数。
- 历史任务树。
- agent 泳道和运行状态动画。
- 工具调用、hook、gate、question、artifact。
- 图片、音频、文档类产物预览。
- prompt、stdout、stderr、last-message、strict-result 等证据文件。
- pending question，并可直接回答后继续。
- 持久 Job 列表，并可请求停止。
- Capability Registry。
- 本地 plugin 启用/停用。

## UI 不做什么

- 不把 CCGS 写死进界面。
- 不理解某个游戏、美术、音频 skill 的内部业务流程。
- 不保存或显示 API key。
- 不替代 skill 本身的决策，只负责启动、观察、继续和控制 runtime。

## 数据来源

每个 session 主要读取：

```text
session-state.json
task-tree.json
artifacts.json
events.jsonl
transcript.jsonl
summary.json
pending-question.json
```

持久 Job 读取：

```text
<state-root>\jobs\jobs.json
```

插件启停状态读取：

```text
<state-root>\plugins\plugins.json
```
