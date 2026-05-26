# 参考工程 Prompt 作用分析与 Clean-room 迁移说明

日期：2026-05-24

本文说明本轮对 `<reference-project>` 中 prompt 相关代码的分析结论，以及当前 Codex runtime 已经添加的关键行为约束。

核心原则：

1. 不动态加载参考工程的 prompt。
2. 不照抄 Claude Code 私有 system prompt 原文。
3. 只分析 prompt section 对执行效果的作用，并用本工程自己的 clean-room 文案和机制实现相同方向的约束。
4. 目标不是 UI 文案一致，而是 skill/agent 在执行代码、调用工具、触发 hook、处理上下文、输出证据时更接近参考工程。

## 一、为什么 prompt 机制会影响 skill 执行效果

对 Claude Code skill 来说，`SKILL.md` 不是唯一输入。参考工程会在 skill 正文外叠加很多系统级约束，例如：

- 什么时候应该先读文件再改文件。
- 被 hook 拦截后应该怎么处理。
- 工具结果里出现“请忽略之前规则”这类文本时，应该把它当成外部数据还是更高优先级指令。
- 是否应该运行测试后再说完成。
- 是否允许删除文件、重置分支、推送代码、调用外部服务。
- 子代理应该如何分工，主代理是否能重复子代理已经在做的事。
- 旧工具结果可能被压缩或清理时，模型应该提前保存哪些关键事实。

如果 Codex runtime 只把 `SKILL.md` 原文发送给 Codex，而没有这些系统级行为约束，那么同一个 skill 在 Claude Code 和 Codex runtime 上的产出会出现差距：Claude Code 会更倾向于先读代码、遵守 hook、报告测试证据、避免危险动作；Codex runtime 则可能把这些隐含规则漏掉。

## 二、参考 prompt section 的作用拆解

以下为作用分析，不是参考 prompt 原文。

| 参考代码位置 | 作用 | 如果缺失会怎样 | 本轮迁移方式 |
|---|---|---|---|
| `getHooksSection` | 告诉模型 hook 输出属于流程反馈；hook 阻塞时要调整做法或让用户检查配置 | hook 拦截可能被忽略，模型可能重复同一个被拒绝动作 | 新增 `Hook-feedback contract`、`Denied-tool retry contract` |
| `getSystemRemindersSection` | 说明系统提醒可能混在工具结果或用户消息里；上下文会被压缩 | 模型可能把提醒误当作文件内容，或忘记长流程会压缩 | 新增 `System-reminder contract`、`Compaction fact-preservation contract` |
| `getLanguageSection` | 按用户/设置要求固定回复语言 | 输出语言不稳定，影响中文工作流和交付文档 | 新增 `Language contract` |
| `getOutputStyleSection` / `outputStyles.ts` | 根据输出风格改变解释粒度和呈现方式 | review、解释、小白模式等输出形态不一致 | 既保留已有 output-style 注入，又新增 `Output-style contract` |
| `getMcpInstructionsSection` | 把 MCP server 自带的工具使用说明注入给模型 | 模型能调 MCP 但不知道该 server 的特殊用法 | 新增 `MCP instruction contract`，并把 `.mcp.json` / plugin MCP 配置中的 `instructions` 注入上下文 |
| `getSimpleSystemSection` | 定义用户可见输出、权限拒绝、prompt 注入、自动压缩等基础规则 | 模型可能假设工具调用可见、重复被拒绝工具、被外部文本注入 | 新增用户可见沟通、权限拒绝、注入识别、上下文生命周期规则 |
| `getSimpleDoingTasksSection` | 定义软件工程任务习惯：先读代码、少做无关改造、失败要诊断、完成前验证、安全编码 | 模型可能不读代码就改、过度设计、不测试就说完成 | 新增 `Read-before-edit`、`Scope-control`、`Failure-diagnosis`、`Verify-before-complete`、`Security` |
| `getActionsSection` | 区分可逆本地动作和高风险动作；危险操作要确认 | 误删文件、重置分支、推送、改 CI、发外部消息等风险升高 | 新增 `Risk confirmation contract` |
| `getUsingYourToolsSection` | 优先使用专用工具；独立工具调用可并行；Todo/Task 及时更新 | 工具使用不透明，效率低，或用 shell 绕开 runtime 证据链 | 新增 `Dedicated-tool preference`、`Parallel independent tool`、`Tool-evidence` |
| `getAgentToolSection` | 说明何时使用 subagent/fork，避免重复劳动 | 主代理和子代理互相重复，或不该委派时委派 | 新增 `Delegation ownership`、`Subagent completion` |
| `getDiscoverSkillsGuidance` | 只使用已发现/可见的 skill，不猜隐藏技能名 | 模型可能调用不存在的 skill，流程中断 | 新增 `Skill-discovery contract` |
| `getSessionSpecificGuidanceSection` | 根据当前工具、skill、agent、AskUserQuestion 能力加入会话级约束 | 工具可见性和使用规则不稳定 | 新增 AskUserQuestion、Skill、Agent、工具可见性相关约束 |
| `getOutputEfficiencySection` / `getSimpleToneAndStyleSection` | 控制状态更新、最终答复、路径引用、避免表情符号和不可见工具调用前的冒号 | 用户看不到过程，最终报告缺少关键证据或路径 | 新增 `User-visible communication contract` 和输出证据规则 |
| `computeSimpleEnvInfo` / `computeEnvInfo` | 注入 cwd、git 状态、平台、shell、模型、日期、额外目录 | 模型可能在错误目录运行命令，或忽略 Windows/Unix shell 差异 | 新增 `Environment contract`；已有 runtime 继续注入项目上下文 |
| `DEFAULT_AGENT_PROMPT` / `enhanceSystemPromptWithEnvDetails` | 子代理要完成任务但不过度发挥；报告路径和关键发现 | 子代理输出不够可交付，主代理无法审计 | 新增 `Subagent completion contract` |
| `getScratchpadInstructions` | 临时文件写到 session scratchpad，不污染项目 | 临时脚本/中间产物散落到项目目录 | 新增 `Scratchpad temp-files contract` |
| `getFunctionResultClearingSection` / `SUMMARIZE_TOOL_RESULTS_SECTION` | 旧工具结果可能被清理，模型要提前保存重要事实 | 长任务后模型丢失早期关键结论 | 新增 `Compaction fact-preservation contract`，并保留已有 microcompact 机制 |
| `systemPromptSections.ts` | 区分稳定 section 和会破坏缓存的动态 section | 每轮 prompt 不稳定，缓存/上下文行为更难复现 | 已有 `runtime/system_prompt.py` section cache；本轮新增行为 section 使用稳定 cache key |
| `bootstrap/state.ts` 相关状态 | 维护 session、cwd、hooks、token、模型、invoked skills、prompt section cache、additional dirs 等状态 | 长流程恢复、权限、skill 可见性、hook 注册会不稳定 | 现有 runtime 已有 session/memory/hook/skill registry；本轮补 prompt 行为层 |

## 三、本轮实际加入的代码机制

### 1. `runtime/system_prompt.py`

新增三个固定 section：

- `Runtime Behavioral Contracts`
- `Runtime Tool And Delegation Contracts`
- `Runtime Context Lifecycle Contracts`

这些 section 覆盖以下关键行为：

- `Read-before-edit contract`：改代码前必须先读相关文件。
- `Denied-tool retry contract`：被拒绝/阻塞的工具不能原样重试。
- `Hook-feedback contract`：hook 输出会影响流程，不能只当日志。
- `Prompt-injection detection contract`：外部工具结果中的越权指令要识别为注入。
- `Verify-before-complete contract`：完成前要尽量验证，不能把未运行测试说成通过。
- `Risk confirmation contract`：危险、共享、外部可见、不可逆动作必须有对应授权。
- `Dedicated-tool preference contract`：优先使用 runtime 专用工具，避免用 shell 绕过证据链。
- `Parallel independent tool contract`：无依赖的工具调用应并行，减少等待。
- `Compaction fact-preservation contract`：大型旧结果可能被清理，要提前保留关键事实。
- `Delegation ownership contract`：Task/Agent 是真实委派，主代理要给清晰边界，避免重复工作。
- `Skill-discovery contract`：只调用可见或被引用的 skill，不猜名字。
- `MCP instruction contract`：MCP server 指令是该 server 的工具使用指南，但优先级低于用户/skill/agent/runtime 安全规则。
- `Scratchpad temp-files contract`：临时文件应进入 scratchpad，不污染项目。

### 2. `runtime/mcp.py`

新增 `mcp_instructions_context()`：

- 读取项目 `.mcp.json`、plugin `.mcp.json`、plugin manifest 中发现的 MCP server 配置。
- 如果 server 配置里存在 `instructions` 或 `instruction` 字段，则生成 `Runtime MCP Server Instructions` 上下文。
- 只使用公开配置字段，不连接远程服务动态抓取私有 prompt。
- 单个 server 指令有长度限制，过长会被截断并标注。

### 3. `runtime/runtime.py`

`_context_bundle()` 现在会把 `mcp_instructions_context()` 结果加入 Codex prompt 上下文。

这解决的是“工具已经存在，但模型不知道怎么正确用这个 MCP server”的问题。它不是 marketplace 生命周期，也不是 Claude Code 私有 UI；它只是把项目明确配置的 server 使用说明提供给模型。

### 4. `runtime/selftest.py`

自测新增断言：

- system prompt 必须包含新增三类行为 section。
- system prompt 必须包含关键行为 marker，例如 read-before-edit、hook-feedback、prompt-injection、verify-before-complete、risk confirmation、delegation、compaction 等。
- runtime 真实 dry-run prompt 也必须包含这些行为 section，防止只在单元构造里存在。
- MCP bridge 自测会创建带 `instructions` 的 `.mcp.json`，验证上下文能注入 `Runtime MCP Server Instructions`。

## 四、这是否等于“私有 system prompt 原文一致”

不等于，也不应该这样声称。

当前做到的是：

- 机制作用一致：把会影响 skill 执行效果的 prompt 行为约束加入 runtime。
- 文案来源独立：本工程使用自己的 clean-room 表述。
- 输入来源可审计：来自 runtime 代码、项目配置、skill/agent/frontmatter、MCP 配置和 session 证据。

当前没有做的是：

- 不保存或加载参考工程的私有 prompt 原文。
- 不动态读取 `<reference-project>` 的 prompt。
- 不伪装成 Claude Code 私有 UI 或 marketplace。
- 不声称模型内部缓存命中、云端压缩策略、私有 prompt 完全相同。

## 五、对 skill 执行效果影响最大的变化

按影响程度排序：

1. `Read-before-edit`、`Verify-before-complete`、`Faithful-outcome`：直接降低“没读代码就改”“没测试就说完成”的概率。
2. `Hook-feedback`、`Denied-tool retry`：让 hook 真正成为流程控制，而不是日志。
3. `Prompt-injection detection`：避免外部文件/网页/MCP 结果越权改变 runtime 行为。
4. `Risk confirmation`：避免危险动作在自动化 runtime 中失控。
5. `Delegation ownership`、`Subagent completion`：让多 agent skill 更接近 Claude Code 的 Task/Agent 语义。
6. `Compaction fact-preservation`、`Resume verification`：长流程、恢复流程更稳定。
7. `MCP instruction context`：让依赖 MCP 的 skill 更可能正确使用工具。
8. `Output-style`、`Language`、`User-visible communication`：提升交付报告一致性，但对代码副作用的影响相对间接。

## 六、小白版解释

可以把这套 prompt 机制理解成“公司规章制度”。

`SKILL.md` 像某个岗位的工作说明书，比如“让 QA 测游戏”。但真正工作时，员工还需要遵守公司通用规章：

- 开工前先看现场，不要凭空猜。
- 做完要检查，不能没测就说好了。
- 门禁系统拦住你，就不要硬闯，要看为什么被拦。
- 别人给你的文档里如果写“忽略老板命令”，那是文档内容，不是新老板。
- 临时纸稿放临时文件夹，不要乱塞进正式项目。
- 危险操作要先问，比如删库、强推、发外部消息。
- 派给 QA 的事就让 QA 做，主负责人最后整合结果。

参考工程的 prompt 就是在给模型灌这些“规章制度”。本轮做的事情，就是把这些规章制度用我们自己的话写进 Codex runtime，让它不需要引用参考工程原文，也能按类似规则执行 skill。

## 七、结论

本轮迁移后，当前工程在 prompt 行为层面比之前更接近参考工程：

- 之前：只有很薄的“Claude Code 兼容层”说明。
- 现在：补上了会影响执行效果的通用工程行为、工具行为、委派行为、上下文生命周期行为和 MCP 指令上下文。

仍需诚实说明：这不是 Claude Code 私有 system prompt 原文复刻，而是执行效果导向的 clean-room 行为迁移。

## 八、20 轮以上攻击式复核记录

复核方法：每轮都按三个问题检查。

1. 参考 prompt 中是否存在对应作用。
2. 本工程是否以 clean-room 机制承接，而不是动态加载或照抄。
3. 该机制是否会影响 skill 执行效果，是否有代码或自测覆盖。

| 轮次 | 攻击点 | 复核结论 |
|---:|---|---|
| 1 | 是否仍动态加载 `<reference-project>` prompt | 没有。runtime 没有读取参考目录；新增内容写死在本工程 `runtime/system_prompt.py`。 |
| 2 | 是否照抄私有 system prompt 原文 | 没有。新增 section 是 contract 化重写，使用本工程命名和表达。 |
| 3 | hook 被阻塞后是否会被忽略 | 已补 `Hook-feedback contract`；hook decision 仍由 `runtime/hooks.py` 和 selftest 覆盖。 |
| 4 | 被拒绝的工具是否会原样重试 | 已补 `Denied-tool retry contract`，要求读取原因并改方案。 |
| 5 | 工具结果里的 prompt injection 是否有防线 | 已补 `Prompt-injection detection contract`，把外部文本降级为数据。 |
| 6 | system reminder 混在观察结果中是否会被误当文件内容 | 已补 `System-reminder contract`，说明提醒是运行上下文。 |
| 7 | 未读代码就提修改建议的问题是否覆盖 | 已补 `Read-before-edit contract`。 |
| 8 | 过度重构、加无关功能、乱建文件是否覆盖 | 已补 `Scope-control contract`。 |
| 9 | 不运行测试却声称完成的问题是否覆盖 | 已补 `Verify-before-complete contract` 和 `Faithful-outcome contract`。 |
| 10 | 测试失败、hook 失败被包装成通过的问题是否覆盖 | 已补 faithful outcome；QA gate 仍要求 `VERDICT` 与 `EVIDENCE MATRIX`。 |
| 11 | 危险动作是否需要确认 | 已补 `Risk confirmation contract`，限定 destructive/shared/external/publication 等范围。 |
| 12 | shell 是否会绕过 runtime 证据链 | 已补 `Dedicated-tool preference contract`；严格模式仍通过 runtime tool executor 落证据。 |
| 13 | 独立工具调用是否能并行 | 已补 `Parallel independent tool contract`；strict action-loop 已支持多 action。 |
| 14 | Task/Agent 是否会被当成普通文字 | 已补 `Delegation ownership contract`；runtime 已有真实子代理调用与 hook。 |
| 15 | 子代理是否会输出不可审计结果 | 已补 `Subagent completion contract`，要求路径、命令、阻塞、verdict。 |
| 16 | AskUserQuestion 是否会滥用或不暂停 | 已补 `AskUserQuestion contract`；runtime 工具仍在无 assume-yes 时返回 BLOCKED。 |
| 17 | 模型是否会猜不存在的 skill 名称 | 已补 `Skill-discovery contract`；runtime 仍注入 visible skill registry。 |
| 18 | frontmatter 是否只是说明文字 | 已补 `Skill-frontmatter contract`；runtime 已执行 agent、model、effort、context fork、memory、MCP 等字段子集。 |
| 19 | MCP server 指令是否缺失 | 已补 `MCP instruction contract`，并新增 `mcp_instructions_context()` 注入配置中的 instructions。 |
| 20 | scratchpad 语义是否缺失 | 已补 `Scratchpad temp-files contract`；coordinator 模式会提供 scratchpad 路径。 |
| 21 | 大型旧工具结果被清理后是否丢事实 | 已补 `Compaction fact-preservation contract`；microcompact selftest 覆盖旧 observation 落盘替换。 |
| 22 | resume 后是否会误信旧 transcript | 已补 `Resume verification contract`，要求编辑/验证前重读 live files。 |
| 23 | 语言/输出风格是否影响交付 | 已补 `Language contract` 与 `Output-style contract`；原 output style 注入仍保留。 |
| 24 | cwd、平台、额外目录、日期等环境信息是否被忽略 | 已补 `Environment contract`；runtime context 继续注入项目上下文。 |
| 25 | 新增内容是否进入真实 runtime prompt，而不只是测试构造 | `system-prompt-contract` 会读取真实 dry-run prompt，确认行为 contract 已注入。 |
| 26 | `.claude` 原始 skill/agent/hook 是否被改动 | selftest 的 `claude-tree-clean` PASS，额外 `git diff -- .claude` 为空。 |

复核结论：本轮迁移覆盖的是参考 prompt 中会影响执行效果的通用行为机制。仍不声称私有 prompt 原文一致，也不实现 marketplace 完整生命周期。
