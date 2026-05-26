# 2D 美术素材生产 Skill

这个目录是独立的 Claude/Codex skill plugin，不属于 CCGS，也不属于 runtime 核心。

它的职责是：

- 将游戏素材需求整理为结构化请求。
- 通过 Forge/A1111 兼容 API 生成 PNG 素材。
- 使用项目级 `style_profile.json` 固定风格配置。
- 处理透明背景、尺寸标准化和 PNG 校验。
- 输出可供 Godot 项目引用的素材路径、manifest 和验证报告。

## 运行前提

1. Stability Matrix 中启动 `Stable Diffusion WebUI Forge - Neo`。
2. Forge 启动参数包含 `--api`。
3. Forge 已选择一个 checkpoint 模型。
4. 默认 API 地址为 `http://127.0.0.1:7860`。

检查后端：

```powershell
python .\art_pipeline_skill\scripts\assetgen_cli.py check-backend
```

## 独立 CLI 验证

```powershell
python .\art_pipeline_skill\scripts\assetgen_cli.py generate `
  --spec .\game_projects\<project>\art_src\requests\<pack>.json `
  --style-profile .\game_projects\<project>\art_src\style_profile.json `
  --out .\game_projects\<project>\godot_project\assets\generated\<pack> `
  --manifest .\game_projects\<project>\reports\asset_manifest.json
```

输出包括：

```text
game_projects/<project>/godot_project/assets/generated/<pack>/*.source.png
game_projects/<project>/godot_project/assets/generated/<pack>/*.png
game_projects/<project>/reports/asset_manifest.json
game_projects/<project>/reports/validation_report.json
```

## 与 CCGS 同时加载

推荐以正在制作的游戏项目为运行根目录，并将 CCGS 与本 plugin 都作为追加目录。这样素材、报告和 runtime 会话不会写入原始 skill 仓库：

```powershell
python -B .\codex_skill_runtime_tool\skill-runtime.py `
  --runtime-env .\codex_skill_runtime_tool\codex-skill-runtime\skill-runtime.env `
  --root .\game_projects\<project> `
  --add-dir .\game_studio_source_code\Claude-Code-Game-Studios `
  --add-dir .\art_pipeline_skill `
  --strict-tools `
  --assume-yes `
  --qa required `
  --godot <godot-executable-or-dir> `
  run /prototype "制作一款使用真实AI生成2D美术素材的Godot游戏。需要角色、道具、技能图标与UI，不允许仅使用占位图；需要调用2d-art-pipeline并提供素材验证报告。"
```

运行时可见能力：

```text
skill: art-pipeline-skill:2d-art-pipeline
agent: art-director
agent: asset-qa
MCP: mcp__asset-pipeline__check_backend
MCP: mcp__asset-pipeline__generate_asset_pack
MCP: mcp__asset-pipeline__validate_asset_pack
MCP: mcp__asset-pipeline__record_visual_review
```

## 视觉准入门禁

`validate_asset_pack` 只检查文件、尺寸、透明通道与路径是否成立，不能证明图像内容符合需求。生成素材后，必须查看实际 PNG，再调用 `record_visual_review` 写入 `approved` 或 `rejected`。只有 manifest 中 `ready_for_integration` 为 `true` 的素材才允许接入 Godot。

多个素材包使用不同 manifest 文件名时，其验证报告会分别保存为 `<manifest-name>.validation_report.json`，不会覆盖彼此的证据。默认的 `asset_manifest.json` 继续对应 `validation_report.json`。

当前已验证的 `animagineXLV31_v31` 更适合动漫角色肖像。透明道具图标的键控去背可能删除主体颜色，必须经过视觉审核，必要时更换擅长物件图标的 checkpoint 或独立去背后端。

## 项目级风格控制

每一个游戏项目应保存自己的风格文件，而不是依赖聊天上下文：

```text
game_projects/<project>/
  art_src/
    ART_BIBLE.md
    style_profile.json
    requests/
      inventory_icons.json
  godot_project/
    assets/generated/
  reports/
    asset_manifest.json
    validation_report.json
```

模板位置：

```text
art_pipeline_skill/templates/style_profile.animagine-xl-2d-game.json
art_pipeline_skill/templates/sample_asset_request.json
art_pipeline_skill/templates/style_profile.animagine-xl-character-portrait.json
art_pipeline_skill/templates/sample_character_portrait_request.json
```

`style_profile.json` 决定：

- 固定基础 prompt。
- 固定 negative prompt。
- 使用的模型意图和采样设置。
- 透明背景策略。
- 各类素材的镜头和构图规则。
- 最终素材尺寸和留白要求。

每次生成会把风格配置计算为 `style_hash` 写进 manifest。后续批次如使用不同配置，hash 会改变，QA 可以识别出风格批次变化。

## 当前验证覆盖

管线验证检查：

- Forge API 是否在线并加载 checkpoint。
- Forge 是否实际生成图片。
- 输出 PNG 是否能打开。
- 最终尺寸是否精确匹配请求。
- 透明素材是否具备透明像素。
- manifest 与 validation report 是否落盘。

视觉一致性仍需要 `art-director` 和 `asset-qa` 依据同一风格 profile 审查多张素材；自动校验不能单独证明“画风美观或完全一致”。
