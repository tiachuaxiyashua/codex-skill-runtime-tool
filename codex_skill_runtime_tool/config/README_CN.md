# 本工程内的唯一运行配置

正式配置文件只有这一份：

```text
<tool-root>\config\skill-runtime.env
```

API key 文件在：

```text
<tool-root>\config\codex_api_key.txt
```

约定：

- `skill-runtime.env` 保存模型、代理地址、加载哪些 skill 仓库、runtime 行为开关。
- `codex_api_key.txt` 保存真实 API key，不写进 env，不提交 Git。
- 不再使用 `codex-skill-runtime\skill-runtime.env` 作为配置入口。

本地资产管线地址也写在 `skill-runtime.env`：

```env
# 2D 美术管线，Stability Matrix 启动 Forge/A1111 时需要启用 --api。
SKILL_RUNTIME_ENV_FORGE_BASE_URL=http://127.0.0.1:7860

# 音频管线，Stability Matrix 启动 ComfyUI。
SKILL_RUNTIME_ENV_COMFYUI_BASE_URL=http://127.0.0.1:8188
```

运行器生成的 `config.toml`、`auth.json`、session、memory、MCP token、bridge、voice、IDE 状态都会写到：

```text
<tool-root>\.skill-runtime\
```

被加载的 skill 仓库仍然是独立目录，例如：

```text
<workspace-root>\game_studio_source_code\Claude-Code-Game-Studios
```

`<tool-root>` 是：

```text
codex_skill_runtime_tool
```

`<workspace-root>` 是 `codex_skill_runtime_tool` 的父目录。环境文件中可以直接使用内置变量 `${SKILL_RUNTIME_TOOL_ROOT}` 和 `${SKILL_RUNTIME_WORKSPACE_ROOT}`。
