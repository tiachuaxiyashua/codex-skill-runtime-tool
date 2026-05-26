# Project: Codex Skill Runtime

## Purpose

本项目的目标是用一个干净实现的外层运行时，把 Claude Code 风格 skill
仓库里的 `.claude/skills`、`.claude/agents`、hooks、QA gate 和测试流程接到
Codex CLI 上。Claude Code Game Studios 只是当前验证用 skill 仓库之一。

这里的“100% 还原”指 CCGS 这套技能组的可观察执行效果等价：同一个 slash
command 会读取同一份 skill，路由到同一个 agent，按同样的流程触发 Task、
AskUserQuestion、hook、QA gate、Godot 实测和证据落盘。

## Constraints

- 原始 `.claude/` skill、agent、rule、docs、settings、hook 文件是只读输入。
- 不使用泄露或未授权的 Claude Code 源码。
- 不追求复制 Claude Code 的隐藏 system prompt、私有 UI、缓存策略或模型内部判断。
- Codex CLI 是“大脑”；`codex-skill-runtime-core/` 是状态机、工具代理、hook 触发器和 gate 执行器。
- 所有运行证据必须写入 `.codex-skill-runtime/sessions/`。
- QA PASS 必须有 `VERDICT` 和 `EVIDENCE MATRIX`。
- Godot 通过必须来自真实 headless 进程退出码和测试脚本输出，而不是文字承诺。

## Runtime Owner

`codex-skill-runtime-core/`

## Verification Standard

这个 change 只有在下面条件全部满足时才算完成：

- OpenSpec change 能通过 `openspec validate codex-runtime-equivalence --strict`。
- 运行时能加载全部 CCGS skills/agents，并能正确读取 frontmatter。
- strict action-loop 能让 Codex 返回结构化 action，并由 runtime 执行工具。
- runtime 能执行 Read/Glob/Grep/Write/Edit/Bash/Task/AskUserQuestion/Godot smoke。
- runtime-owned Write/Edit/Bash/Task/session 事件能触发 `.claude/settings.json` 配置的 hooks。
- 真实 `qa-tester` 子代理能跑完，并通过 QA gate。
- Godot 测试项目能被 runtime 真实运行并通过。
- `.claude` 在整个过程后仍然没有本地修改。
