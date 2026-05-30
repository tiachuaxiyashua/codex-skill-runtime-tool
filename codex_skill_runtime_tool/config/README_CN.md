# Runtime 配置说明

本工程只保留一个运行配置入口：

```text
codex_skill_runtime_tool\config\skill-runtime.env
```

真实 API key 放在：

```text
codex_skill_runtime_tool\config\codex_api_key.txt
```

不要把真实 API key 写进 git。

## 关键配置

```env
SKILL_RUNTIME_TARGET_WORKSPACE=...
SKILL_RUNTIME_SKILL_REPOS=...
SKILL_RUNTIME_NAMESPACES=...
SKILL_RUNTIME_STATE_ROOT=...
SKILL_RUNTIME_CODEX_HOME=...
CODEX_SKILL_RUNTIME_BARE=true
SKILL_RUNTIME_ENV_<NAME>=...
CODEX_BASE_URL=...
CODEX_API_KEY_FILE=...
```

含义：

- `SKILL_RUNTIME_TARGET_WORKSPACE`：Codex 真正工作的目录。
- `SKILL_RUNTIME_SKILL_REPOS`：要加载的 skill/plugin 仓库列表，使用 `;` 分隔。
- `SKILL_RUNTIME_NAMESPACES`：给 skill repo 绑定命名空间，例如 `ccgs=...;art=...`。
- `SKILL_RUNTIME_STATE_ROOT`：session、memory、job、plugin state 等 runtime 自己的状态目录。
- `SKILL_RUNTIME_CODEX_HOME`：隔离的 Codex 配置目录，避免污染本机其他 Codex 配置。
- `CODEX_SKILL_RUNTIME_BARE`：为 `true` 时不读取用户全局 `.claude` skills/settings，只读取目标工作区和显式 skill repo。
- `SKILL_RUNTIME_ENV_<NAME>`：注入 runtime 父进程的环境变量，供 plugin/skill 脚本读取，例如 `SKILL_RUNTIME_ENV_GODOT_EXE`。
- `CODEX_BASE_URL`：你的 Codex/OpenAI 兼容代理地址。
- `CODEX_API_KEY_FILE`：API key 文件路径。
- `SKILL_RUNTIME_QA_AUTO_PATTERNS`：`qa=auto` 时触发 QA 的可配置模式，格式是 `command:argument-glob`，使用 `;` 分隔。

## 内置变量

env 文件支持：

```text
${SKILL_RUNTIME_TOOL_ROOT}
${SKILL_RUNTIME_WORKSPACE_ROOT}
```

其中：

- `SKILL_RUNTIME_TOOL_ROOT` 是 `codex_skill_runtime_tool`。
- `SKILL_RUNTIME_WORKSPACE_ROOT` 是 `codex_skill_runtime_tool` 的父目录。

## Capability Registry 配置

外部服务不要写死进 runtime core。可以通过通用 capability 暴露：

```env
SKILL_RUNTIME_CAPABILITY_FORGE_ENDPOINT=http://127.0.0.1:7860
SKILL_RUNTIME_CAPABILITY_FORGE_KIND=image-generation-api
SKILL_RUNTIME_CAPABILITY_FORGE_STATUS=configured
SKILL_RUNTIME_CAPABILITY_FORGE_DESCRIPTION=Stability Matrix Forge or A1111-compatible backend exposed with --api.
```

也可以在 skill/plugin 仓库里放：

```text
.codex-skill-runtime\capabilities.json
```

或在 `.claude-plugin\plugin.json` 中写 `capabilities` 字段。

## 当前目录约定

runtime 自己产生的文件都在工程内：

```text
codex_skill_runtime_tool\.skill-runtime\
```

被加载的 skill 仓库是独立目录，例如：

```text
game_studio_source_code\Claude-Code-Game-Studios
art_pipeline_skill
audio_pipeline_skill
godot_tool_bridge_skill
```

这些目录是 skill/plugin，不是 runtime core。
