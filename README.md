# Codex Skill Runtime Tool

Codex Skill Runtime Tool 是一个面向 Codex CLI 的 Claude Code skill 运行器。它的目标是让 Codex 以接近 Claude Code 的执行机制加载和运行 Claude Code 风格的 skill、agent、hook、MCP 工具和项目记忆，而不是把某一个 skill 写死进程序里。

当前仓库默认组合了四类能力：

- `codex_skill_runtime_tool/`：通用 runtime 核心、CLI、状态机、结构化 action loop、memory、MCP、hook、agent/task 调度和验证逻辑。
- `art_pipeline_skill/`：2D 美术资产生产 skill，可对接 Forge/A1111 兼容图像后端。
- `audio_pipeline_skill/`：游戏音频生产 skill，用于 BGM、音效、语音和怪物声音等资产流程。
- `godot_tool_bridge_skill/`：Godot 工具桥 skill，用于把 Godot 检查、导入和 headless smoke test 从 runtime core 中独立出来。
- `game_studio_source_code/Claude-Code-Game-Studios`：上游 CCGS 作为 submodule 引入，只作为可加载 skill 仓库之一。

## 设计目标

- 通用加载任意 Claude Code skill 仓库，不特化为 CCGS 或游戏项目。
- 支持多仓库 namespace，例如 `ccgs:start`、`art:2d-art-pipeline`、`audio:game-audio-pipeline`、`godot:godot-tool-bridge`。
- 支持 skill 内继续调用 skill 的 nested invocation。
- 支持结构化 action，包括文件读写、命令执行、agent/task、MCP、项目记忆、资产登记和用户提问暂停。
- 使用独立 `CODEX_HOME`，隔离本工具和本机其他 Codex 配置。
- 把 API key 放在工程内私密文件，不写入 env，不提交 Git。

## 快速开始

克隆仓库并拉取 submodule：

```powershell
git clone --recurse-submodules <repo-url>
cd <repo-dir>
```

如果已经克隆但没有拉取 submodule：

```powershell
git submodule update --init --recursive
```

配置 API key：

```powershell
$key = Read-Host "Codex API Key"
Set-Content -NoNewline -Encoding ASCII -LiteralPath ".\codex_skill_runtime_tool\config\codex_api_key.txt" -Value $key.Trim()
```

唯一运行配置文件是：

```text
codex_skill_runtime_tool\config\skill-runtime.env
```

检查 runtime 能加载哪些 skill：

```powershell
python -B codex_skill_runtime_tool\skill-runtime.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env inspect
```

运行真实 Codex live smoke test：

```powershell
python -B codex_skill_runtime_tool\skill-runtime.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env strict-smoke README.md
```

打开交互界面：

```powershell
python -B codex_skill_runtime_tool\skill-runtime.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env ui
```

## 配置说明

`codex_skill_runtime_tool\config\skill-runtime.env` 是唯一正式配置入口。它会生成独立的：

```text
codex_skill_runtime_tool\.skill-runtime\codex-home\config.toml
codex_skill_runtime_tool\.skill-runtime\codex-home\auth.json
```

这些文件只供 runtime 启动的 Codex 子进程使用，不污染本机默认 Codex 配置。

真实 API key 存放在：

```text
codex_skill_runtime_tool\config\codex_api_key.txt
```

该文件已被 `.gitignore` 忽略。

默认本地管线地址：

```env
# 2D 美术管线：Stability Matrix 启动的 Forge/A1111，启动参数需要包含 --api。
SKILL_RUNTIME_ENV_FORGE_BASE_URL=http://127.0.0.1:7860

# 音频管线：Stability Matrix 启动的 ComfyUI。
SKILL_RUNTIME_ENV_COMFYUI_BASE_URL=http://127.0.0.1:8188
```

## 当前验证

已经完成一次真实 Codex API live 验证，不是 dry-run：

```powershell
python -B codex_skill_runtime_tool\skill-runtime.py --runtime-env codex_skill_runtime_tool\config\skill-runtime.env strict-smoke README.md
```

结果：

```text
STRICT-SMOKE PASS
session: codex_skill_runtime_tool\.skill-runtime\state\sessions\20260527-001017-strict-smoke
```

验证证据包括：

- Codex CLI 子进程真实启动。
- 使用 OpenAI-compatible proxy 的 Responses wire API。
- runtime strict action loop 执行两轮。
- `read_file` action 实际读取 CCGS submodule 的 `README.md`。
- 最终 gate 为 `STRICT-SMOKE PASS`。

## 目录结构

```text
.
├─ codex_skill_runtime_tool/        # 通用 runtime
├─ art_pipeline_skill/              # 2D 美术资产 skill/plugin
├─ audio_pipeline_skill/            # 游戏音频资产 skill/plugin
├─ godot_tool_bridge_skill/         # Godot 工具桥 skill/plugin
├─ game_studio_source_code/         # 上游 skill 仓库 submodule 目录
├─ .gitmodules
├─ .gitignore
├─ LICENSE
└─ README.md
```

## 注意

本项目不是 Claude Code 的复制品，也不包含 Claude Code 私有 system prompt。runtime 采用 clean-room 方式实现可观察执行机制，包括 skill 发现、frontmatter 解释、action loop、工具调度、MCP、memory、hook 和提问暂停生命周期。
