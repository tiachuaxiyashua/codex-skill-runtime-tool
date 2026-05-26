# 2026-05-24 参考项目机制移植补充

本文件记录用户要求“除 marketplace 完整生命周期外，直接移植其他机制”后的完成项。

- [x] 除 marketplace 完整生命周期外，补齐参考项目暴露出的执行效果机制：system prompt 分层、output style、custom/append system prompt、section cache。
- [x] 补齐 transcript JSONL 记录、read-state、large result replacement manifest 与 `resume` 命令的 replay context。
- [x] 补齐 MCP OAuth/token 执行生命周期：安全 token store、authCommand/tokenCommand、stored Authorization header、401/403 后 refresh/retry、`mcp__server__authenticate` 伪工具、CLI `mcp-auth` 启动/完成授权码流程。
- [x] 补强远程 MCP OAuth 边界：SSE authCommand、WebSocket 401/403 refresh/retry、标准 refresh_token 到 token endpoint 的刷新路径。
- [x] 补齐 strict-loop microcompact 执行等效层：旧的大型 observation 落盘，prompt 中替换为稳定占位和完整文件路径，保留最近 observation。
- [x] 补齐 Bridge 本地执行等效层：register、enqueue、poll、ack、heartbeat、session_event、archive、reconnect pointer、prompt context 注入。
- [x] 补齐 Voice 本地执行等效层：start、append、finalize、load/latest transcript、prompt context 注入。
- [x] 补齐 IDE/LSP 本地执行等效层：selection、diagnostics、lsp_command、prompt context 注入。
- [x] 将 `bridge`、`voice`、`ide`、`send_message`、`task_stop`、`memory_read`、`memory_write` 加入 strict action schema 和 prompt tool list。
- [x] 新增 selftest：`system-prompt-contract`、`transcript-resume-contract`、`mcp-oauth-store-contract`、`bridge-voice-ide-contract`、`microcompact-contract`。
- [x] 按参考工程 prompt 的作用补 clean-room 行为契约：读代码再改、hook 反馈、拒绝工具不原样重试、prompt 注入识别、完成前验证、危险动作确认、专用工具优先、并行独立工具、子代理边界、skill 发现、MCP 指令、scratchpad、压缩事实保留、恢复前重新验证。
- [x] 新增 MCP 配置指令上下文：项目或插件 MCP server 配置中的 `instructions` / `instruction` 会进入 `Runtime MCP Server Instructions`，不动态加载参考 prompt。
- [x] 修复 Windows DPAPI token 解密后保留换行的问题，避免 Authorization header 因尾随换行失效。
- [x] 运行 `python -B -m compileall .\runtime .\schemas .\core_cli.py` 通过。
- [x] 运行 `python -B .\core_cli.py --assume-yes --qa off selftest` 通过：`SELFTEST_SUMMARY total=27 failed=0`。
- [x] 运行带 Godot、live strict、live QA 的完整 selftest 通过：`SELFTEST_SUMMARY total=27 failed=0`，关键 session 为 `20260524-154547-godot-smoke`、`20260524-154548-strict-smoke`、`20260524-154929-agent-qa-tester`。
- [x] marketplace browse/install/update 生命周期按用户要求不移植。
