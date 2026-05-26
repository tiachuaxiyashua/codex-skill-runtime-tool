---
name: game-audio-pipeline
description: Generate, postprocess, validate, and integrate game audio assets for Godot projects through a local Stability Matrix ComfyUI backend.
allowed-tools: read_file, write_file, edit_file, glob, grep, bash, mcp, task, agent
disable-model-invocation: false
---

# Game Audio Pipeline Skill

Use this skill when a game workflow needs real generated music, ambience, stingers, UI sound effects, menu loops, battle loops, short cinematic cues, dialogue voice lines, narration, creature sounds, or monster vocalizations.

Do not use this skill for placeholder beeps or manually described audio that is not generated and validated.

## Required Workflow

1. Locate the active game project directory. Keep all generated audio under that project.
2. Create or read `audio_src/AUDIO_BIBLE.md` and `audio_src/style_profile.json`.
3. Write an `audio_src/requests/<audio-pack>.json` request using `${CLAUDE_PLUGIN_ROOT}/schemas/audio-request.schema.json`.
4. Call `mcp__audio-pipeline__doctor`. If Stability Matrix ComfyUI, required nodes, or model files are unavailable, stop with the diagnostic.
5. If a narrower diagnostic is needed, call `mcp__audio-pipeline__check_backend` and `mcp__audio-pipeline__check_models`.
6. Call `mcp__audio-pipeline__generate_audio_pack` with:
   - `spec_path`
   - `output_dir`
   - `manifest_path`
   - optional `style_profile_path`
7. Call `mcp__audio-pipeline__validate_audio_pack` for measurable file, duration, sample rate, channel, and format checks.
8. Listen to the generated output when possible. Use `audio-qa` or request human review if listening is unavailable.
9. Call `mcp__audio-pipeline__record_listening_review` with `decision: approved` or `decision: rejected`, reviewer identity, and concrete notes.
10. For Godot projects, call `mcp__audio-pipeline__godot_audio_import_smoke` after the approved audio files are copied under the Godot project.
11. Integrate only assets whose manifest has `ready_for_integration: true` and whose Godot import smoke passes when Godot integration is requested.

## Routing

Each asset can set `pipeline` explicitly. If omitted, the runtime routes from `type` and `usage`.

- `ace_step`: BGM, menu loops, battle loops, stingers, short cinematic music.
- `qwen_tts`: human dialogue, narration, UI announcer lines, story voice lines.
- `mmaudio`: sound effects, ambience, environment beds, UI sounds, creature sounds, monster voice/noise.

Use explicit `pipeline` when a type is ambiguous. Example: a `monster_warning_line` that must be spoken by a human narrator should use `qwen_tts`; a nonverbal monster roar should use `mmaudio`.

For QwenTTS assets, provide `text` or `script` and optionally `speaker`, `voice`, `instruct`, `language`, `model_size`, and `max_new_tokens`.

For MMAudio assets, provide `prompt` or `description`, `duration_seconds`, optional `negative_prompt`, `steps`, and `cfg`.

## Stability Matrix / ComfyUI Baseline

This skill expects ComfyUI from Stability Matrix at `http://127.0.0.1:8188` unless `COMFYUI_BASE_URL` is configured differently.

For ACE-Step 1.5 text-to-audio, the backend must expose ComfyUI nodes:

- `UNETLoader`
- `DualCLIPLoader`
- `VAELoader`
- `TextEncodeAceStepAudio1.5`
- `EmptyAceStep1.5LatentAudio`
- `ConditioningZeroOut`
- `KSampler`
- `ModelSamplingAuraFlow`
- `VAEDecodeAudio`
- `SaveAudio`

Required model files follow the ComfyUI blueprint `Text to Audio (ACE-Step 1.5)`:

- `models/diffusion_models/acestep_v1.5_turbo.safetensors`
- `models/text_encoders/qwen_0.6b_ace15.safetensors`
- `models/text_encoders/qwen_4b_ace15.safetensors`
- `models/vae/ace_1.5_vae.safetensors`

For QwenTTS human voice, the backend must expose:

- `AILab_Qwen3TTSCustomVoice_Advanced`
- `SaveAudio`

Required local model folders:

- `models/TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/`

The current `CustomVoice` route reads its speech tokenizer from the model folder's `speech_tokenizer/` subdirectory; a separately downloaded standalone tokenizer folder is not required for this route.

For MMAudio sound effects and creature/ambience audio, the backend must expose:

- `MMAudioModelLoader`
- `MMAudioFeatureUtilsLoader`
- `MMAudioSampler`
- `SaveAudio`

Required local model files:

- `models/mmaudio/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors`
- `models/mmaudio/mmaudio_large_44k_v2_fp16.safetensors`
- `models/mmaudio/mmaudio_synchformer_fp16.safetensors`
- `models/mmaudio/mmaudio_vae_44k_fp16.safetensors`
- `models/mmaudio/nvidia/bigvgan_v2_44khz_128band_512x/config.json`
- `models/mmaudio/nvidia/bigvgan_v2_44khz_128band_512x/bigvgan_generator.pt`

If models are missing, do not fake generation. Report the missing files and ask the user to download them in Stability Matrix or place them in the listed model folders. `download-models` can install known public files when network access is available.

## Asset Defaults

Start from `${CLAUDE_PLUGIN_ROOT}/templates/style_profile.ace-step-game-audio.json` and `${CLAUDE_PLUGIN_ROOT}/templates/sample_audio_request.json`.

Keep generation settings in top-level `generation`:

- `duration_seconds`
- `bpm`
- `keyscale`
- `timesignature`
- `language`
- `cfg_scale`
- `steps`
- `sampler_name`
- `scheduler`
- `output_format`
- `pipeline`
- `text` / `script`
- `speaker`
- `voice_instruct`
- `max_new_tokens`
- `negative_prompt`
- `cfg`

For game use, prefer 3-12 second loops or stingers for first validation. Long music should be generated only after the backend and short-pass QA are stable.

## Output Evidence

A completed audio pack must leave:

- source request JSON
- generated audio files
- `audio_manifest.json`
- validation report
- recorded listening review status and `ready_for_integration`
- Godot import smoke result when the asset is for a Godot project
- tags/lyrics/prompt, seed, duration, backend/model details

Do not claim the audio pack is complete without these files.
