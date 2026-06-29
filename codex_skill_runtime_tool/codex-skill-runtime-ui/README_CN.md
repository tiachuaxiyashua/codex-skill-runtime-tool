# Codex Skill Runtime Web UI

这是通用 Web 控制台，不绑定 CCGS、Godot、美术或音频管线。它读取 runtime 的通用状态文件，并通过 HTTP API 启动、观察、继续、停止任务。

## 启动

推荐使用工程内的一键脚本：

```powershell
.\codex_skill_runtime_tool\start-runtime.ps1
```

如果你要把它放进 macOS「快捷指令」或直接做成一个手动启动入口，请用：

```bash
./codex_skill_runtime_tool/start-runtime-shortcut-macos.sh
```

这个入口会启动 runtime UI，打开网页，并在你关闭页面后清理本次会话启动的 runtime 及其由 UI 启动的外部服务。

如果是在「快捷指令」里的“运行 Shell 脚本”，不要直接填脚本路径，要填：

```bash
bash "/Users/sanchuan/Documents/chuanproject/claude_code_game_sutdio/codex_skill_runtime_tool/start-runtime-shortcut-macos.sh"
```

如果系统仍然提示 `Operation not permitted`，去「系统设置」里给“快捷指令”开启对“文稿/完整磁盘访问”的权限，或者先把脚本复制到一个快捷指令可读取的位置再执行。

如果是双击启动，可以运行：

```text
codex_skill_runtime_tool\start-runtime.bat
```

脚本会读取：

```text
codex_skill_runtime_tool\config\skill-runtime.env
```

并检查 Codex CLI、Codex API key、Godot 路径，然后启动通用 Web UI。

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

只检查依赖、不启动 UI：

```powershell
.\codex_skill_runtime_tool\start-runtime.ps1 -CheckOnly
```

外部服务不会由 runtime 自动拉起。它们会从 `skill-runtime.env` 的通用 service registry 读取，并在 UI 里手动启动/停止：

```env
SKILL_RUNTIME_SERVICES_JSON={"services":[...]}
```

这个设计是为了保持 runtime 通用：runtime 只知道能力端点、服务状态和可选启动命令，不绑定 Stability Matrix、Forge、ComfyUI 或某个游戏项目的安装方式。

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
