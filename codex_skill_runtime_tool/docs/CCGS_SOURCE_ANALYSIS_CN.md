# Claude Code Game Studios 源码分析与 Godot 从零使用指南

> 文档语言：中文  
> 分析对象：`<skill-repo-root>`  
> 分析日期：2026-05-17  
> 结论先行：这不是一份已经写好的 Godot 游戏源码，而是一套给 Claude Code 使用的“游戏开发工作室流程模板”。它由 Agent、Skill、Hook、规则、文档模板、引擎参考资料组成，用来指导 AI 和用户按专业游戏制作流程从概念、设计、架构、故事任务、实现、测试、打磨到发布逐步推进。

---

## 1. 给完全新手的总览

如果你刚接触这套东西，先把它理解成一个“AI 游戏公司操作手册”。

它不是 Godot 项目，不会直接双击运行，也没有 `project.godot`、游戏场景、玩家脚本、怪物脚本、主菜单等内容。它真正提供的是：

1. 一套 Claude Code 能读取的项目说明。
2. 49 个“专业角色”的提示词，例如制作人、技术总监、玩法程序、Godot 专家、QA 测试等。
3. 73 个“工作流命令”的说明，例如 `/start`、`/brainstorm`、`/setup-engine`、`/design-system`、`/create-architecture`、`/dev-story`。
4. 12 个自动 Hook 脚本，用来在 Claude Code 会话、提交、推送、写文件、压缩上下文等时做提醒或校验。
5. 11 个路径规则，告诉 AI 在不同目录写不同类型文件时要遵守什么规范。
6. 一大批文档模板，帮你写 GDD、ADR、史诗、故事、测试计划、发布清单等。
7. Godot、Unity、Unreal 的版本参考资料，其中 Godot 部分已经固定到 `Godot 4.6` 参考。

把它当作“游戏项目生产线”会更准确。真正的游戏源码需要你后续通过这套流程生成或手写。

---

## 2. 最重要的判断

### 2.1 它到底是不是游戏源码

不是。

源码仓库里的 `src/` 目前只有：

- `src/.gitkeep`：空占位文件。
- `src/CLAUDE.md`：告诉 AI 以后写游戏代码时遵守什么规则。

这说明当前仓库没有实际游戏逻辑。没有玩家控制器，没有地图，没有战斗系统，没有 Godot 场景，也没有导出配置。

### 2.2 它是什么

它是一套 Claude Code 项目模板。Claude Code 在仓库根目录运行时，会读取：

- 根目录 `CLAUDE.md`
- `.claude/settings.json`
- `.claude/agents/*.md`
- `.claude/skills/*/SKILL.md`
- `.claude/hooks/*.sh`
- `.claude/rules/*.md`
- `.claude/docs/*.md`

这些文件共同定义“AI 应该如何像一个游戏开发团队一样工作”。

### 2.3 它会自动开发游戏吗

不会。

它强调“用户驱动协作”，核心流程是：

1. AI 提问。
2. AI 给选项。
3. 用户决策。
4. AI 起草内容。
5. 用户批准。
6. AI 写入文件或执行实现。

所以它不是自动驾驶系统。它更像“有组织的 AI 助手团队”。

### 2.4 这套软件如何运行

它本身没有传统意义上的运行入口，例如 `npm start`、`python main.py`、`godot --path .`。

实际运行方式是：

1. 你进入仓库目录。
2. 启动 Claude Code。
3. Claude Code 读取 `.claude` 配置。
4. 你输入 `/start` 或其他 Slash Command。
5. Claude Code 按对应 `SKILL.md` 的步骤工作。
6. 技能可能调用 Agent、读取模板、写设计文档、写代码、运行测试。
7. Hook 脚本在会话和工具调用时自动执行辅助检查。

---

## 3. 仓库整体目录结构

当前仓库根目录是：

```text
Claude-Code-Game-Studios/
├─ .claude/                       # Claude Code 运行时核心配置
├─ .github/                       # GitHub 贡献、Issue、PR、资助配置
├─ CCGS Skill Testing Framework/  # 测试这套 Skill/Agent 的可选测试框架
├─ design/                        # 游戏设计产物目录，目前只有 registry 占位
├─ docs/                          # 公开说明、工作流、引擎参考、示例
├─ production/                    # 生产管理目录，目前只有 session-state 占位
├─ src/                           # 未来游戏源码目录，目前未包含游戏代码
├─ .gitignore
├─ CLAUDE.md                      # Claude Code 主入口说明
├─ CONTRIBUTING.md
├─ LICENSE
├─ README.md
├─ SECURITY.md
└─ UPGRADING.md
```

全量文件统计：

| 项目 | 数量 |
|---|---:|
| 全部文件，包括 `.git` | 445 |
| `.claude` 文件 | 211 |
| `CCGS Skill Testing Framework` 文件 | 127 |
| `docs` 文件 | 62 |
| Markdown 文件 | 392 |
| Shell 脚本 | 13 |
| YAML 文件 | 5 |
| JSON 文件 | 1 |

实际运行这套系统最重要的是 `.claude/`。`CCGS Skill Testing Framework/` 是可选测试框架，游戏开发者可以不用它。

---

## 4. `.claude/` 目录：真正的运行核心

`.claude/` 的结构如下：

```text
.claude/
├─ settings.json                  # Claude Code 配置：权限、Hook、状态栏
├─ statusline.sh                  # 状态栏脚本
├─ agents/                        # 49 个 Agent 定义
├─ skills/                        # 73 个 Slash Command 技能
├─ hooks/                         # 12 个自动脚本
├─ rules/                         # 11 个路径规则
├─ docs/                          # Claude Code 专用说明和模板
└─ agent-memory/                  # 个别 Agent 的长期记忆，目前有 lead-programmer
```

### 4.1 `settings.json`

这是 Claude Code 的项目配置。它定义三类事情：

1. 状态栏：
   - 使用 `bash .claude/statusline.sh`。
   - 用来显示阶段、上下文、任务等状态。

2. 权限：
   - 允许一些低风险命令，例如 `git status`、`git diff`、`python -m pytest`。
   - 禁止危险命令，例如 `rm -rf`、`git reset --hard`、强制推送、读取 `.env`。

3. Hook：
   - 在 SessionStart、PreToolUse、PostToolUse、PreCompact、PostCompact、Stop、SubagentStart、SubagentStop 等事件执行脚本。

### 4.2 `agents/`

每个 Agent 是一个 Markdown 文件，带 YAML 头部。例如：

```yaml
---
name: godot-specialist
description: "..."
tools: Read, Glob, Grep, Write, Edit, Bash, Task
model: sonnet
---
```

这不是程序类，也不是 Python 模块。它是“角色提示词”。Claude Code 通过这些文件知道某个子代理应该扮演什么专业角色、能用哪些工具、该遵守什么工作边界。

49 个 Agent 分为几类：

| 层级 | Agent |
|---|---|
| 总监层 | `creative-director`、`technical-director`、`producer` |
| 部门负责人 | `game-designer`、`lead-programmer`、`art-director`、`audio-director`、`narrative-director`、`qa-lead`、`release-manager`、`localization-lead` |
| 通用专家 | `gameplay-programmer`、`engine-programmer`、`ai-programmer`、`network-programmer`、`tools-programmer`、`ui-programmer`、`systems-designer`、`level-designer`、`economy-designer`、`technical-artist`、`sound-designer`、`writer`、`world-builder`、`ux-designer`、`prototyper`、`performance-analyst`、`devops-engineer`、`analytics-engineer`、`security-engineer`、`qa-tester`、`accessibility-specialist`、`live-ops-designer`、`community-manager` |
| Godot 专家 | `godot-specialist`、`godot-gdscript-specialist`、`godot-csharp-specialist`、`godot-shader-specialist`、`godot-gdextension-specialist` |
| Unity 专家 | `unity-specialist`、`unity-ui-specialist`、`unity-shader-specialist`、`unity-dots-specialist`、`unity-addressables-specialist` |
| Unreal 专家 | `unreal-specialist`、`ue-blueprint-specialist`、`ue-gas-specialist`、`ue-replication-specialist`、`ue-umg-specialist` |

### 4.2.1 Agent 如何在项目中生效

Agent 不是后台常驻程序，也不是会自动运行的插件。它的“能力”来自对应 Markdown 文件里的角色提示词。

一个 Agent 文件通常由两部分组成：

```yaml
---
name: godot-gdscript-specialist
description: "The GDScript specialist owns all GDScript code quality..."
tools: Read, Glob, Grep, Write, Edit, Bash, Task
model: sonnet
---
```

```text
正文：协作协议、职责边界、编码规则、检查清单、输出格式、和其他 Agent 的协作方式。
```

Claude Code 中的生效过程是：

1. Claude Code 启动后能看到 `.claude/agents/*.md`。
2. Skill 或主会话通过 `Task` 调用某个 Agent 名称。
3. Claude Code 把该 Agent 文件作为子代理的系统提示词。
4. 子代理按自己的 `description`、`tools`、`model` 和正文规则工作。
5. 子代理返回分析、草案、代码变更建议、评审结论或阻塞项。
6. 主会话收集结果，再向用户汇报或继续下一步。

所以 Agent 的角色功能不是由隐藏代码实现，而是由以下内容共同实现：

| 来源 | 作用 |
|---|---|
| `name` | 让 Skill 能按名字调用这个角色 |
| `description` | 让 Claude 知道这个角色何时适用 |
| `model` | 指定任务复杂度对应的模型层级 |
| `tools` | 限定这个角色可以读、写、搜索、运行命令或再调用子任务 |
| 正文职责 | 定义它负责什么、不负责什么 |
| 协作协议 | 要求它先问、给选项、等用户批准，不擅自跨域决策 |
| 检查清单 | 让它的输出稳定，例如代码评审表、可访问性清单、Godot API 版本检查 |
| 协调规则 | 遇到跨领域问题时转交给正确的上级或同级 Agent |

### 4.2.2 49 个 Agent 逐个能力分析

#### 总监层 Agent

| Agent | 角色功能 | 能力如何实现 | 常见输出 |
|---|---|---|---|
| `creative-director` | 负责游戏愿景、支柱、调性、创意冲突裁决 | 通过 Opus 层级、愿景评审提示、`director-gates.md` 中的 CD 类 Gate 检查创意是否一致 | APPROVE/CONCERNS/REJECT、支柱修改建议 |
| `technical-director` | 负责技术方向、架构、引擎选择、技术风险 | 通过架构评审提示、技术风险检查、引擎参考文档和 TD 类 Gate 做高层判断 | 技术可行性结论、架构风险、阻塞项 |
| `producer` | 负责范围、里程碑、Sprint、跨部门协调 | 通过生产管理提示、进度/风险/范围检查表、PR 类 Gate 约束项目节奏 | Sprint 计划、范围裁剪建议、REALISTIC/CONCERNS/UNREALISTIC |

#### 部门负责人 Agent

| Agent | 角色功能 | 能力如何实现 | 常见输出 |
|---|---|---|---|
| `game-designer` | 设计核心机制、规则、进度、经济和平衡 | 读取 GDD、使用 MDA/玩家动机/系统设计规则生成或评审玩法 | 机制方案、规则表、系统设计建议 |
| `lead-programmer` | 负责代码架构、API、代码评审、程序任务分配 | 读取 ADR、Control Manifest、源码和测试，按代码标准做工程判断 | 代码评审、可行性评估、重构建议 |
| `art-director` | 负责视觉方向、Art Bible、资产标准、UI 视觉一致性 | 读取 Art Bible、资产规格和视觉 Gate，检查风格是否统一 | 视觉方向、资产规范、AD Gate verdict |
| `audio-director` | 负责音乐方向、声音调性、混音和音频实现策略 | 读取游戏支柱、场景和系统需求，规划声音层级和音频反馈 | Sound Bible、音频事件策略、声音风格建议 |
| `narrative-director` | 负责故事结构、世界观、角色弧线、叙事系统 | 读取叙事文档和 GDD，检查世界观、语气和角色一致性 | 故事框架、角色方向、叙事一致性评审 |
| `qa-lead` | 负责测试策略、Bug 分级、回归范围、发布质量门 | 读取 Story、验收标准、测试证据和缺陷记录，判断质量风险 | QA 计划、覆盖率缺口、发布风险 |
| `release-manager` | 负责版本、构建、平台提交流程、发布日协调 | 读取 Release Checklist、构建信息、平台要求和变更记录 | 发布清单、版本策略、发布阻塞项 |
| `localization-lead` | 负责国际化、字符串、翻译流程、语言环境测试 | 扫描硬编码字符串、读取 UI/平台要求，建立本地化流程 | 字符串清单、翻译准备报告、RTL/文化审查建议 |

#### 通用专家 Agent

| Agent | 角色功能 | 能力如何实现 | 常见输出 |
|---|---|---|---|
| `systems-designer` | 细化单个玩法系统、公式、状态、交互矩阵 | 读取系统 GDD 和依赖系统，补足规则、变量、范围、边界情况 | 公式表、系统规则、边界情况清单 |
| `level-designer` | 设计关卡布局、节奏、遭遇、空间叙事 | 读取游戏支柱、关卡目标和系统规则，产出空间与节奏设计 | Level Design Document、遭遇表 |
| `economy-designer` | 设计资源、水龙头/水槽、掉落、成长曲线 | 读取经济相关 GDD 和数值表，检查通胀、死循环、最优策略 | 经济模型、掉落表、平衡风险 |
| `gameplay-programmer` | 实现玩法机制、玩家、战斗、交互功能 | 读取 Story、GDD、ADR、Control Manifest，把规则落成源码和测试 | 玩法代码、单元测试、实现总结 |
| `engine-programmer` | 实现核心框架、渲染/物理/资源加载等底层系统 | 按 `src/core/**` 和引擎规则处理性能敏感代码 | 核心系统代码、性能说明 |
| `ai-programmer` | 实现 AI、状态机、寻路、感知、NPC 决策 | 读取 AI GDD 和行为规则，设计状态机/行为树/寻路接口 | AI 脚本、调试策略、行为测试 |
| `network-programmer` | 实现联网、同步、复制、延迟补偿、匹配 | 读取网络 ADR 和安全要求，保证服务端权威和消息版本化 | 网络架构、同步代码、带宽风险 |
| `tools-programmer` | 实现编辑器工具、调试工具、管线自动化 | 读取生产痛点和工具需求，创建辅助脚本或编辑器扩展 | 工具脚本、调试面板、管线自动化 |
| `ui-programmer` | 实现菜单、HUD、UI 框架、数据绑定 | 读取 UX Spec、可访问性要求和 UI 规则，落地界面代码 | UI 场景/组件、绑定逻辑、截图验证建议 |
| `technical-artist` | 处理 Shader、VFX、渲染优化、美术管线 | 读取 Art Bible、资产规范和渲染预算，连接美术与工程 | Shader/VFX 方案、性能预算、资产管线建议 |
| `sound-designer` | 设计具体 SFX、音频事件、混音参数 | 读取音频方向和系统事件，定义声音反馈和触发条件 | 音效规格、音频事件列表 |
| `writer` | 写对白、道具文案、描述、环境文本 | 读取叙事方向、角色设定和语气指南，生成玩家可见文本 | 对白、Lore、物品说明 |
| `world-builder` | 设计世界规则、阵营、历史、地理、生态 | 读取世界观和系统需求，保持设定一致 | 世界观文档、阵营设计、时间线 |
| `ux-designer` | 设计用户流程、交互模式、信息架构、可访问性 | 读取 GDD、平台输入和 UX 模板，设计屏幕/流程 | UX Spec、HUD 设计、交互库 |
| `prototyper` | 快速验证玩法或垂直切片 | 使用放宽标准，在 `prototypes/` 中构建一次性验证物 | 原型、验证报告、PROCEED/PIVOT/KILL |
| `performance-analyst` | 分析帧率、内存、CPU/GPU、加载问题 | 读取性能预算、运行测试命令和日志，定位瓶颈 | 性能报告、优化优先级 |
| `devops-engineer` | 维护 CI/CD、构建脚本、分支策略、测试流水线 | 读取构建配置和测试命令，生成或检查自动化流程 | CI 配置、构建脚本、流水线建议 |
| `analytics-engineer` | 设计遥测、事件、A/B 测试、数据分析 | 读取产品目标和玩家行为需求，定义数据事件结构 | 事件字典、分析方案、仪表盘需求 |
| `security-engineer` | 检查作弊、存档篡改、网络漏洞、数据泄露 | 扫描输入、存档、网络和隐私相关代码或设计 | 安全审计、修复建议、风险等级 |
| `qa-tester` | 写测试用例、Bug 报告、回归清单 | 读取验收标准和测试模板，把需求转成可执行检查项 | 测试用例、Bug Report、回归清单 |
| `accessibility-specialist` | 检查可访问性、按键重映射、文字缩放、色盲模式 | 读取 UX、平台输入和可访问性模板，检查屏幕与交互 | 可访问性审计、修复建议 |
| `live-ops-designer` | 设计赛季、活动、留存、后续内容节奏 | 读取经济、社区和分析需求，规划上线后的内容循环 | 活动方案、赛季计划、留存机制 |
| `community-manager` | 面向玩家沟通、补丁说明、反馈整理、危机沟通 | 读取变更记录和玩家反馈，把内部语言转成玩家语言 | Patch Notes、社区公告、反馈摘要 |

#### 引擎专家 Agent

| Agent | 角色功能 | 能力如何实现 | 常见输出 |
|---|---|---|---|
| `godot-specialist` | Godot 总体架构、场景/节点/信号/资源模式 | 先读 `docs/engine-reference/godot/`，再按 Godot 最佳实践和子专家路由判断 | Godot 架构建议、文件路由、API 风险 |
| `godot-gdscript-specialist` | `.gd` 代码质量、静态类型、信号、协程、性能 | 读取 GDScript 规则、弃用 API、版本变更，用 `.gd` 文件过滤检查 | GDScript 代码、重构建议、反模式清单 |
| `godot-csharp-specialist` | Godot C#、`.csproj`、`partial class`、Signal delegate | 读取 Godot C# 版本参考，检查 `[Export]`、`[Signal]`、异步和集合边界 | C# 脚本、项目配置、类型安全建议 |
| `godot-shader-specialist` | `.gdshader`、材质、粒子、后处理、渲染性能 | 读取 Godot 渲染模块和 Shader 规则，按渲染器预算实现效果 | Shader、材质方案、渲染预算 |
| `godot-gdextension-specialist` | C++/Rust GDExtension、原生性能扩展 | 只在 profiling 证明需要时建议原生扩展，设计脚本/原生边界 | GDExtension 方案、构建说明 |
| `unity-specialist` | Unity 总体架构、MonoBehaviour/DOTS/Addressables 选择 | 读取 Unity 参考资料并路由到 Unity 子专家 | Unity 架构建议、包/系统选择 |
| `unity-ui-specialist` | UI Toolkit/UGUI、输入、跨平台 UI | 读取 UX 和 Unity UI 规则，选择 UI 技术栈 | UI Toolkit/UGUI 方案 |
| `unity-shader-specialist` | Shader Graph、HLSL、VFX Graph、URP/HDRP | 根据渲染管线和性能预算设计视觉效果 | Shader/VFX 方案 |
| `unity-dots-specialist` | ECS、Jobs、Burst、数据导向架构 | 判断系统是否适合 DOTS，并设计组件/系统边界 | ECS 设计、性能策略 |
| `unity-addressables-specialist` | Addressables、异步加载、远程内容、内存 | 规划资源组、加载卸载、catalog 和内存预算 | Addressables 配置建议 |
| `unreal-specialist` | Unreal 总体架构、C++/Blueprint、UE 子系统 | 读取 Unreal 参考资料并路由到 UE 子专家 | UE 架构建议、蓝图/C++ 边界 |
| `ue-blueprint-specialist` | Blueprint 架构、图规范、BP/C++ 边界 | 检查 Blueprint 是否可维护、是否该下沉到 C++ | Blueprint 规范、重构建议 |
| `ue-gas-specialist` | Gameplay Ability System 能力、效果、属性、Tag | 使用 GAS 模式约束技能系统和预测逻辑 | GAS 架构、Ability/Effect 设计 |
| `ue-replication-specialist` | UE 网络复制、RPC、预测、带宽 | 检查服务端权威、相关性、同步策略 | Replication 方案、带宽风险 |
| `ue-umg-specialist` | UMG/CommonUI、Widget、输入路由、UI 性能 | 设计 Widget 层级、绑定方式和 CommonUI 输入 | UMG/CommonUI 实现建议 |

### 4.3 `skills/`

`skills/` 下有 73 个目录，每个目录里有一个 `SKILL.md`。这些就是 Claude Code 里的 Slash Command 工作流。

例如：

```text
.claude/skills/start/SKILL.md
.claude/skills/setup-engine/SKILL.md
.claude/skills/brainstorm/SKILL.md
.claude/skills/design-system/SKILL.md
.claude/skills/dev-story/SKILL.md
```

Skill 的本质是“分步骤操作说明”。它告诉 Claude：

- 这个命令叫什么。
- 什么时候使用。
- 该优先用什么模型层级。
- 可以用哪些工具。
- 先读什么文件。
- 该问用户什么问题。
- 什么时候写文件。
- 是否调用 Agent。
- 输出什么格式。
- 下一个命令是什么。

每个 `SKILL.md` 的 YAML 头部通常包括：

```yaml
---
name: start
description: "First-time onboarding ..."
argument-hint: "[no arguments]"
allowed-tools: Read, Glob, Grep, Write, AskUserQuestion
model: sonnet
---
```

其中 `model` 是 Claude Code 的模型层级提示。源码里的 73 个 Skill 都带有 `model` 字段。大体规则是：

| 模型层级 | 用途 |
|---|---|
| `haiku` | 状态查询、摘要、轻量只读任务，例如 `/help`、`/sprint-status` |
| `sonnet` | 默认工作模型，用于大多数设计、实现、分析、测试任务 |
| `opus` | 高风险、多文档综合、阶段关卡，例如 `/gate-check`、`/review-all-gdds`、`/architecture-review` |

### 4.4 73 个 Skill 的分类

| 分类 | 命令 |
|---|---|
| 入门与导航 | `/start`、`/help`、`/project-stage-detect`、`/setup-engine`、`/adopt` |
| 游戏设计 | `/brainstorm`、`/map-systems`、`/design-system`、`/quick-design`、`/review-all-gdds`、`/propagate-design-change` |
| 美术与资产 | `/art-bible`、`/asset-spec`、`/asset-audit` |
| UX 与界面 | `/ux-design`、`/ux-review` |
| 架构 | `/create-architecture`、`/architecture-decision`、`/architecture-review`、`/create-control-manifest` |
| 史诗、故事、Sprint | `/create-epics`、`/create-stories`、`/dev-story`、`/sprint-plan`、`/sprint-status`、`/story-readiness`、`/story-done`、`/estimate` |
| 评审与分析 | `/design-review`、`/code-review`、`/balance-check`、`/content-audit`、`/scope-check`、`/perf-profile`、`/tech-debt`、`/gate-check`、`/consistency-check`、`/security-audit` |
| QA 与测试 | `/qa-plan`、`/smoke-check`、`/soak-test`、`/regression-suite`、`/test-setup`、`/test-helpers`、`/test-evidence-review`、`/test-flakiness`、`/skill-test`、`/skill-improve` |
| 生产管理 | `/milestone-review`、`/retrospective`、`/bug-report`、`/bug-triage`、`/reverse-document`、`/playtest-report` |
| 发布 | `/release-checklist`、`/launch-checklist`、`/changelog`、`/patch-notes`、`/hotfix`、`/day-one-patch` |
| 创意与内容 | `/prototype`、`/vertical-slice`、`/onboard`、`/localize` |
| 团队编排 | `/team-combat`、`/team-narrative`、`/team-ui`、`/team-release`、`/team-polish`、`/team-audio`、`/team-level`、`/team-live-ops`、`/team-qa` |

### 4.4.1 Skill 如何在项目中生效

Skill 是 Claude Code 的 Slash Command 定义。它也不是可执行程序，而是一份“执行流程脚本说明”。它生效的方式是：

1. Claude Code 发现 `.claude/skills/<skill-name>/SKILL.md`。
2. 用户输入 `/skill-name`。
3. Claude Code 读取该 Skill 的 YAML 头部，知道命令名、描述、参数提示、允许工具和模型层级。
4. Claude Code 按正文中的阶段执行：读取文件、询问用户、调用 Agent、写文档、写代码、运行测试。
5. 如果 Skill 写文件，通常会先要求用户批准。
6. 如果 Skill 需要评审，会按 `production/review-mode.txt` 或 `--review` 参数决定是否调用总监 Gate。

一个 Skill 的“能力”通常由 5 个部分实现：

| 能力来源 | 具体作用 |
|---|---|
| YAML frontmatter | 让 Claude Code 能把 `/命令` 映射到正确文件 |
| 阶段化步骤 | 规定必须先读什么、再问什么、最后写什么 |
| 工具列表 | 控制它能否读文件、搜索、写文件、运行 Bash、调用 Agent、联网 |
| 模板和参考文档 | 保证输出格式统一，例如 GDD、ADR、Story、QA 报告 |
| Gate 和 Agent 调用 | 让专业角色参与评审、实现或测试 |

### 4.4.2 73 个 Skill 逐个能力分析

#### 入门与导航

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/start` | 新用户入口，判断你有没有想法、有没有引擎、有没有现有项目 | 读取项目状态，问用户当前阶段，写 `production/review-mode.txt` 和初始阶段信息 | 推荐下一步工作流 |
| `/help` | 根据当前项目状态告诉你下一步做什么 | 读取 `workflow-catalog.yaml`、现有产物和用户描述，只读分析 | 下一步命令建议 |
| `/project-stage-detect` | 自动判断项目处于哪个阶段，有哪些缺口 | 扫描 `design/`、`docs/`、`src/`、`production/`，按七阶段管线匹配 | 阶段检测报告 |
| `/setup-engine` | 配置 Godot/Unity/Unreal、版本、语言、技术偏好 | 读取/修改 `CLAUDE.md` 与 `technical-preferences.md`，必要时 WebSearch/WebFetch 官方文档 | 引擎配置、专家路由、版本参考 |
| `/adopt` | 接入已有项目或旧模板项目 | 审计现有 GDD/ADR/Story/基础设施是否符合 CCGS 格式 | 迁移计划和缺口列表 |

#### 游戏设计

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/brainstorm` | 从零构思游戏概念 | 用提问、MDA、玩家心理、平台/范围分析，并调用总监评审 | `design/gdd/game-concept.md` |
| `/map-systems` | 把游戏概念拆成可设计的系统 | 读取概念文档，枚举系统、依赖、优先级、实体清单 | `design/gdd/systems-index.md` |
| `/design-system` | 为某个系统逐节写 GDD | 读取概念、系统索引、依赖 GDD，逐节提问并调用设计/QA/美术等 Agent | `design/gdd/[system].md` |
| `/quick-design` | 为小改动写轻量设计说明 | 读取相关 GDD，只写小范围变更，不走完整 GDD | Quick Design Spec |
| `/review-all-gdds` | 一次性审查所有 GDD 的一致性和设计理论问题 | Opus 多文档综合，读取所有 GDD，查冲突、支柱漂移、公式矛盾 | 跨 GDD 评审报告 |
| `/propagate-design-change` | 设计变更后找受影响的 ADR 和 Story | 扫描变更 GDD、TR registry、ADR、Epic/Story 引用 | 变更影响报告 |

#### 美术与资产

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/art-bible` | 建立视觉身份和美术规范 | 读取概念和视觉锚点，逐节写 Art Bible，并调用 art-director Gate | `design/art/art-bible.md` |
| `/asset-spec` | 为角色、场景、UI、道具写资产规格和生成提示 | 读取 Art Bible、GDD、关卡/角色文档，更新资产清单 | `design/assets/specs/`、`asset-manifest.md` |
| `/asset-audit` | 审核资产命名、大小、格式、引用和管线规范 | 只读扫描 `assets/` 与资产清单，按规则列问题 | 资产审计报告 |

#### UX 与界面

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/ux-design` | 设计 HUD、菜单、屏幕流程或交互模式 | 读取 GDD、平台输入、可访问性要求和 UX 模板，逐节写规格 | `design/ux/*.md` |
| `/ux-review` | 审查 UX 文档是否完整、可访问、和 GDD 对齐 | 只读检查 UX Spec/HUD/Pattern Library 的章节和质量 | APPROVED/NEEDS REVISION |

#### 架构

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/create-architecture` | 把所有 GDD 转成整体技术蓝图 | 读取 GDD、系统索引、ADR、引擎参考，提取 TR 并设计模块/数据流/API 边界 | `docs/architecture/architecture.md` |
| `/architecture-decision` | 写一条架构决策记录 ADR | 询问背景、备选方案、决策和后果，调用技术/程序评审 | `docs/architecture/adr-*.md` |
| `/architecture-review` | 审查架构和 ADR 覆盖是否完整一致 | Opus 读取架构、ADR、TR registry、GDD，建立追踪矩阵 | PASS/CONCERNS/FAIL 报告 |
| `/create-control-manifest` | 从 ADR 提取程序员可执行规则 | 读取 Accepted ADR、技术偏好、引擎参考，压平成禁用/必须规则 | `control-manifest.md` |

#### Epic、Story 与 Sprint

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/create-epics` | 从 GDD 和架构生成 Epic | 读取 in-scope GDD、ADR、TR registry，按架构层拆大功能包 | `production/epics/*/EPIC.md` |
| `/create-stories` | 把 Epic 拆成可实现 Story | 读取 Epic、GDD、ADR、Control Manifest，嵌入 TR-ID、验收标准、测试证据路径 | `production/epics/**/*.md` |
| `/dev-story` | 实现一条 Story | 读取 Story、GDD、ADR、Control Manifest，路由到对应程序/引擎 Agent 写代码和测试 | `src/` 代码、`tests/` 测试、实现总结 |
| `/sprint-plan` | 创建或更新 Sprint 计划 | 读取 Epic/Story、容量、里程碑和生产状态，调用 producer 评审 | `production/sprints/sprint-*.md` |
| `/sprint-status` | 快速查看当前 Sprint 进度 | 只读扫描 Sprint 文件和 Story 状态 | 30 行左右状态摘要 |
| `/story-readiness` | 检查 Story 是否能开工 | 读取 Story、GDD、ADR、验收标准和阻塞项 | READY/NEEDS WORK/BLOCKED |
| `/story-done` | 关闭 Story 前的完成审查 | 读取实现文件、Story、GDD、ADR、测试证据，必要时调用 QA 和 Lead Programmer | 更新 Story 为 Complete 或列阻塞 |
| `/estimate` | 估算任务复杂度和风险 | 读取上下文、依赖和历史信息，按复杂度/风险/信心拆解 | 结构化估算 |

#### 评审与分析

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/design-review` | 审查单个设计文档 | 读取 GDD/设计文档，按必需章节、可实现性、一致性给 verdict | 设计评审报告 |
| `/code-review` | 审查代码质量和架构一致性 | 读取代码、ADR、规则和测试，必要时调用 Lead Programmer | 代码问题清单 |
| `/balance-check` | 检查数值、公式、经济和平衡风险 | 读取数据表、公式、GDD，找异常曲线和破坏性策略 | 平衡报告 |
| `/content-audit` | 对比 GDD 计划内容和实际实现内容 | 扫描设计要求和实现/资产数量 | 内容缺口清单 |
| `/scope-check` | 检查 Sprint 或功能是否范围膨胀 | 只读对比原计划、当前 Story 和新增工作 | Scope creep 报告 |
| `/perf-profile` | 做性能分析和优化建议 | 读取预算、运行或解析性能数据，找 CPU/GPU/内存瓶颈 | 性能报告 |
| `/tech-debt` | 扫描和登记技术债 | 搜索 TODO、反模式、重复代码、已知绕路，维护债务登记 | 技术债清单 |
| `/gate-check` | 判断是否能进入下一阶段 | Opus 读取阶段所需产物，按 full/lean/solo 调用总监 Gate | PASS/CONCERNS/FAIL |
| `/consistency-check` | 查 GDD/实体/数值跨文档矛盾 | 读取实体 registry，再定向 grep 相关 GDD 段落 | 一致性问题列表 |
| `/security-audit` | 审查作弊、存档、网络、输入、隐私风险 | 扫描相关设计和代码，按严重级别输出修复建议 | 安全审计报告 |

#### QA 与测试

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/qa-plan` | 为 Sprint、功能或 Story 写测试计划 | 读取 GDD/Story，按 Logic/Integration/Visual/UI 分类测试 | QA Test Plan |
| `/smoke-check` | 做交给 QA 前的冒烟检查 | 运行测试命令、检查关键路径和核心功能 | PASS/FAIL 冒烟报告 |
| `/soak-test` | 设计长时间稳定性测试方案 | 根据时长和关注点定义观察指标、日志和失败标准 | Soak Test Protocol |
| `/regression-suite` | 维护回归测试覆盖 | 映射 GDD 关键路径、已修 bug 和现有测试 | `tests/regression-suite.md` |
| `/test-setup` | 搭建测试框架和 CI | 根据引擎生成 `tests/` 结构、测试运行器和 GitHub Actions | 测试脚手架 |
| `/test-helpers` | 生成测试辅助库 | 读取现有测试风格，为系统生成断言、工厂、mock | `tests/helpers/` |
| `/test-evidence-review` | 审查测试证据是否充分 | 读取测试文件和手工证据，检查断言、边界和签收 | ADEQUATE/INCOMPLETE/MISSING |
| `/test-flakiness` | 找不稳定测试 | 读取 CI 日志或测试历史，统计间歇失败 | flaky test registry |
| `/skill-test` | 测试 CCGS Skill 自身 | 读取 `CCGS Skill Testing Framework` 的 spec、rubric 和 catalog | static/spec/category/audit 结果 |
| `/skill-improve` | 测试-修复-复测某个 Skill | 运行 `/skill-test`、诊断失败、改 Skill、再测试 | 改进后的 Skill |

#### 生产管理

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/milestone-review` | 评审里程碑进度和风险 | 读取 Sprint、Story、质量和范围信息，调用 producer Gate | 里程碑报告 |
| `/retrospective` | 做 Sprint 或里程碑复盘 | 读取完成项、阻塞、速度和质量趋势，生成行动项 | Retrospective 文档 |
| `/bug-report` | 创建结构化 Bug 报告 | 从描述或代码分析提取复现、严重度、环境、期望/实际 | Bug Report |
| `/bug-triage` | 重新排序和分配 Bug | 读取 open bugs，按严重度/优先级/趋势分组 | Bug triage 报告 |
| `/reverse-document` | 从已有代码反向补设计或架构文档 | 读取 `src/` 或原型，生成缺失的 GDD/架构说明 | 反向文档 |
| `/playtest-report` | 创建或分析试玩报告 | 收集试玩观察、玩家反馈、问题和行动项 | Playtest Report |

#### 发布

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/release-checklist` | 生成发布前检查表 | 读取平台、构建、QA、内容、法律/商店要求 | Release Checklist |
| `/launch-checklist` | 做最终上线准备验证 | 横跨代码、内容、商店、社区、基础设施做 go/no-go | Launch Readiness |
| `/changelog` | 生成内部变更日志 | 读取 git、Sprint 和设计文档，把变更结构化 | Changelog |
| `/patch-notes` | 生成玩家可读补丁说明 | 把内部提交和 Sprint 语言转成玩家语言 | Patch Notes |
| `/hotfix` | 紧急修复流程 | 创建热修复范围、审计轨迹、测试与回滚计划 | Hotfix plan/patch |
| `/day-one-patch` | 首日补丁流程 | 把金盘后或上线初期问题做成迷你 Sprint | Day-one patch 计划 |

#### 创意与内容

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/prototype` | 概念阶段做一次性原型 | 根据游戏类型选择 HTML/Engine/Paper 路径，快速验证核心问题 | 原型和 PROCEED/PIVOT/KILL |
| `/vertical-slice` | 预制作阶段做完整体验切片 | 读取 GDD、架构、UX，构建接近生产质量的小闭环 | Vertical Slice Report |
| `/onboard` | 给新成员/角色生成入门说明 | 读取项目状态、架构、规范和当前优先级 | Onboarding 文档 |
| `/localize` | 本地化流水线 | 扫描硬编码字符串、抽取/验证翻译、文化审查、RTL 检查 | Localization 报告 |

#### 团队编排

| Skill | 它做什么 | 能力如何实现 | 典型产物/效果 |
|---|---|---|---|
| `/team-combat` | 编排战斗团队 | 并行/顺序调用设计、玩法程序、AI、美术技术、音效、QA Agent | 战斗功能端到端方案 |
| `/team-narrative` | 编排叙事团队 | 调用 narrative-director、writer、world-builder、level-designer | 叙事内容包 |
| `/team-ui` | 编排 UI 团队 | 调用 ux-designer、ui-programmer、art-director、accessibility-specialist | UI 规格和实现计划 |
| `/team-release` | 编排发布团队 | 调用 release-manager、qa-lead、devops-engineer、producer | 发布执行计划 |
| `/team-polish` | 编排打磨团队 | 调用 performance-analyst、technical-artist、sound-designer、qa-tester | 打磨问题和修复计划 |
| `/team-audio` | 编排音频团队 | 调用 audio-director、sound-designer、technical-artist、gameplay-programmer | 音频方向到实现计划 |
| `/team-level` | 编排关卡团队 | 调用 level-designer、narrative-director、world-builder、art-director、systems-designer、qa-tester | 关卡完整方案 |
| `/team-live-ops` | 编排运营活动团队 | 调用 live-ops、economy、analytics、community、writer/narrative | 赛季/活动计划 |
| `/team-qa` | 编排 QA 团队 | 调用 qa-lead、qa-tester、必要时程序/producer | QA 周期包 |

### 4.5 `hooks/`

Hook 是自动触发的小脚本。对小白来说，可以把 Hook 理解成“当某件事发生时自动插进来检查一下的门卫”。

在这个项目里，Hook 的生效链路是：

1. `.claude/settings.json` 的 `hooks` 字段声明触发点。
2. Claude Code 在对应事件发生时执行指定 shell 脚本。
3. 脚本从标准输入或当前目录读取上下文。
4. 脚本检查是否和本次事件相关。
5. 如果不相关，快速 `exit 0`，不影响开发。
6. 如果相关，就输出提醒、记录日志，或在严重情况下阻止危险操作。

例如 `validate-commit.sh` 虽然挂在 `PreToolUse` 的 Bash 事件上，但它会先检查当前 Bash 命令是不是 `git commit`。如果不是，它直接退出，不会每次 Bash 都完整扫描项目。

Hook 在项目中主要解决 4 类问题：

| 类别 | 解决什么问题 |
|---|---|
| 会话定位 | 让 Claude 每次进入项目时知道当前分支、最近状态、缺失文档 |
| 安全防护 | 阻止强制推送、危险删除、提交明显错误内容 |
| 质量提醒 | 修改资产或 Skill 后提醒运行对应检查 |
| 上下文恢复 | 长会话压缩或中断后，用 `active.md` 恢复状态 |

| Hook 文件 | 触发时机 | 作用 |
|---|---|---|
| `session-start.sh` | 会话开始 | 显示项目上下文、分支、最近提交、状态文件预览 |
| `detect-gaps.sh` | 会话开始 | 检查是否是新项目、是否缺设计文档、是否已有代码但没架构 |
| `validate-commit.sh` | Bash 工具使用前 | 如果命令是 `git commit`，检查硬编码、TODO、JSON、设计文档 |
| `validate-push.sh` | Bash 工具使用前 | 如果命令是 `git push`，提醒或阻止推到保护分支 |
| `validate-assets.sh` | Write/Edit 后 | 如果改了 `assets/`，检查命名和 JSON |
| `validate-skill-change.sh` | Write/Edit 后 | 如果改了 `.claude/skills/`，提醒运行 `/skill-test` |
| `pre-compact.sh` | 上下文压缩前 | 输出会话状态，减少压缩后丢失信息 |
| `post-compact.sh` | 上下文压缩后 | 提醒读取 `production/session-state/active.md` |
| `notify.sh` | 通知事件 | Windows 下发系统通知 |
| `session-stop.sh` | 会话停止 | 归档会话日志和 Git 活动 |
| `log-agent.sh` | 子代理启动 | 记录 Agent 调用 |
| `log-agent-stop.sh` | 子代理结束 | 记录 Agent 完成结果 |

这些 Hook 只在 Claude Code 识别 `.claude/settings.json` 时自动生效。如果你用普通终端、Godot Editor、VS Code 或 Codex 直接打开项目，它们不会自动生效，除非你把它们改造成 Git Hook、CI 步骤或手动脚本。

### 4.6 `rules/`

规则文件是“按路径生效的编码规范”。例如写 `src/gameplay/**` 时，要遵守玩法代码规则；写 `src/ui/**` 时，要遵守 UI 规则。

Rules 的生效逻辑是：

1. Rule 文件放在 `.claude/rules/*.md`。
2. 每个 Rule 文件描述一类路径或文件的约束。
3. Claude Code 在编辑相关路径时，会把对应规则作为上下文。
4. Agent 和 Skill 也会在正文中要求遵守这些路径规则。
5. Hook 和评审 Skill 可以再用扫描方式检查是否违反规则。

Rules 不是编译器，也不是 Godot 的项目设置。它不会像静态类型检查那样自动阻止所有错误。它的作用是给 AI 和评审流程提供“该目录下应该怎么写”的明文规范。

在项目中，Rules 主要约束 5 类东西：

| 类别 | 例子 | 约束目的 |
|---|---|---|
| 玩法代码 | `src/gameplay/**` | 数值要数据驱动，不能让 UI 持有游戏状态 |
| 核心代码 | `src/core/**` | API 稳定、热路径少分配、注意线程/性能 |
| UI 代码 | `src/ui/**` | 可本地化、可访问、不要写业务状态 |
| 设计文档 | `design/gdd/**` | GDD 必须有必需章节、公式和边界情况 |
| 测试 | `tests/**` | 测试命名、覆盖要求、证据格式一致 |

当前 11 个规则：

```text
ai-code.md
data-files.md
design-docs.md
engine-code.md
gameplay-code.md
narrative.md
network-code.md
prototype-code.md
shader-code.md
test-standards.md
ui-code.md
```

11 个 Rule 的含义如下：

| Rule | 主要约束 |
|---|---|
| `ai-code.md` | AI 行为、调试可见性、性能预算、参数数据驱动 |
| `data-files.md` | JSON/YAML/数据表结构、命名、校验和可追踪性 |
| `design-docs.md` | GDD、设计文档章节完整性和公式/边界情况要求 |
| `engine-code.md` | 核心引擎层代码稳定性、性能和 API 边界 |
| `gameplay-code.md` | 玩法代码的数据驱动、delta time、系统边界 |
| `narrative.md` | 叙事文案、世界观、角色语气和一致性 |
| `network-code.md` | 服务端权威、消息版本化、安全和带宽意识 |
| `prototype-code.md` | 原型代码允许粗糙，但必须隔离和记录假设 |
| `shader-code.md` | Shader 命名、性能预算、材质/渲染约束 |
| `test-standards.md` | 测试命名、证据、覆盖和可复现性 |
| `ui-code.md` | UI 不持有核心状态、可访问、可本地化、输入方式完整 |

### 4.7 `.claude/docs/`

这里是 Claude Code 运行时参考文档。Docs 可以理解为“AI 团队的公司制度、模板库和项目手册”。

Docs 的生效方式分三种：

1. 根目录 `CLAUDE.md` 通过 `@.claude/docs/...` 引用它们，让 Claude Code 启动时知道要读哪些基础制度。
2. Skill 正文显式要求读取某些文档，例如 `/create-architecture` 要读 `technical-preferences.md`、`workflow-catalog.yaml` 和引擎参考。
3. Agent 正文要求在特定场景读取它们，例如 Godot Agent 必须先读 `docs/engine-reference/godot/VERSION.md`。

Docs 不会自己执行。它们的作用是提供稳定知识源，避免每个 Skill 都重复写一遍同样规则。

`.claude/docs/` 可以分成 5 类：

| 类型 | 文件 | 在项目中怎么生效 |
|---|---|---|
| 入门索引 | `quick-start.md`、`skills-reference.md`、`agent-roster.md` | 帮用户和 AI 找到该用哪个 Skill/Agent |
| 协作制度 | `coordination-rules.md`、`director-gates.md`、`context-management.md` | 约束子代理、评审强度、上下文恢复 |
| 工程标准 | `coding-standards.md`、`technical-preferences.md`、`rules-reference.md` | 指导代码、测试、技术栈和路径规则 |
| 管线定义 | `workflow-catalog.yaml`、`directory-structure.md` | 定义七阶段流程和目录应该长什么样 |
| 模板库 | `templates/` | 给 GDD、ADR、UX、Sprint、测试证据、发布清单提供统一格式 |

这里包括：

- `quick-start.md`：快速入门。
- `agent-roster.md`：Agent 花名册。
- `skills-reference.md`：Skill 索引。
- `coordination-rules.md`：Agent 协作规则。
- `context-management.md`：上下文管理策略。
- `director-gates.md`：总监评审关卡。
- `workflow-catalog.yaml`：七阶段开发管线。
- `technical-preferences.md`：项目技术偏好，初始状态是待配置。
- `directory-structure.md`：推荐项目结构。
- `templates/`：37 个文档模板。

---

## 5. 根目录 `CLAUDE.md` 的作用

根目录 `CLAUDE.md` 是 Claude Code 进入项目后的主说明文件。

它声明：

- 项目是 Claude Code Game Studios。
- 游戏引擎待选择：Godot、Unity 或 Unreal。
- 技术栈待 `/setup-engine` 填入。
- 项目结构参考 `.claude/docs/directory-structure.md`。
- Godot 引擎参考默认指向 `docs/engine-reference/godot/VERSION.md`。
- 协作协议是“Question -> Options -> Decision -> Draft -> Approval”。
- Agent 写文件前必须询问用户。
- 代码标准和上下文管理从 `.claude/docs/` 加载。

这里的 `@.claude/docs/...` 是 Claude Code 风格的引用，意思是让 Claude 读取对应文件作为项目上下文。

---

## 6. 公开 `docs/` 目录

根目录 `docs/` 不是 Claude Code 运行配置，而是公开说明和参考资料。

主要内容：

```text
docs/
├─ WORKFLOW-GUIDE.md
├─ COLLABORATIVE-DESIGN-PRINCIPLE.md
├─ architecture/
│  └─ tr-registry.yaml
├─ registry/
│  └─ architecture.yaml
├─ examples/
│  └─ 多个示例会话文档
└─ engine-reference/
   ├─ README.md
   ├─ godot/
   ├─ unity/
   └─ unreal/
```

### 6.1 `WORKFLOW-GUIDE.md`

这是完整工作流手册，按照七阶段解释如何从零做游戏：

1. Concept：概念阶段。
2. Systems Design：系统设计阶段。
3. Technical Setup：技术搭建阶段。
4. Pre-Production：预制作阶段。
5. Production：正式制作阶段。
6. Polish：打磨阶段。
7. Release：发布阶段。

### 6.2 `docs/engine-reference/godot/`

Godot 参考资料包括：

```text
docs/engine-reference/godot/
├─ VERSION.md
├─ current-best-practices.md
├─ breaking-changes.md
├─ deprecated-apis.md
└─ modules/
   ├─ animation.md
   ├─ audio.md
   ├─ input.md
   ├─ navigation.md
   ├─ networking.md
   ├─ physics.md
   ├─ rendering.md
   └─ ui.md
```

源码内置的 Godot 版本参考是：

| 字段 | 值 |
|---|---|
| Engine Version | Godot 4.6 |
| Release Date | January 2026 |
| Project Pinned | 2026-02-12 |
| Last Docs Verified | 2026-02-12 |
| LLM Knowledge Cutoff | May 2025 |

这部分很重要，因为源码作者认为 Godot 4.4、4.5、4.6 超出了很多模型训练数据，必须查本地参考资料或官方文档，不能靠记忆猜 API。

我在 2026-05-17 额外核对了 Godot 官方下载与归档页面：官方 Windows 下载页显示的稳定下载版本是 `4.6.2`；官方归档页会列出更多版本号，例如 `4.6.3`、`4.7`，但归档页“列出版本”不等于“当前推荐稳定下载”。所以新手应以官方下载页的稳定版为准。源码文档固定到 4.6，但你真正开新项目时，可以在 `/setup-engine godot [version]` 中填你实际安装的稳定版本。

官方页面：

- https://godotengine.org/download/windows/
- https://godotengine.org/download/archive/

---

## 7. `CCGS Skill Testing Framework/` 是什么

这个目录不是游戏开发运行时。它是测试 CCGS 自身 Skill 和 Agent 的框架。

结构：

```text
CCGS Skill Testing Framework/
├─ README.md
├─ CLAUDE.md
├─ catalog.yaml
├─ quality-rubric.md
├─ skills/
├─ agents/
└─ templates/
```

它的作用是：

- 记录 73 个 Skill 和 49 个 Agent 的测试覆盖。
- 给 `/skill-test` 和 `/skill-improve` 提供测试规格。
- 检查 Skill 是否有必须字段。
- 检查某类 Skill 是否符合质量标准。
- 帮维护者改进这套框架。

普通游戏开发者可以不管它。它的 README 明确说这个目录自包含且可选，`.claude/` 不依赖它。

---

## 8. 这套系统的运行机制

### 8.1 第一层：Claude Code 启动

你在仓库根目录运行：

```bash
claude
```

Claude Code 会读取项目里的说明和配置：

- `CLAUDE.md`
- `.claude/settings.json`
- `.claude/agents/`
- `.claude/skills/`
- `.claude/hooks/`
- `.claude/rules/`

然后 `.claude/settings.json` 里的 SessionStart Hook 会触发：

```bash
bash .claude/hooks/session-start.sh
bash .claude/hooks/detect-gaps.sh
```

这些 Hook 会告诉 Claude 当前项目状态，例如有没有引擎配置、有没有概念文档、有没有代码但缺设计文档。

### 8.2 第二层：用户运行 Slash Command

例如你输入：

```text
/start
```

Claude Code 会找到：

```text
.claude/skills/start/SKILL.md
```

然后按里面的阶段执行：

1. 检测项目状态。
2. 问用户当前处于什么状态。
3. 根据用户回答推荐下一步。
4. 设置评审强度模式。
5. 写入初始阶段文件。
6. 交接到下一个命令。

### 8.3 第三层：Skill 调用 Agent

一些 Skill 会调用 Agent。例如 `/brainstorm` 在确定游戏支柱后，会调用：

- `creative-director`
- `art-director`
- `technical-director`
- `producer`

这些 Agent 会给专业评审意见。

注意：Skill 调用 Agent 不是“让多个真实员工同时工作”，而是在 Claude Code 里启动多个子代理上下文，让它们按不同角色分析问题。

### 8.4 第四层：写文件前询问

这套系统反复强调写文件前必须问用户。例如：

```text
May I write this to design/gdd/game-concept.md?
```

用户确认后才写入。

这能防止 AI 擅自覆盖你的设计和代码。

### 8.5 第五层：Hook 自动检查

当 Claude 运行 Bash 或写文件时，Hook 可能自动执行。

例子：

- 如果尝试 `git commit`，`validate-commit.sh` 会检查提交质量。
- 如果修改 `.claude/skills/`，`validate-skill-change.sh` 会提醒跑 `/skill-test`。
- 如果上下文快被压缩，`pre-compact.sh` 会输出会话状态，`post-compact.sh` 会提醒恢复状态。

### 8.6 第六层：文件作为长期记忆

这套系统不依赖聊天记录当记忆，而是要求把状态写进：

```text
production/session-state/active.md
```

原因是长对话会压缩或丢失，而文件会保留。

---

## 9. 七阶段游戏开发管线

`workflow-catalog.yaml` 定义了完整生产管线。

### 9.1 阶段一：Concept 概念阶段

目标：从“我想做游戏”变成“有清晰概念、有引擎、有系统拆分”。

关键命令：

```text
/start
/brainstorm
/setup-engine
/design-review
/art-bible
/map-systems
```

产物：

```text
design/gdd/game-concept.md
.claude/docs/technical-preferences.md
design/art/art-bible.md
design/gdd/systems-index.md
```

### 9.2 阶段二：Systems Design 系统设计阶段

目标：给每个核心系统写 GDD。

GDD 是 Game Design Document，中文可以理解为“游戏设计文档”。它写清楚某个系统应该带来什么体验、规则是什么、公式是什么、边界情况是什么、验收标准是什么。

关键命令：

```text
/design-system combat
/design-review design/gdd/combat.md
/review-all-gdds
/consistency-check
```

产物：

```text
design/gdd/[system-name].md
design/gdd/gdd-cross-review-*.md
design/registry/entities.yaml
```

### 9.3 阶段三：Technical Setup 技术搭建阶段

目标：把设计翻译成技术架构。

关键概念：

- ADR：Architecture Decision Record，架构决策记录。
- TR：Technical Requirement，技术需求。
- Control Manifest：从 ADR 提炼出来的程序员规则表。

关键命令：

```text
/create-architecture
/architecture-decision
/architecture-review
/create-control-manifest
```

产物：

```text
docs/architecture/architecture.md
docs/architecture/adr-*.md
docs/architecture/architecture-review-*.md
docs/architecture/control-manifest.md
design/accessibility-requirements.md
```

### 9.4 阶段四：Pre-Production 预制作阶段

目标：做关键屏幕、资产规格、原型、史诗、故事和第一轮 Sprint。

关键命令：

```text
/asset-spec
/ux-design
/ux-review
/prototype
/create-epics
/create-stories
/test-setup
/sprint-plan
/vertical-slice
```

产物：

```text
design/assets/entity-inventory.md
design/assets/asset-manifest.md
design/ux/*.md
prototypes/*/README.md
production/epics/*/EPIC.md
production/epics/**/*.md
production/sprints/sprint-*.md
tests/
```

### 9.5 阶段五：Production 正式制作阶段

目标：按 Sprint 实现故事。

关键命令：

```text
/story-readiness
/dev-story production/epics/[epic]/[story].md
/code-review [files]
/story-done production/epics/[epic]/[story].md
/qa-plan
/bug-report
/bug-triage
/sprint-status
/retrospective
```

产物：

```text
src/
tests/
production/qa/
production/bugs/
production/sprints/
```

### 9.6 阶段六：Polish 打磨阶段

目标：性能、平衡、资产、体验、可访问性、Playtest。

关键命令：

```text
/perf-profile
/balance-check
/asset-audit
/playtest-report
/tech-debt
/team-polish
/localize
```

产物：

```text
production/playtests/*.md
production/reports/
production/tech-debt/
```

### 9.7 阶段七：Release 发布阶段

目标：发布前完整检查。

关键命令：

```text
/release-checklist
/launch-checklist
/patch-notes
/changelog
/hotfix
/day-one-patch
```

产物：

```text
production/release/
CHANGELOG.md
release notes
```

---

## 10. 如果你要从 0 用 Godot 做游戏，应该怎么做

### 10.1 你需要安装什么

最低需要：

1. Git。
2. Claude Code。
3. Godot。

推荐安装：

1. Git Bash：Windows 上 Hook 需要 `bash`。
2. `jq`：Hook 解析 JSON 更可靠。
3. Python 3：JSON 校验和测试辅助更可靠。
4. 如果选择 Godot C#：安装 .NET SDK。

Godot 安装建议：

- 新手、2D、独立开发：优先使用 Godot 官方稳定版。
- 如果使用这套源码内置参考：它写的是 Godot 4.6。
- 如果你下载时官方稳定版更高：运行 `/setup-engine godot [你的版本]`，然后使用 `/setup-engine refresh` 刷新参考资料。

### 10.2 推荐的项目布局

最简单的 Godot 布局是让 Godot 项目根目录就是仓库根目录：

```text
Claude-Code-Game-Studios/
├─ project.godot                 # Godot 项目文件
├─ .godot/                       # Godot 缓存，已在 .gitignore 中忽略
├─ .claude/                      # Claude Code 工作室配置
├─ src/                          # 放脚本和场景
│  ├─ scenes/
│  ├─ scripts/
│  ├─ gameplay/
│  ├─ core/
│  └─ ui/
├─ assets/                       # 美术、音频、数据
├─ design/                       # GDD、Art Bible、UX、资产规格
├─ docs/                         # 架构、ADR、引擎参考
├─ tests/                        # 自动化测试
└─ production/                   # Sprint、Epic、Story、QA、发布记录
```

为什么推荐根目录放 `project.godot`：

- Godot 打开项目最简单。
- `.claude/` 和 `design/` 只是普通文件夹，不影响 Godot 运行。
- 这套模板本来就希望 `src/`、`assets/`、`design/`、`docs/` 在同一仓库。
- `.gitignore` 已经忽略 `.godot/` 和常见构建产物。

### 10.3 新手建议选择 GDScript

`/setup-engine` 会让你在 Godot 语言中选择：

| 选项 | 适合谁 |
|---|---|
| GDScript | 新手、独立开发、快速迭代、2D 游戏、轻中量 3D |
| C# | Unity 背景、需要强 IDE、复杂逻辑、偏工程化项目 |
| GDScript + C# | 高级用法，需要清楚语言边界 |

如果你完全是小白，建议选 GDScript。

原因：

- Godot 原生。
- 教程和示例直接对应。
- 脚本短，调试快。
- 不需要额外 .NET 配置。

### 10.4 从零开始的实际命令顺序

#### 第 1 步：进入项目目录

```powershell
cd <skill-repo-root>
```

#### 第 2 步：启动 Claude Code

```bash
claude
```

#### 第 3 步：运行入口命令

```text
/start
```

如果你没有游戏想法，选择“没有想法”。它会引导你去：

```text
/brainstorm open
```

如果你已经知道要用 Godot，可以后续运行：

```text
/setup-engine godot 4.6.2
```

版本号请用你实际安装的官方稳定版本。源码内置文档是 4.6，官方稳定版以后可能继续变化。

#### 第 4 步：配置 Godot 和语言

`/setup-engine` 会做这些事：

1. 确认引擎。
2. 查找或确认稳定版本。
3. 如果是 Godot，询问语言：GDScript、C# 或 Both。
4. 更新 `CLAUDE.md` 的 Technology Stack。
5. 更新 `.claude/docs/technical-preferences.md`。
6. 填写命名规范。
7. 填写输入平台。
8. 配置 Godot 专家路由。

Godot + GDScript 的路由会类似：

| 文件类型 | 负责 Agent |
|---|---|
| `.gd` 游戏脚本 | `godot-gdscript-specialist` |
| `.gdshader` Shader | `godot-shader-specialist` |
| `.tscn`、`.tres` 场景资源 | `godot-specialist` |
| UI 场景 | `godot-specialist` |
| GDExtension | `godot-gdextension-specialist` |

#### 第 5 步：生成游戏概念

```text
/brainstorm open
```

它会问你喜欢什么游戏、想要什么体验、平台是什么、范围多大。最终会写：

```text
design/gdd/game-concept.md
```

这个文件会包含：

- 一句话卖点。
- 核心幻想。
- 核心循环。
- 游戏支柱。
- 反支柱。
- 玩家类型。
- MVP 范围。
- 风险。
- 引擎建议。

#### 第 6 步：写 Art Bible

```text
/art-bible
```

输出：

```text
design/art/art-bible.md
```

Art Bible 是视觉风格说明。它会约束之后生成角色、场景、UI、特效时的风格一致性。

#### 第 7 步：拆系统

```text
/map-systems
```

输出：

```text
design/gdd/systems-index.md
```

这个文件会列出你的游戏由哪些系统组成，例如：

- 玩家移动。
- 战斗。
- 敌人 AI。
- 关卡。
- UI。
- 存档。
- 音频。

并且会标记依赖顺序。比如“战斗系统”可能依赖“角色属性系统”。

#### 第 8 步：逐个系统写 GDD

例子：

```text
/design-system player-movement
/design-system combat
/design-system enemy-ai
```

每个系统会生成：

```text
design/gdd/player-movement.md
design/gdd/combat.md
design/gdd/enemy-ai.md
```

GDD 至少要包含：

1. 概览。
2. 玩家幻想。
3. 详细规则。
4. 公式。
5. 边界情况。
6. 依赖。
7. 调参项。
8. 验收标准。

#### 第 9 步：评审设计

```text
/design-review design/gdd/combat.md
/review-all-gdds
/consistency-check
```

目的：

- 检查 GDD 是否完整。
- 检查系统之间有没有矛盾。
- 检查数值、名称、规则是否统一。

#### 第 10 步：做技术架构

```text
/create-architecture
```

输出：

```text
docs/architecture/architecture.md
```

这个文档会把所有 GDD 翻译成技术模块。例如：

- `src/core/` 放基础系统。
- `src/gameplay/` 放玩法系统。
- `src/ui/` 放界面。
- `assets/data/` 放数据配置。

#### 第 11 步：写 ADR

```text
/architecture-decision
```

ADR 是“为什么这么设计技术方案”的记录。例子：

```text
docs/architecture/adr-0001-scene-architecture.md
docs/architecture/adr-0002-data-driven-combat.md
docs/architecture/adr-0003-save-system.md
```

对新手来说，ADR 的价值是以后不容易忘记“为什么当时选了这个方案”。

#### 第 12 步：生成控制清单

```text
/create-control-manifest
```

输出：

```text
docs/architecture/control-manifest.md
```

它把 ADR 里的技术决策压平成程序员规则。例如：

- 所有战斗数值必须从资源文件读取。
- 不允许在 UI 节点里直接修改游戏状态。
- Godot 信号命名必须用过去式。

#### 第 13 步：做 UX 设计

```text
/ux-design hud
/ux-design main-menu
/ux-design pause-menu
/ux-review all
```

最低需要：

- 主菜单。
- 核心玩法 HUD。
- 暂停菜单。

#### 第 14 步：创建 Epic 和 Story

```text
/create-epics layer: foundation
/create-stories player-movement
```

Epic 是“大功能包”。Story 是“可以实现的小任务”。

例子：

```text
production/epics/player-movement/EPIC.md
production/epics/player-movement/story-basic-input.md
production/epics/player-movement/story-jump-physics.md
```

#### 第 15 步：搭测试框架

```text
/test-setup
```

Godot 常见测试选择包括 GUT 或 GDUnit4。具体用哪个应由 `/test-setup` 根据项目语言和偏好确认。

#### 第 16 步：计划第一个 Sprint

```text
/sprint-plan new
```

输出：

```text
production/sprints/sprint-001.md
```

Sprint 是“一小段开发周期”。你可以把它理解为“这一周或这两周要做哪些 Story”。

#### 第 17 步：实现一个 Story

```text
/story-readiness production/epics/player-movement/story-basic-input.md
/dev-story production/epics/player-movement/story-basic-input.md
```

`/dev-story` 会：

1. 读取 Story。
2. 读取相关 GDD。
3. 读取相关 ADR。
4. 读取 Control Manifest。
5. 找到应该负责的程序 Agent。
6. 实现代码和测试。
7. 输出实现总结。

#### 第 18 步：检查代码并关闭 Story

```text
/code-review src/gameplay/player_controller.gd
/story-done production/epics/player-movement/story-basic-input.md
```

`/story-done` 会检查：

- 验收标准是否达成。
- 测试是否存在。
- 实现是否违反 GDD。
- 实现是否违反 ADR。
- 是否越界修改了不该改的文件。

#### 第 19 步：打开 Godot 运行游戏

Claude Code 负责生成文件。Godot 负责运行游戏。

你需要在 Godot Editor 中打开该仓库根目录，然后运行主场景。

如果还没有 `project.godot`，你需要先用 Godot 创建项目。建议：

1. 打开 Godot。
2. 点击 New Project。
3. Project Path 选择这个仓库根目录。
4. 创建后，Godot 会生成 `project.godot` 和 `.godot/`。
5. `.godot/` 已经在 `.gitignore` 中，不应提交。

---

## 11. 一条适合小白的 Godot 路线

如果你完全不懂游戏开发，我建议按下面路线降低难度：

| 决策点 | 推荐 |
|---|---|
| 引擎 | Godot 官方稳定版 |
| 语言 | GDScript |
| 游戏类型 | 2D 俯视、2D 平台跳跃、简单解谜、简单塔防 |
| 首个目标 | 一个可以走动、交互、失败、重开的 5 分钟游戏 |
| 不建议一开始做 | 大型开放世界、联网多人、复杂 3D 动作、商业级背包经济、完整剧情 RPG |

第一款游戏建议范围：

```text
一个 2D 俯视小游戏：
- 玩家可以移动。
- 地图上有 3 个可收集物。
- 有 1 种敌人。
- 碰到敌人扣血。
- 收集完物品出现出口。
- 到出口胜利。
- 死亡可以重开。
```

这个范围足够让你练完整流程：

- 概念。
- GDD。
- Godot 输入。
- 场景。
- 碰撞。
- UI。
- 音效。
- 测试。
- 打包。

---

## 12. 这套系统在 Godot 项目中会产生哪些文件

当你真正开始做游戏后，会逐步出现这些文件：

```text
design/
├─ gdd/
│  ├─ game-concept.md
│  ├─ systems-index.md
│  ├─ player-movement.md
│  └─ combat.md
├─ art/
│  └─ art-bible.md
├─ ux/
│  ├─ hud.md
│  ├─ main-menu.md
│  └─ pause-menu.md
└─ assets/
   ├─ entity-inventory.md
   └─ asset-manifest.md

docs/
└─ architecture/
   ├─ architecture.md
   ├─ adr-0001-*.md
   ├─ control-manifest.md
   └─ tr-registry.yaml

production/
├─ epics/
│  └─ player-movement/
│     ├─ EPIC.md
│     └─ story-basic-input.md
├─ sprints/
│  └─ sprint-001.md
├─ session-state/
│  └─ active.md
└─ qa/

src/
├─ core/
├─ gameplay/
├─ ui/
├─ scenes/
└─ scripts/

tests/
└─ ...
```

---

## 13. Godot 专家 Agent 的职责

这套源码对 Godot 支持很完整。相关 Agent 不是泛泛而谈，而是按文件类型和技术领域拆开的。

### 13.1 `godot-specialist`

负责 Godot 总体架构，例如：

- 节点和场景架构。
- Autoload 是否使用。
- 信号和组。
- 资源模式。
- 场景拆分。
- Godot 版本兼容。

### 13.2 `godot-gdscript-specialist`

负责 `.gd` 脚本，例如：

- GDScript 静态类型。
- `class_name`。
- 信号命名。
- `_ready`、`_process`、`_physics_process` 使用规范。
- 不在每帧重复查找节点。
- 避免过深继承。

### 13.3 `godot-csharp-specialist`

负责 `.cs` 文件，例如：

- Godot C# 必须使用 `partial class`。
- `[Export]`、`[Signal]`、`EventHandler` 命名。
- `.csproj` 配置。
- async/await 和 `ToSignal()`。
- C# 和 GDScript 边界。
- 内部集合优先用 .NET `List<T>`、`Dictionary<K,V>`，跨 Godot 边界才用 `Godot.Collections`。

### 13.4 `godot-shader-specialist`

负责 `.gdshader`、材质和渲染，例如：

- `spatial`、`canvas_item`、`particles` Shader。
- Forward+、Mobile、Compatibility 渲染器差异。
- 后处理。
- 粒子。
- Draw Call。
- Shader 性能。

### 13.5 `godot-gdextension-specialist`

负责原生扩展，例如：

- C++ 或 Rust GDExtension。
- 自定义节点。
- GDScript 和原生代码边界。
- 只有性能测试证明需要时才建议使用。

对小白来说，前期通常只需要 `godot-specialist` 和 `godot-gdscript-specialist`。

---

## 14. 它是否可以被 Codex 同样运行

### 14.1 结论

不能“原样同样运行”。

原因不是 Codex 不能帮你做游戏，而是这套源码的运行格式是 Claude Code 专用的。

Claude Code 专用点包括：

| Claude Code 功能 | 在本源码中的体现 | Codex 原样是否识别 |
|---|---|---|
| `.claude/settings.json` | 权限、Hook、状态栏 | 不按 Claude Code 方式自动识别 |
| `.claude/skills/*/SKILL.md` | Slash Command，如 `/start` | 不会自动变成 Codex Slash Command |
| `.claude/agents/*.md` | 命名 Agent，如 `godot-specialist` | 不会自动成为 Codex 可直接选择的 Agent |
| `Task` 工具 | Skill 内要求启动 Claude 子代理 | Codex 有子代理能力，但调用规则、角色定义不同 |
| `AskUserQuestion` 工具 | 多选交互组件 | 当前 Codex 默认模式不能按 Claude 方式使用 |
| `.claude/hooks/*.sh` | Claude Code Hook 事件 | Codex 不会按 `.claude/settings.json` 自动触发 |
| `@file` 引用风格 | `CLAUDE.md` 中引用 docs | Codex 不一定按 Claude 语义自动展开 |

### 14.2 Codex 能不能使用这套思路

可以。

Codex 可以做到：

- 读取这些 Markdown 和 YAML。
- 按文档流程帮助你做 Godot 游戏。
- 根据 GDD 写代码。
- 根据 ADR 检查实现。
- 运行 shell 命令。
- 修改文件。
- 在你明确要求子代理协作时使用 Codex 子代理。

但需要改造，不能直接依赖 `.claude/` 自动生效。

### 14.3 Codex 直接手动使用的最低成本方案

最低成本方案是不转换文件，只把这套仓库当“参考手册”。

你可以对 Codex 说：

```text
请按照 .claude/skills/start/SKILL.md 的流程执行，但用 Codex 的交互方式，不使用 Claude Code 专属 AskUserQuestion。
```

或者：

```text
请读取 .claude/skills/setup-engine/SKILL.md，帮我配置 Godot + GDScript 项目。
```

这种方式能用，但缺点是：

- 没有 `/start` Slash Command。
- 没有自动 Hook。
- 没有命名 Agent 自动路由。
- 每次都要告诉 Codex 读哪个 Skill。

### 14.4 Codex 正式改造方案

建议分四层改造。

#### 第一层：做一个 Codex 总 Skill

Codex 的 Skill 目录通常由 `CODEX_HOME` 或本机 Codex 配置决定。不要在文档或脚本里写死本机用户目录，建议用：

```text
%CODEX_HOME%\skills\
```

Codex Skill 的基本结构是：

```text
ccgs-game-studio/
└─ SKILL.md
```

`SKILL.md` 需要 YAML frontmatter：

```yaml
---
name: ccgs-game-studio
description: 使用 Claude Code Game Studios 的游戏开发流程在 Codex 中指导 Godot、Unity、Unreal 项目，从概念、GDD、ADR、Epic、Story 到实现、测试和发布。
---
```

然后在正文里写：

- 如何读取原 `.claude/docs/workflow-catalog.yaml`。
- 如何选择原 `.claude/skills/<name>/SKILL.md`。
- 如何把 Claude 专属工具替换成 Codex 行为。
- 如何执行 Godot 项目。

优点：迁移成本最低。

#### 第二层：把 73 个 Claude Skill 转换为 Codex Skill

可以把：

```text
.claude/skills/start/SKILL.md
```

转换为：

```text
%CODEX_HOME%\skills\ccgs-start\SKILL.md
```

转换规则：

| Claude Skill 字段 | Codex 中建议 |
|---|---|
| `name` | 保留，但加前缀，例如 `ccgs-start` |
| `description` | 改成 Codex 触发描述，写清“什么时候使用” |
| `argument-hint` | Codex 不一定使用，移到正文 |
| `allowed-tools` | Codex 不用这个字段强控，移到“建议工具”说明 |
| `model` | Codex 不按 Claude 模型名执行，移到“复杂度建议” |
| `AskUserQuestion` | 改成普通问题，或在 Plan mode 下使用 Codex 可用的用户输入工具 |
| `Task` | 改成“如用户明确要求子代理，则 spawn_agent；否则主会话模拟该角色分析” |
| `Write/Edit` 审批 | 保留“写文件前说明将修改什么”的流程 |

优点：最接近原来的命令体系。

缺点：73 个 Skill 工作量大。

#### 第三层：转换 Agent 为 Codex 参考角色

Claude 的 Agent 文件不能直接变成 Codex 的命名 Agent。

推荐做法：

```text
ccgs-game-studio/
├─ SKILL.md
└─ references/
   └─ agents/
      ├─ godot-specialist.md
      ├─ godot-gdscript-specialist.md
      ├─ producer.md
      └─ ...
```

Codex 执行某个任务时读取对应角色文件，把它作为“角色参考”。

如果用户明确要求并行子代理，可以把角色提示词复制进 `spawn_agent` 的 prompt。否则不要强行并行。

#### 第四层：替换 Hook

Claude Hook 不会在 Codex 中自动触发。可以改造成三种形式：

1. Git Hook：
   - 把 `validate-commit.sh` 接到 `.git/hooks/pre-commit`。
   - 把 `validate-push.sh` 接到 `.git/hooks/pre-push`。

2. 手动命令：
   - 写一个 `tools/ccgs-check.ps1` 或 `tools/ccgs-check.sh`。
   - 每次提交前手动运行。

3. CI：
   - 用 GitHub Actions 跑 JSON、测试、资产检查。

建议最小改造：

```text
tools/
├─ ccgs-session-start.sh
├─ ccgs-validate-commit.sh
├─ ccgs-validate-assets.sh
└─ ccgs-check-all.sh
```

然后在 Codex 文档里要求：

```text
提交前运行 tools/ccgs-check-all.sh
```

---

## 15. 如果用 Codex 改造，推荐的实施步骤

### 15.1 第一步：保留原仓库，不要先删 `.claude`

`.claude` 虽然 Codex 不直接运行，但里面是最完整的知识库。先保留。

### 15.2 第二步：创建一个 Codex 总 Skill

路径：

```text
%CODEX_HOME%\skills\ccgs-game-studio\SKILL.md
```

内容重点：

- 总是先读仓库根目录 `CLAUDE.md`。
- 再读 `.claude/docs/workflow-catalog.yaml`。
- 根据任务选择对应 `.claude/skills/<name>/SKILL.md`。
- 把 Claude 专属工具翻译成 Codex 行为。
- Godot 任务必须读 `docs/engine-reference/godot/VERSION.md` 和相关模块文档。

### 15.3 第三步：迁移最常用 12 个 Skill

不要一口气迁移 73 个。先迁移真正会用到的：

```text
ccgs-start
ccgs-setup-engine
ccgs-brainstorm
ccgs-map-systems
ccgs-design-system
ccgs-create-architecture
ccgs-architecture-decision
ccgs-create-epics
ccgs-create-stories
ccgs-dev-story
ccgs-story-done
ccgs-gate-check
```

这 12 个够支撑从 0 到第一轮实现。

### 15.4 第四步：改写交互点

把所有：

```text
AskUserQuestion(...)
```

改成：

```text
用一段简短中文解释背景，然后直接问用户一个必要问题。
```

或者在能使用结构化输入的模式中，用 Codex 的 `request_user_input`。

### 15.5 第五步：改写子代理点

把所有：

```text
Spawn `creative-director` via Task
```

改成：

```text
如果用户明确要求使用子代理，则 spawn_agent；
否则主会话读取 creative-director 角色定义，按该角色视角给出评审意见。
```

这是因为当前 Codex 的子代理调用规则要求：只有用户明确要求子代理、代理协作、并行代理工作时，才能使用 `spawn_agent`。

### 15.6 第六步：改写权限和 Hook

Claude 的 `.claude/settings.json` 不再作为权限来源。

Codex 中需要依赖：

- Codex 自身配置。
- 当前会话的 sandbox 和 approval 策略。
- Git Hook。
- CI。
- 人工审查。

### 15.7 第七步：实际运行 Godot 项目

Codex 负责改文件和运行命令；Godot 仍然负责编辑器运行和导出。

你可以让 Codex：

```text
请按 CCGS 流程为 Godot + GDScript 创建 player-movement 的第一条 story，并实现 src/gameplay/player_controller.gd。
```

但运行游戏仍是：

```bash
godot --path .
```

或在 Godot Editor 中点击运行。

---

## 16. Claude Code 与 Codex 的关键差异

| 能力 | Claude Code 中的表现 | Codex 中的替代 |
|---|---|---|
| Slash Command | `/start` 自动绑定 `.claude/skills/start/SKILL.md` | 用 Codex Skill 触发，或手动要求读取文件 |
| Agent | `.claude/agents/*.md` 自动作为子代理角色 | 放入 Codex Skill references，必要时人工传给子代理 |
| Hook | `.claude/settings.json` 自动触发 | Git Hook、CI、手动脚本 |
| 权限 | `.claude/settings.json` allow/deny | Codex sandbox、approval、项目规则 |
| AskUserQuestion | Claude UI 多选问题 | 普通问题，或 Codex 可用输入工具 |
| 状态栏 | `statusline.sh` | 无直接等价，可用状态文件 |
| 上下文恢复 | `pre-compact.sh`、`post-compact.sh` | 依赖 `production/session-state/active.md` 和 Codex 总结 |

---

## 17. 概念词典

### Agent

Agent 是一个 AI 角色。比如 `godot-gdscript-specialist` 就是“Godot GDScript 专家”。它不是代码对象，而是提示词文件。

### Skill

Skill 是一个工作流命令。比如 `/design-system` 教 AI 如何一步一步写 GDD。

### Hook

Hook 是自动触发脚本。比如提交前检查、写文件后检查。

### GDD

Game Design Document，游戏设计文档。它回答“这个系统玩家怎么体验、规则是什么、数值怎么算、怎样算做完”。

### ADR

Architecture Decision Record，架构决策记录。它回答“为什么技术上这样做，而不是那样做”。

### Epic

Epic 是一个大功能包。比如“玩家移动系统”可以是一个 Epic。

### Story

Story 是可以实现的小任务。比如“实现 WASD 移动输入”就是一个 Story。

### Sprint

Sprint 是一个短周期计划。比如一周内完成 5 个 Story。

### Gate

Gate 是阶段关卡或评审点。它检查当前产物是否足够进入下一阶段。

### Vertical Slice

Vertical Slice 是“垂直切片”，意思是做一个小而完整的游戏体验，从开始、核心循环、反馈、失败或胜利都能跑通，用来验证整个方向可行。

### Prototype

Prototype 是原型。它可以很粗糙，目的是尽快验证一个玩法是否有趣。

### Control Manifest

Control Manifest 是程序员规则清单，从 ADR 提炼出来，约束后续实现。

### Technical Requirement

Technical Requirement，技术需求。通常从 GDD 中提取，用 `TR-系统名-编号` 追踪。

---

## 18. 源码与文档对应清单

| 文档中的说法 | 对应源码位置 |
|---|---|
| 49 个 Agent | `.claude/agents/*.md` |
| 73 个 Skill | `.claude/skills/*/SKILL.md` |
| 12 个 Hook | `.claude/hooks/*.sh` |
| 11 个规则 | `.claude/rules/*.md` |
| 七阶段流程 | `.claude/docs/workflow-catalog.yaml` 和 `docs/WORKFLOW-GUIDE.md` |
| 主入口说明 | `CLAUDE.md` |
| 快速入门 | `.claude/docs/quick-start.md` |
| Agent 名单 | `.claude/docs/agent-roster.md` |
| Skill 名单 | `.claude/docs/skills-reference.md` |
| 协作规则 | `.claude/docs/coordination-rules.md` |
| 上下文策略 | `.claude/docs/context-management.md` |
| 总监评审 | `.claude/docs/director-gates.md` |
| Godot 版本参考 | `docs/engine-reference/godot/VERSION.md` |
| Godot 当前实践 | `docs/engine-reference/godot/current-best-practices.md` |
| Godot 破坏性变化 | `docs/engine-reference/godot/breaking-changes.md` |
| Godot 弃用 API | `docs/engine-reference/godot/deprecated-apis.md` |
| 可选测试框架 | `CCGS Skill Testing Framework/` |
| 未来源码目录 | `src/CLAUDE.md` 和 `src/.gitkeep` |
| Git 忽略规则 | `.gitignore` |

---

## 19. 重要风险和注意事项

### 19.1 文档里有编码乱码

多个 Markdown 文件里出现了类似 `鈥?` 的字符。这通常是编码显示问题，本意多半是破折号或箭头。它不影响 Markdown 大体阅读，但会影响美观和某些脚本匹配。

建议后续做一次编码修复。

### 19.2 `docs/engine-reference/godot` 需要定期刷新

源码固定参考是 2026-02-12。Godot 后续版本会变化，所以开新项目时应运行：

```text
/setup-engine refresh
```

或者手动核对官方文档。

### 19.3 它不能替代你学习 Godot 基础

这套系统能帮你组织开发，但你仍然需要知道：

- 什么是节点。
- 什么是场景。
- 什么是脚本。
- 什么是信号。
- 什么是碰撞。
- 什么是资源。
- 如何在 Godot Editor 里运行主场景。

### 19.4 不要一开始开 full 模式

`full` 模式会触发很多总监评审，适合团队或严肃项目。新手学习建议：

```text
lean
```

原因：

- 节奏更快。
- 只在关键阶段做评审。
- 不会被大量流程拖慢。

### 19.5 不要第一款游戏做太大

这套系统很完整，容易让新手误以为自己一开始就该做商业大作。不要这样做。第一款游戏先做 5 分钟可玩的小游戏。

---

## 20. 对这套源码的最终评价

这套源码的价值不在于“已经有游戏代码”，而在于“把 AI 开发游戏的混乱过程变成有阶段、有角色、有文档、有评审、有测试的流程”。

适合：

- 想用 Claude Code 辅助做游戏的人。
- 想避免 AI 乱写代码、乱跳步骤的人。
- 想建立 GDD、ADR、Epic、Story 流程的人。
- 想从 Godot、Unity、Unreal 中选一个并保持版本意识的人。

不适合：

- 想下载后直接运行一个游戏的人。
- 想完全不学习引擎、只靠 AI 自动生成商业游戏的人。
- 想跳过设计文档直接堆代码的人。

如果你使用 Claude Code，它可以原样工作。  
如果你使用 Codex，它可以作为知识库和流程蓝本工作，但需要转换 Skill、Agent、Hook 和交互方式。

---

## 21. 20 轮以上 Review 记录

本节记录按用户要求进行的 Review。每一轮都按同一顺序检查：

1. 回顾源码和本文档，检查是否一一对应，是否遗漏。
2. 如果没有遗漏，检查概念是否拆到足够细，是否有可量化说明。
3. 如果也达成，进行小白阅读障碍扫描，检查专业名词是否解释、是否省略常识。
4. 如发现问题，修复文档。

### Review 01

- 源码对应：已覆盖 `.claude`、`docs`、`src`、`production`、`CCGS Skill Testing Framework`。
- 发现问题：最初容易把它误解为 Godot 游戏源码。
- 修复：在第 2 节明确写明“不是游戏源码”，并列出 `src/` 当前只有占位和说明。

### Review 02

- 源码对应：已覆盖 49 Agent、73 Skill、12 Hook、11 Rule。
- 发现问题：Agent 和 Skill 对小白过于抽象。
- 修复：第 4 节解释 Agent 是角色提示词，Skill 是工作流说明，不是程序模块。

### Review 03

- 源码对应：已检查 `settings.json`。
- 发现问题：运行机制中权限和 Hook 关系需要拆开。
- 修复：第 4.1、8.5 节分别解释权限和 Hook。

### Review 04

- 源码对应：已检查 `workflow-catalog.yaml` 和 `WORKFLOW-GUIDE.md`。
- 发现问题：七阶段流程需要明确每阶段输入和产物。
- 修复：第 9 节逐阶段列出关键命令和产物路径。

### Review 05

- 源码对应：已检查 Godot 版本参考。
- 发现问题：源码 Godot 版本和用户实际安装版本可能不同。
- 修复：第 6.2 和第 10.1 节区分“源码内置参考版本”和“安装时官方稳定版”。

### Review 06

- 源码对应：已检查 `/setup-engine`。
- 发现问题：Godot 语言选择对小白不够明确。
- 修复：第 10.3 节加入 GDScript、C#、Both 适用人群，并给出新手建议。

### Review 07

- 源码对应：已检查 `.gitignore`。
- 发现问题：没有说明 Godot 项目根目录怎么和 `.claude` 共存。
- 修复：第 10.2 节给出推荐布局，说明 `project.godot` 放仓库根目录。

### Review 08

- 源码对应：已检查 `src/CLAUDE.md`。
- 发现问题：未来源码目录规范没有解释。
- 修复：第 12 节列出后续会生成的 `src/`、`tests/`、`design/`、`docs/architecture/` 结构。

### Review 09

- 源码对应：已检查 Godot Agent 文件。
- 发现问题：Godot 5 个专家职责需要拆细。
- 修复：第 13 节分别解释 `godot-specialist`、`godot-gdscript-specialist`、`godot-csharp-specialist`、`godot-shader-specialist`、`godot-gdextension-specialist`。

### Review 10

- 源码对应：已检查 `CCGS Skill Testing Framework/README.md`。
- 发现问题：测试框架容易被误认为游戏测试目录。
- 修复：第 7 节明确它测试的是 CCGS 自身，不是测试你的游戏。

### Review 11

- 源码对应：已检查 `coordination-rules.md`。
- 发现问题：子代理协作、三层组织结构需要新手解释。
- 修复：第 4.2、8.3 节说明 Agent 层级和子代理含义。

### Review 12

- 源码对应：已检查 `director-gates.md`。
- 发现问题：full、lean、solo 三种评审强度没有给建议。
- 修复：第 19.4 节给出新手建议使用 `lean`。

### Review 13

- 源码对应：已检查 `context-management.md`。
- 发现问题：为什么要写 `active.md` 不清楚。
- 修复：第 8.6 节解释文件才是长期记忆，聊天记录会压缩。

### Review 14

- 源码对应：已检查 `technical-preferences.md`。
- 发现问题：`/setup-engine` 改哪些文件需要量化。
- 修复：第 10.4 第 4 步列出 8 项具体行为。

### Review 15

- 源码对应：已检查 `README.md`。
- 发现问题：需要把官方宣传口径和本地事实区分。
- 修复：第 1、2 节用本地 `src/` 事实说明它不是已完成游戏源码。

### Review 16

- 源码对应：已检查 `.github`、`CONTRIBUTING.md`、`SECURITY.md`。
- 发现问题：贡献和安全文件不是主线，过度展开会干扰小白。
- 修复：第 3 节只在目录总览中提到 `.github`，不把它放入运行机制主线。

### Review 17

- 源码对应：已检查 Codex 本地 Skill 结构。
- 发现问题：Codex 能否原样运行必须直说。
- 修复：第 14.1 节明确“不可以原样同样运行”，并用表格列出原因。

### Review 18

- 源码对应：已检查 Codex `skill-creator` 说明。
- 发现问题：Codex 改造不能只说“转换一下”，需要给路径和字段规则。
- 修复：第 14.4、15 节补充 Codex Skill 目录、frontmatter 示例和字段映射。

### Review 19

- 源码对应：已检查 Hook 脚本。
- 发现问题：Codex 没有 Claude Hook 时怎么替代不清晰。
- 修复：第 14.4 第四层给出 Git Hook、手动脚本、CI 三种替代方案。

### Review 20

- 源码对应：已检查常用命令流程。
- 发现问题：从 0 到运行 Godot 的步骤需要更像操作清单。
- 修复：第 10.4 节补齐从 `cd`、`claude`、`/start` 到 Godot Editor 创建项目和运行主场景。

### Review 21

- 源码对应：已再次对照关键路径清单。
- 发现问题：专业名词散落在文中，首次阅读时仍可能卡住。
- 修复：第 17 节新增概念词典，解释 Agent、Skill、Hook、GDD、ADR、Epic、Story、Sprint、Gate、Vertical Slice、Prototype、Control Manifest、Technical Requirement。

### Review 22

- 源码对应：已核对文档是否引用了不存在的核心路径。
- 发现问题：需要一张总的“说法到源码位置”表，方便读者复查。
- 修复：第 18 节新增源码与文档对应清单。

### Review 23

- 源码对应：已核对本地 `docs/engine-reference/godot/VERSION.md` 与 Godot 官方下载页、官方归档页。
- 发现问题：归档页会显示比官方下载页更多的版本号，小白可能把“归档中存在”误认为“当前推荐稳定版”。
- 修复：第 6.2 节补充 2026-05-17 的外部核对结论：官方下载页稳定版是 `4.6.2`，归档页列出的 `4.6.3`、`4.7` 不能直接等同于推荐稳定下载。

### Review 24

- 源码对应：已核对 73 个 `.claude/skills/*/SKILL.md` 的 YAML frontmatter。
- 发现问题：第 4.3 节最初解释 Skill 头部时漏写了 `model` 字段，而源码中的 Skill 都配置了模型层级。
- 修复：第 4.3 节补充 `model` 字段示例，并解释 `haiku`、`sonnet`、`opus` 三个模型层级的用途。

### Review 25

- 源码对应：重新核对 `.claude/agents/*.md` 的 49 个 Agent。
- 发现问题：原文只分组列出 Agent，没有逐个说明它们的角色能力和能力来源。
- 修复：第 4.2.1、4.2.2 节新增 Agent 生效机制和 49 个 Agent 逐个能力分析表，说明每个 Agent 的角色功能、实现方式和典型输出。

### Review 26

- 源码对应：重新核对 `.claude/skills/*/SKILL.md` 的 73 个 Skill。
- 发现问题：原文只按分类列出 Skill，没有逐个说明每个命令如何实现能力、读取什么、产出什么。
- 修复：第 4.4.1、4.4.2 节新增 Skill 生效机制和 73 个 Skill 逐个能力分析，按分类列出每个命令的用途、实现方式和典型产物。

### Review 27

- 源码对应：重新核对 `.claude/settings.json`、`.claude/hooks/*.sh`、`.claude/rules/*.md`、`.claude/docs/*`。
- 发现问题：原文没有把 Hook、Rules、Docs 的“是什么”和“如何在项目中生效”拆开说明。
- 修复：第 4.5、4.6、4.7 节分别补充 Hook 触发链路、Rules 路径规则生效方式、Docs 作为制度/模板/索引被 CLAUDE.md、Skill、Agent 读取的机制。

---

## 22. 最短可执行路线

如果你只想马上开始，按这个最短路线走：

```text
1. 安装 Git、Claude Code、Godot 官方稳定版。
2. 进入 Claude-Code-Game-Studios 仓库。
3. 运行 claude。
4. 输入 /start。
5. 选择 lean 评审模式。
6. 输入 /setup-engine godot [你的 Godot 版本]。
7. 语言选 GDScript。
8. 输入 /brainstorm open，得到 design/gdd/game-concept.md。
9. 输入 /map-systems，得到 systems-index.md。
10. 输入 /design-system player-movement。
11. 输入 /create-architecture。
12. 输入 /architecture-decision。
13. 输入 /create-epics layer: foundation。
14. 输入 /create-stories player-movement。
15. 输入 /dev-story [第一条 story 路径]。
16. 打开 Godot，创建或打开同一仓库下的 project.godot。
17. 运行主场景。
```

这条路线不是最严谨路线，但足够让小白从“完全没有”走到“第一个可实现功能”。
