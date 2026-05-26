# 游戏音频生产 Skill

这个目录是独立的 Claude/Codex skill plugin，不属于任何具体游戏公司 skill，也不属于 runtime 核心。它的职责是把游戏音频需求变成结构化请求，通过 Stability Matrix 管理的本地 ComfyUI 生成真实音频，并在当前游戏项目内留下 manifest、validation report 和听觉审核状态。

## 当前能力

这个 skill 现在支持三条本地 ComfyUI 管线：

- `ace_step`：BGM、菜单循环、战斗循环、stinger、短过场音乐。
- `qwen_tts`：中文/英文等人声台词、旁白、NPC 对话。
- `mmaudio`：音效、环境声、UI 声、怪物叫声、非语言生物声。

每个 asset 可以显式写 `pipeline`。如果不写，脚本会根据 `type` 和 `usage` 自动判断。

## 诊断

在工程根目录运行：

```powershell
python -B .\audio_pipeline_skill\scripts\audiogen_cli.py `
  --comfyui-root "<Stability Matrix 的 ComfyUI 目录>" `
  doctor
```

只检查某一条管线：

```powershell
python -B .\audio_pipeline_skill\scripts\audiogen_cli.py `
  --comfyui-root "<Stability Matrix 的 ComfyUI 目录>" `
  doctor --pipelines qwen_tts
```

## 生成

请求文件放在当前游戏项目内，例如：

```text
audio_src/requests/mixed_pack.json
```

调用：

```powershell
python -B .\audio_pipeline_skill\scripts\audiogen_cli.py `
  --project-root .\game_projects\<project> `
  --comfyui-root "<Stability Matrix 的 ComfyUI 目录>" `
  generate `
  --spec audio_src\requests\mixed_pack.json `
  --out assets\generated\audio `
  --manifest reports\mixed_pack_manifest.json
```

生成完成后会得到：

- `assets/generated/audio/*.wav`
- `reports/*manifest.json`
- `reports/*validation_report.json`

## 请求格式

参考：

```text
audio_pipeline_skill/templates/sample_audio_request.json
audio_pipeline_skill/schemas/audio-request.schema.json
```

最重要字段：

- `id`：资产 ID，会用于输出文件名。
- `type`：资产类型，例如 `music_loop`、`voice_line`、`monster_sfx`。
- `pipeline`：可选，显式指定 `ace_step`、`qwen_tts` 或 `mmaudio`。
- `description` / `prompt`：音乐和音效生成提示。
- `text` / `script` / `line`：QwenTTS 人声台词文本。
- `duration_seconds`：目标生成时长。
- `target_duration_seconds`：验证时用于对比的目标时长。
- `seed`：随机种子。
- `format`：通常使用 `wav`，方便 Godot 导入。

## 模型位置

默认 ComfyUI API：

```text
http://127.0.0.1:8188
```

ACE-Step 需要：

```text
models/diffusion_models/acestep_v1.5_turbo.safetensors
models/text_encoders/qwen_0.6b_ace15.safetensors
models/text_encoders/qwen_4b_ace15.safetensors
models/vae/ace_1.5_vae.safetensors
```

QwenTTS 需要：

```text
models/TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/
```

当前 `CustomVoice` 路线会使用这个模型目录内的 `speech_tokenizer/` 子目录；单独的 `Qwen3-TTS-Tokenizer-12Hz` 下载目录不是这条路线的运行前提。

MMAudio 需要：

```text
models/mmaudio/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors
models/mmaudio/mmaudio_large_44k_v2_fp16.safetensors
models/mmaudio/mmaudio_synchformer_fp16.safetensors
models/mmaudio/mmaudio_vae_44k_fp16.safetensors
models/mmaudio/nvidia/bigvgan_v2_44khz_128band_512x/config.json
models/mmaudio/nvidia/bigvgan_v2_44khz_128band_512x/bigvgan_generator.pt
```

可用 `download-models` 下载已知公开模型文件：

```powershell
python -B .\audio_pipeline_skill\scripts\audiogen_cli.py `
  --comfyui-root "<Stability Matrix 的 ComfyUI 目录>" `
  download-models --pipelines ace_step qwen_tts mmaudio
```

## Runtime 加载方式

示例：

```powershell
python -B .\codex_skill_runtime_tool\skill-runtime.py `
  --runtime-env .\codex_skill_runtime_tool\codex-skill-runtime\skill-runtime.env `
  --root .\game_projects\<project> `
  inspect
```

应能看到：

```text
audio-pipeline-skill:game-audio-pipeline
audio-director
audio-qa
```

可用 MCP 工具：

```text
mcp__audio-pipeline__doctor
mcp__audio-pipeline__check_backend
mcp__audio-pipeline__check_models
mcp__audio-pipeline__generate_audio_pack
mcp__audio-pipeline__validate_audio_pack
mcp__audio-pipeline__record_listening_review
mcp__audio-pipeline__godot_audio_import_smoke
```

## Godot 导入 Smoke Test

如果生成音频要进入 Godot 项目，先把文件放入 Godot 项目目录，再运行：

```powershell
python -B .\audio_pipeline_skill\scripts\godot_audio_smoke.py `
  --godot-exe "<Godot console exe>" `
  --godot-project "<Godot project 目录>" `
  --asset "res://assets/generated/audio/example.wav"
```

它会运行 Godot headless import，并确认资源可被识别为 `AudioStream`。
