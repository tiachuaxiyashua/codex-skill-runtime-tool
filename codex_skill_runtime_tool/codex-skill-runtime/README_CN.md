# Codex Skill Runtime 通用操作界面

## 推荐配置方式：本工程内环境文件和独立 Codex 配置

现在推荐使用 `--runtime-env` 启动。它解决两个问题：

1. 代理地址、模型、Godot 路径等配置集中写在一个环境文件中，不必每次输入长命令。
2. 运行器会给自己使用的 Codex 子进程设置独立的 `CODEX_HOME`，生成独立的 `config.toml` 和 `auth.json`，不会读取或修改本机其他 Codex 会话默认使用的 `%USERPROFILE%\.codex`。
3. 运行器自己的 session、memory、MCP token、bridge、voice、IDE 状态会写到 `SKILL_RUNTIME_STATE_ROOT`，不会写进被加载的 skill 仓库。

本工程已经提供正式配置文件：

```text
<tool-root>\config\skill-runtime.env
```

API key 文件也在本工程内：

```text
<tool-root>\config\codex_api_key.txt
```

`skill-runtime.env` 是唯一正式运行配置文件。真实 API key 不写入 env，而是放入 `codex_api_key.txt`，避免提交代码时泄露。

一键启动 Web UI 和依赖检查：

```powershell
.\start-runtime.ps1
```

双击启动入口：

```text
start-runtime.bat
```

只检查依赖、不启动 UI：

```powershell
.\start-runtime.ps1 -CheckOnly
```

当前推荐配置结构如下：

```env
# 当前工程默认加载 CCGS、美术、音频、Godot bridge 四个 skill/plugin 仓库。
SKILL_RUNTIME_ROOT=${SKILL_RUNTIME_WORKSPACE_ROOT}\game_studio_source_code\Claude-Code-Game-Studios
SKILL_RUNTIME_ADD_DIRS=${SKILL_RUNTIME_WORKSPACE_ROOT}\game_studio_source_code\Claude-Code-Game-Studios;${SKILL_RUNTIME_WORKSPACE_ROOT}\art_pipeline_skill;${SKILL_RUNTIME_WORKSPACE_ROOT}\audio_pipeline_skill;${SKILL_RUNTIME_WORKSPACE_ROOT}\godot_tool_bridge_skill
SKILL_RUNTIME_NAMESPACES=ccgs=${SKILL_RUNTIME_WORKSPACE_ROOT}\game_studio_source_code\Claude-Code-Game-Studios;art=${SKILL_RUNTIME_WORKSPACE_ROOT}\art_pipeline_skill;audio=${SKILL_RUNTIME_WORKSPACE_ROOT}\audio_pipeline_skill;godot=${SKILL_RUNTIME_WORKSPACE_ROOT}\godot_tool_bridge_skill

# 这个目录只供 skill-runtime 启动的 Codex 子进程使用，不污染本机 Codex。
SKILL_RUNTIME_CODEX_HOME=${SKILL_RUNTIME_TOOL_ROOT}\.skill-runtime\codex-home

# 这个目录只供 skill-runtime 保存运行状态，不写入 skill 仓库。
SKILL_RUNTIME_STATE_ROOT=${SKILL_RUNTIME_TOOL_ROOT}\.skill-runtime\state
SKILL_RUNTIME_CODEX_EXECUTABLE=codex

# 当前 Codex API 代理配置。
SKILL_RUNTIME_MODEL=gpt-5.4
CODEX_PROVIDER=OpenAI
CODEX_BASE_URL=https://api.psydo.top
CODEX_WIRE_API=responses
CODEX_REQUIRES_OPENAI_AUTH=true
CODEX_API_KEY_FILE=${SKILL_RUNTIME_TOOL_ROOT}\config\codex_api_key.txt

# skill-runtime 工作流配置。
SKILL_RUNTIME_STRICT_TOOLS=true
SKILL_RUNTIME_STRICT_SCHEMA=false
SKILL_RUNTIME_QA=auto
SKILL_RUNTIME_ASSUME_YES=true
SKILL_RUNTIME_DRY_RUN=false
SKILL_RUNTIME_MAX_STEPS=8

# 本地资产生产管线。
SKILL_RUNTIME_ENV_FORGE_BASE_URL=http://127.0.0.1:7860
SKILL_RUNTIME_ENV_COMFYUI_BASE_URL=http://127.0.0.1:8188
```

打开操作界面：

```powershell
cd <tool-root>
python -B .\skill-runtime.py --runtime-env .\config\skill-runtime.env ui
```

直接运行某个已加载仓库中的 skill：

```powershell
python -B .\skill-runtime.py --runtime-env .\config\skill-runtime.env run /help "告诉我当前可以进行的工作"
```

首次使用该环境文件时，运行器会创建：

```text
<tool-root>\.skill-runtime\codex-home\
├─ config.toml   # 仅含此运行器的 model/provider/base_url 等 Codex 配置
└─ auth.json     # 仅含此运行器需要的 API key
```

如果没有填写 `SKILL_RUNTIME_CODEX_HOME`，但使用了 `--runtime-env`，默认隔离目录为：

```text
<SKILL_RUNTIME_ROOT>\.skill-runtime\codex-home\
```

本工具目录已经忽略 `.skill-runtime/` 和 `config\codex_api_key.txt`。所有运行器创建或使用的文件都在本工程内。

配置优先级为：程序默认值 < `--runtime-env` 文件 < 显式命令行参数。也就是说，可以在环境文件里保存日常配置，并在一次运行中用例如 `--model`、`--codex-base-url` 或 `--codex-home` 临时覆盖。

在交互界面中输入 `status` 可以查看实际采用的隔离路径和 Codex 配置；API key 只会显示为 `[REDACTED]`。

这个入口不是 CCGS 专用工具。

它的定位是：

> 用 Codex CLI 作为大脑，加载任意 Claude Code skill / command / agent / hook / MCP 仓库，并尽量按 Claude Code 的执行机制运行。

CCGS 只是一个可加载的测试仓库，和 `superpowers`、一人公司 skill、编程 skill、插件仓库一样，都是输入。

## 一、从哪里启动

推荐从仓库根目录启动：

```powershell
cd <skill-repo-root>
```

通用入口有两个：

```powershell
python -B .\skill-runtime.py --help
```

或者：

```powershell
python -B .\codex-skill-runtime\skill_runtime.py --help
```

这两个入口效果一致。`skill-runtime.py` 是根目录快捷入口，`codex-skill-runtime\skill_runtime.py` 是新建的通用操作界面入口。

## 二、加载一个 skill 仓库

非交互方式：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> inspect
```

如果要加载当前 CCGS 仓库：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> inspect
```

这个命令会列出：

- 当前加载的仓库根目录。
- 发现的 skills / slash commands。
- 发现的 agents。
- 会被注入上下文的项目文件，例如 `CLAUDE.md`、`README.md`、`.claude/docs/*`。

## 三、打开交互式操作界面

```powershell
python -B .\skill-runtime.py ui
```

进入后可以输入：

```text
load <skill-repo-root>
skills
agents
status
```

此时 CCGS 只是被加载的一个 skill 仓库。

你也可以加载其他仓库：

```text
load <superpowers-skill-repo>
skills
run /some-skill 你的需求
```

## 四、运行 skill

默认推荐使用 strict 工具循环，因为它更接近 Claude Code：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --strict-tools --assume-yes run /skill-name "你的需求"
```

对于 CCGS 的 Godot 原型，只是这样加载并运行：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --strict-tools --assume-yes --qa required run /prototype "用Godot 4制作一个瓦片地图金币收集小游戏，要求玩家移动、墙阻挡、金币、步数HUD、胜利条件、重启、自动测试" --path engine --spike
```

注意这里真正的通用结构是：

```text
skill-runtime --root <某个skill仓库> run /<某个skill> <参数>
```

CCGS 不再是工具本体，只是 `<某个skill仓库>`。

## 五、交互界面中的常用命令

进入：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> ui
```

可用命令：

```text
help
load <path>
status
inspect
skills [filter]
agents [filter]
run /skill <args>
/skill <args>
strict /skill <args>
agent <name> <prompt>
resume <session> <prompt>
answer <session> <answer>
mcp-auth <server> [--callback-url <url>] [--code <code>]
set strict on|off
set qa auto|off|required
set assume-yes on|off
set dry-run on|off
set model <name|clear>
set output-style <style|clear>
set max-steps <n>
exit
```

## 六、运行 agent

非交互：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --assume-yes agent qa-tester "请检查这个项目是否真的可以运行，并给出 VERDICT 和 EVIDENCE MATRIX。"
```

交互：

```text
agent qa-tester 请检查这个项目是否真的可以运行，并给出 VERDICT 和 EVIDENCE MATRIX。
```

## 七、Godot 检查

Godot smoke 现在通过普通插件 skill 运行，不再是 runtime core 命令。先确保 `godot_tool_bridge_skill` 在 `SKILL_RUNTIME_ADD_DIRS` 中，然后调用：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --strict-tools run /godot:godot-tool-bridge "检查 <godot-project>，Godot 路径为 <godot-executable-or-dir>"
```

交互：

```text
set env GODOT_EXE=<godot-executable-or-dir>
strict /godot:godot-tool-bridge 检查 <godot-project>
```

## 八、只看流程，不调用 Codex

如果你想先看 runtime 会怎样组织 prompt、skill、agent、hook、gate，而不消耗 Codex token：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --dry-run --strict-tools --assume-yes run /skill-name "你的需求"
```

交互：

```text
set dry-run on
strict /skill-name 你的需求
```

## 九、配置你的 Codex API 代理

`skill-runtime` 不自己调用 API；它会启动 `codex exec` 子进程。使用推荐的 `--runtime-env` 隔离模式时，它会把 API key 写入独立 `CODEX_HOME\auth.json`，使该子进程可以认证，但不会写入你的本机默认 Codex 配置目录。

现在你可以把 API key、代理 base URL、HTTP 代理和 Codex config 覆盖项直接传给 `skill-runtime`，runtime 会在调用 `codex exec` 时注入这些配置。

### 方式 A：命令行直接传入

```powershell
python -B .\skill-runtime.py `
  --root <skill-repo-root> `
  --codex-api-key "你的API_KEY" `
  --codex-base-url "https://你的代理地址" `
  --codex-provider "proxy" `
  --model "gpt-5.5" `
  --strict-tools --assume-yes `
  run /skill-name "你的需求"
```

`--codex-base-url` 会自动给 Codex CLI 注入类似下面的配置覆盖：

```toml
model_provider = "proxy"

[model_providers.proxy]
name = "proxy"
base_url = "https://你的代理地址"
wire_api = "responses"
requires_openai_auth = true
```

### 方式 B：API key 从文件读取

避免把 key 暴露在命令历史里：

```powershell
python -B .\skill-runtime.py `
  --root <skill-repo-root> `
  --codex-api-key-file <tool-root>\config\codex_api_key.txt `
  --codex-base-url "https://你的代理地址" `
  --codex-provider "proxy" `
  run /skill-name "你的需求"
```

也可以用 `@file`：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> --codex-api-key @<tool-root>\config\codex_api_key.txt --codex-base-url "https://你的代理地址" run /skill-name "你的需求"
```

### 方式 C：仅向子进程注入 env 文件（旧方式）

创建一个工程内文件，例如 `<tool-root>\config\codex.env`：

```env
OPENAI_API_KEY=你的API_KEY
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

运行：

```powershell
python -B .\skill-runtime.py `
  --root <skill-repo-root> `
  --codex-env-file <tool-root>\config\codex.env `
  --codex-base-url "https://你的代理地址" `
  run /skill-name "你的需求"
```

这种方式只注入环境变量，不会自动创建独立 `CODEX_HOME`。需要与本机 Codex 配置隔离时，应使用本文开头的 `--runtime-env` 方式。

### 方式 D：传任意 Codex config

如果你的 Codex CLI 需要更特殊的 provider 配置，可以直接传原始 `--config`：

```powershell
python -B .\skill-runtime.py `
  --root <skill-repo-root> `
  --codex-env OPENAI_API_KEY=你的API_KEY `
  --codex-config 'model_provider="myproxy"' `
  --codex-config 'model_providers.myproxy.name="myproxy"' `
  --codex-config 'model_providers.myproxy.base_url="https://你的代理地址"' `
  --codex-config 'model_providers.myproxy.wire_api="responses"' `
  --codex-config 'model_providers.myproxy.requires_openai_auth=true' `
  run /skill-name "你的需求"
```

### 方式 E：只设置 HTTP/HTTPS 代理

```powershell
python -B .\skill-runtime.py `
  --root <skill-repo-root> `
  --codex-http-proxy http://127.0.0.1:7890 `
  --codex-https-proxy http://127.0.0.1:7890 `
  run /skill-name "你的需求"
```

### 交互界面中设置

进入：

```powershell
python -B .\skill-runtime.py --root <skill-repo-root> ui
```

在界面里输入：

```text
set api-key @<tool-root>\config\codex_api_key.txt
set base-url https://你的代理地址
set provider proxy
set model gpt-5.5
status
```

`status` 会显示配置，但 API key 会显示为 `[REDACTED]`，不会明文输出。

### 重要说明

- `skill-runtime` 只把这些配置传给 `codex exec` 子进程。
- `--runtime-env` 会创建独立 `CODEX_HOME\config.toml`；如果提供 API key，还会创建独立 `CODEX_HOME\auth.json`。
- 不使用 `--runtime-env` 或 `--codex-home` 时，兼容旧行为，不自动创建隔离配置目录。
- API key 注入为 `OPENAI_API_KEY` 环境变量。
- `--codex-base-url` 会通过 Codex CLI 的 `--config` 注入 provider 配置。
- dry-run 的 session 证据会记录 env 名称，但会把 key/token/password/auth 这类字段标为 `[REDACTED]`。

## 十、结果在哪里

每次运行都会生成一个 session：

```text
<SKILL_RUNTIME_STATE_ROOT>\sessions\<session-id>\
```

本工程默认是：

```text
<tool-root>\.skill-runtime\state\sessions\<session-id>\
```

里面保存：

- prompt
- Codex stdout / stderr
- 工具调用结果
- hook 结果
- gate 结果
- QA agent 输出
- Godot 输出
- transcript / resume 信息
- summary.json

判断“是否真的运行过测试”，不要看最终一句话，要看这里的证据文件。被加载的 skill 仓库不应该再出现新的 `.codex-skill-runtime` 运行状态目录。

## 十一、当前命名

工具本体的核心命名已经去 CCGS 化：

- `codex-skill-runtime-core`
- `.skill-runtime`
- `CodexSkillRuntime`

用户入口仍然推荐统一使用：

```powershell
python -B .\skill-runtime.py ...
```

现在保留的 CCGS 字样只表示一个可加载的测试 skill 仓库，不代表工具本体。
