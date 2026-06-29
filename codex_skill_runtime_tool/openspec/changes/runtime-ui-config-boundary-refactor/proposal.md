# Proposal: Runtime UI Config Boundary Refactor

## Why

`server.py` 当前同时承担 HTTP 路由、session/job 编排、项目文件服务、UI 事件投影、模型配置、环境变量解析、外部服务配置解析等职责。直接拆完整后端风险很高，因为工作区已有大量 runtime/UI 改动，且这些路径会影响真人使用。

本 change 先切出一个低风险边界：把 UI 后端中相对独立的环境配置、模型配置、服务配置解析逻辑从 `server.py` 移到独立模块。这样可以减少后端单体文件职责，同时为后续拆分 HTTP/session/process/UI projection 留出清晰接口。

## What Changes

- 新增 UI 后端配置模块，集中承载：
  - `skill-runtime.env` 读取与变量展开；
  - 模型配置读取、保存 payload 转换、受控 env 写回；
  - runtime env 导出；
  - 可配置外部服务解析；
  - 跨平台路径解析。
- `server.py` 通过该模块调用配置能力，不再内联这些解析函数。
- 增加针对配置模块的轻量自测，覆盖模型配置、路径、服务配置和 env 写回。

## Scope

本 change 只拆 UI 后端配置边界，不改 `/api/chat`、runtime action loop、ToolExecutor、skill 发现、agent 调度、MCP 执行、Godot/美术/音频管线逻辑。

## Non-Goals

- 不一次性拆完 `server.py`。
- 不拆 `tool_executor.py` 或 `selftest.py`。
- 不更改 runtime env 文件格式。
- 不把 runtime 特化为 CCGS 或游戏开发工具。

## Acceptance

- `server.py` 中不再定义模型/env/服务配置解析函数。
- UI 后端仍可读取健康信息、模型配置、服务配置和路径配置。
- 配置模块自测通过。
- Python 编译检查通过。
- OpenSpec strict 校验通过。
