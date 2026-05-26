# 参考项目机制移植记录（2026-05-24）

本记录对应用户要求：“除了 marketplace 完整生命周期，请直接移植其他的。”

## 边界

本次没有复制 Claude Code 私有 system prompt 原文，也没有实现 marketplace 的浏览、安装、更新、卸载完整生命周期。原因是用户明确排除了 marketplace 生命周期；私有 prompt 原文不应照抄。其余会影响公开 skill 执行效果的机制已按 clean-room 方式移植到 Codex runtime。

## 已移植机制

1. System prompt / output style / section cache
   - 新增 `runtime/system_prompt.py`。
   - 支持 `.claude/output-styles`、项目 `output-styles`、用户目录 output style。
   - 支持 `--system-prompt`、`--append-system-prompt`，参数可以是文本，也可以是 `@file`。
   - runtime profile 现在会合并原有 compatibility profile 与 clean-room system prompt 层。

2. Transcript replay / resume
   - `RuntimeSession` 同时写 `events.jsonl` 和 `transcript.jsonl`。
   - `CodexCLI` 会记录 prompt prepared / assistant captured transcript event。
   - 新增 `core_cli.py resume <session> ...`，会从 transcript、summary、read-state、large-result manifest 重建上下文。

3. MCP OAuth / token 生命周期
   - 新增 `runtime/secure_store.py` 和 `runtime/mcp_oauth.py`。
   - 支持 Windows DPAPI；不可用时降级为 `.codex-skill-runtime/secure-store/*.json`。
   - 支持 `authCommand`、`oauthRefreshCommand`、`tokenCommand`。
   - 支持 `mcp__<server>__authenticate` 伪工具：返回已认证、auth URL、unsupported 或 error。
   - 新增 `core_cli.py mcp-auth <server>`，可启动 OAuth URL 流程；也可用 `--callback-url` 或 `--code` 完成 token 写入。
   - HTTP MCP 初始化和工具调用遇到 401/403 会尝试 refresh/retry；没有凭据时返回明确 BLOCKED。
   - SSE MCP 现在同样覆盖 authCommand 和认证失败后的 refresh/retry。
   - WebSocket MCP 在握手阶段遇到 401/403/unauthorized/forbidden 时会尝试刷新 token 后重连。
   - 存在 refresh_token 和 token endpoint 时，会执行标准 OAuth refresh_token 刷新，不只依赖 authCommand。

4. Bridge
   - 新增 `runtime/bridge.py`。
   - 支持 register、enqueue、poll、ack、heartbeat、session_event、archive、reconnect。
   - reconnect pointer 会通过 `_context_bundle()` 注入后续 prompt。

5. Voice
   - 新增 `runtime/voice.py`。
   - 支持 start、append transcript、finalize、load/latest。
   - finalized transcript 会注入后续 prompt。

6. IDE / LSP
   - 新增 `runtime/ide.py`。
   - 支持 selection、diagnostics、lsp_command。
   - IDE selection 和 diagnostics 会注入后续 prompt。

7. Strict action-loop 工具面
   - `schemas/action-result.schema.json` 增加 `send_message`、`task_stop`、`memory_read`、`memory_write`、`bridge`、`voice`、`ide`。
   - `runtime/action_loop.py` 的 tool list 已同步。
   - `runtime/tool_executor.py` 已接入 Bridge / Voice / IDE action handler。

8. Microcompact / 大上下文控制
   - 新增 `runtime/microcompact.py`。
   - strict action-loop 的旧大型 observation 会落盘到 session 的 `microcompact/` 目录。
   - 后续 prompt 中只保留稳定占位、工具名、状态和完整结果路径，最近 observation 保持原样。
   - 这不是私有 provider cache editing API，但保留了执行效果：旧工具结果不再反复膨胀上下文，完整证据仍可追溯。

## 自测覆盖

新增并通过的自测：

- `system-prompt-contract`：验证 output style、custom/append system prompt、coordinator section 注入。
- `transcript-resume-contract`：验证 transcript replay、summary、read-state、large-result manifest 和 `resume` dry-run。
- `mcp-oauth-store-contract`：验证 authCommand token store、OAuth auth URL、authorization-code completion、stored Authorization header。
- `bridge-voice-ide-contract`：验证 Bridge/Voice/IDE 直接 API 与 strict tool executor 入口。
- `microcompact-contract`：验证旧大型 observation 的落盘、占位替换、manifest 和最近 observation 保留。

## 最新验证结果

```powershell
python -B -m compileall .\runtime .\schemas .\core_cli.py
```

结果：通过。

```powershell
python -B .\core_cli.py --assume-yes --qa off selftest
```

结果：

```text
SELFTEST_SUMMARY total=27 failed=0
```

## 结论

执行效果层面，除 marketplace 完整生命周期外，参考项目中会影响公开 skill 流程、工具、副作用、恢复、远程 MCP 认证、上下文注入的机制已经接入当前 Codex runtime，并由 selftest 证明。仍不声称逐字复刻 Claude Code 私有 prompt、私有 UI、私有云服务和模型内部缓存实现。
