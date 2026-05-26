# 2026-05-24 验证证据

## 编译验证

命令：

```powershell
python -B -m compileall .\codex-skill-runtime-core
```

结果：通过。

## 普通 Selftest

命令：

```powershell
python -B .\core_cli.py --assume-yes --qa off selftest
```

关键结果：

```text
PASS: mcp-bridge-contract - session=20260524-152410-selftest-mcp
PASS: memory-compaction-contract - session=20260524-152440-selftest-memory
PASS: microcompact-contract - session=20260524-152440-selftest-microcompact
PASS: system-prompt-contract - session=20260524-152440-selftest-system-prompt runtime_session=20260524-152440-agent-qa-tester
PASS: transcript-resume-contract - session=20260524-152442-selftest-transcript resume_session=20260524-152442-resume
PASS: mcp-oauth-store-contract - session=20260524-152442-selftest-mcp-oauth
PASS: bridge-voice-ide-contract - session=20260524-152445-selftest-bridge-voice-ide
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=27 failed=0
```

## 完整 Live Selftest

命令：

```powershell
python -B .\core_cli.py --godot <godot-executable-or-dir> --assume-yes --qa off selftest --godot-project <godot-project> --live-strict-target README.md --live-qa-target <godot-project>
```

2026-05-24 15:44-15:54 的最终结果：

```text
PASS: godot-contract - session=20260524-154547-godot-smoke
PASS: live-strict-contract - session=20260524-154548-strict-smoke
PASS: live-codex-qa-contract - session=20260524-154929-agent-qa-tester gate=PASS
PASS: claude-tree-clean - .claude diff is empty
SELFTEST_SUMMARY total=27 failed=0
```

## 验证说明

- `mcp-bridge-contract` 证明 stdio、HTTP、SSE MCP、headersHelper、HTTP authCommand、SSE authCommand 都可执行。
- `mcp-oauth-store-contract` 证明 authCommand/token store、OAuth 授权 URL、授权码完成、Authorization header 注入、refresh_token 刷新可用。
- `microcompact-contract` 证明 strict action-loop 的旧大型 observation 会落盘并替换为稳定占位，最近 observation 仍保留。
- `system-prompt-contract` 证明 clean-room system prompt 分层、output style、custom/append prompt 已进入真实 runtime prompt。
- `transcript-resume-contract` 证明 transcript replay、read-state、summary、large-result manifest 和 `resume` 命令可用。
- `bridge-voice-ide-contract` 证明 Bridge、Voice、IDE/LSP 机制既能直接调用，也能通过 strict tool executor 调用。
- `claude-tree-clean` 证明原始 `.claude` skill/agent/hook/settings 树没有被修改。
