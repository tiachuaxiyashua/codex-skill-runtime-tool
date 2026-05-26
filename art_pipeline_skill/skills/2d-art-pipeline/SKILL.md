---
name: 2d-art-pipeline
description: Generate, postprocess, validate, and integrate 2D game art assets for Godot projects through a local Forge/A1111-compatible image backend.
allowed-tools: read_file, write_file, edit_file, glob, grep, bash, mcp, task, agent
disable-model-invocation: false
---

# 2D Art Pipeline Skill

Use this skill when a game workflow needs real generated 2D art assets: characters, enemies, item icons, equipment icons, skill icons, UI panels, background illustrations, tiles, or visual effects.

Do not use this skill for simple color placeholders, debug primitives, or CSS/SVG-only mockups.

## Required Workflow

1. Locate the active game project directory. Keep all generated game assets inside that project.
2. Create or read `art_src/ART_BIBLE.md` and `art_src/style_profile.json`.
3. Write an `art_src/requests/<asset-pack>.json` request using the schema in `${CLAUDE_PLUGIN_ROOT}/schemas/asset-request.schema.json`.
4. Call MCP tool `mcp__asset-pipeline__check_backend`. If Forge/A1111 is unavailable or no model is loaded, stop with the diagnostic.
5. Call MCP tool `mcp__asset-pipeline__generate_asset_pack` with:
   - `spec_path`
   - `output_dir`
   - `manifest_path`
   - optional `style_profile_path`
6. Call MCP tool `mcp__asset-pipeline__validate_asset_pack` for measurable file, size, and alpha checks.
7. Inspect the rendered PNG output against the request and style profile. Use `asset-qa` or request human review if visual inspection is unavailable.
8. Call MCP tool `mcp__asset-pipeline__record_visual_review` with `decision: approved` or `decision: rejected`, the reviewer identity, and concrete notes.
9. Integrate only assets whose manifest has `ready_for_integration: true`. A technical `validation.status: passed` is not visual approval.
10. When integrating into Godot, reference the approved generated PNG paths from the manifest.

## Asset Type Defaults

For character or dialogue portraits with the bundled Animagine model, start from `${CLAUDE_PLUGIN_ROOT}/templates/style_profile.animagine-xl-character-portrait.json` and `${CLAUDE_PLUGIN_ROOT}/templates/sample_character_portrait_request.json`.

Keep generation settings under top-level `generation`, not only under `backend`, `backend_generation_settings`, or prose notes. A portrait profile should preserve:

- `base_prompt_prefix` with Animagine quality tags and `1girl, solo` when requesting a single heroine.
- `negative_prompt` that rejects text, watermarks, collages, multiple views, photorealism, and low quality output.
- `generation.sampler_name: "DPM++ 2M"`, `generation.steps: 28`, `generation.cfg_scale: 5.5`, source `width`/`height` 512, and final target 256x256.
- `final_asset_rules.require_alpha: false` for opaque dialogue portraits.

Do not invent a new portrait sampler/profile unless the user explicitly asks to experiment. If a generated image fails visual review, retry from the validated portrait template before changing model families.

For transparent item or skill icons, prefer `${CLAUDE_PLUGIN_ROOT}/templates/style_profile.animagine-xl-transparent-icon-rembg.json` and `${CLAUDE_PLUGIN_ROOT}/templates/sample_transparent_icon_rembg_request.json`.

Transparent icons must declare a transparency strategy:

- Use `transparency_strategy: "rembg"` for normal production attempts. This generates the object on a simple neutral background, then removes the background with a segmentation model.
- Use `transparency_strategy: "chroma_key"` only for controlled tests where the object colors cannot collide with the key color.
- Do not approve a transparent icon only because alpha exists. QA must inspect whether the subject is still intact, centered, readable at 32x32, and not clipped by the removal step.

## Global Style Rules

Style must live in files, not only in conversation memory.

Every generated asset request must include or reference:

- `style_profile_id`
- base prompt prefix
- negative prompt
- target dimensions
- transparency policy
- camera/view rule
- palette or rendering rule
- backend generation settings

The pipeline records a `style_hash` in the manifest. If the style profile changes, new assets must be treated as a new style generation batch.

## Forge Backend Notes

This skill expects a local Forge/A1111-compatible backend at `http://127.0.0.1:7860` unless `FORGE_BASE_URL` is configured differently.

The backend must expose:

- `GET /sdapi/v1/sd-models`
- `GET /sdapi/v1/options`
- `POST /sdapi/v1/txt2img`

If Stability Matrix starts Forge, ensure the package launch arguments include `--api` and a checkpoint is loaded.

## Output Evidence

A completed asset pack must leave:

- source request JSON
- generated PNG files
- `asset_manifest.json`
- `validation_report.json`
- recorded visual review status and `ready_for_integration`
- prompts and seeds used for generation
- backend/model details

Do not claim the asset pack is complete without these files.
