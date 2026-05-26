from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 900

ACE_MODEL_FILES = {
    "diffusion_models": {
        "name": "acestep_v1.5_turbo.safetensors",
        "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/diffusion_models/acestep_v1.5_turbo.safetensors",
    },
    "text_encoders": {
        "name": "qwen_0.6b_ace15.safetensors",
        "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_0.6b_ace15.safetensors",
    },
    "text_encoders_2": {
        "folder": "text_encoders",
        "name": "qwen_4b_ace15.safetensors",
        "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/text_encoders/qwen_4b_ace15.safetensors",
    },
    "vae": {
        "name": "ace_1.5_vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/vae/ace_1.5_vae.safetensors",
    },
}

PIPELINE_ACE_STEP = "ace_step"
PIPELINE_QWEN_TTS = "qwen_tts"
PIPELINE_MMAUDIO = "mmaudio"
SUPPORTED_PIPELINES = [PIPELINE_ACE_STEP, PIPELINE_QWEN_TTS, PIPELINE_MMAUDIO]

REQUIRED_NODES_BY_PIPELINE = {
    PIPELINE_ACE_STEP: [
        "UNETLoader",
        "DualCLIPLoader",
        "VAELoader",
        "TextEncodeAceStepAudio1.5",
        "EmptyAceStep1.5LatentAudio",
        "ConditioningZeroOut",
        "KSampler",
        "ModelSamplingAuraFlow",
        "VAEDecodeAudio",
        "SaveAudio",
    ],
    PIPELINE_QWEN_TTS: [
        "AILab_Qwen3TTSCustomVoice_Advanced",
        "SaveAudio",
    ],
    PIPELINE_MMAUDIO: [
        "MMAudioModelLoader",
        "MMAudioFeatureUtilsLoader",
        "MMAudioSampler",
        "SaveAudio",
    ],
}
REQUIRED_NODES = sorted({node for nodes in REQUIRED_NODES_BY_PIPELINE.values() for node in nodes})

QWEN_TTS_MODEL_FILES = [
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/config.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "generation_config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/generation_config.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "merges.txt",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/merges.txt",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "model.safetensors",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/model.safetensors",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "preprocessor_config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/preprocessor_config.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "tokenizer_config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/tokenizer_config.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "name": "vocab.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/vocab.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/speech_tokenizer",
        "name": "config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/speech_tokenizer/config.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/speech_tokenizer",
        "name": "configuration.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/speech_tokenizer/configuration.json",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/speech_tokenizer",
        "name": "model.safetensors",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/speech_tokenizer/model.safetensors",
        "backend_visible": False,
    },
    {
        "folder": "TTS/Qwen3-TTS/Qwen3-TTS-12Hz-0.6B-CustomVoice/speech_tokenizer",
        "name": "preprocessor_config.json",
        "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/resolve/main/speech_tokenizer/preprocessor_config.json",
        "backend_visible": False,
    },
]

MMAUDIO_MODEL_FILES = [
    {
        "folder": "mmaudio",
        "name": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
    },
    {
        "folder": "mmaudio",
        "name": "mmaudio_large_44k_v2_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_large_44k_v2_fp16.safetensors",
    },
    {
        "folder": "mmaudio",
        "name": "mmaudio_synchformer_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_synchformer_fp16.safetensors",
    },
    {
        "folder": "mmaudio",
        "name": "mmaudio_vae_44k_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_vae_44k_fp16.safetensors",
    },
    {
        "folder": "mmaudio/nvidia/bigvgan_v2_44khz_128band_512x",
        "name": "config.json",
        "url": "https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x/resolve/main/config.json",
        "backend_visible": False,
    },
    {
        "folder": "mmaudio/nvidia/bigvgan_v2_44khz_128band_512x",
        "name": "bigvgan_generator.pt",
        "url": "https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x/resolve/main/bigvgan_generator.pt",
    },
]


class AudioPipelineError(RuntimeError):
    pass


@dataclass
class BackendStatus:
    ok: bool
    base_url: str
    message: str
    available_nodes: list[str]
    missing_nodes: list[str]
    system_stats: dict[str, Any]
    checked_pipelines: list[str]
    available_pipelines: list[str]
    missing_nodes_by_pipeline: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "base_url": self.base_url,
            "message": self.message,
            "available_nodes": self.available_nodes,
            "missing_nodes": self.missing_nodes,
            "system_stats": self.system_stats,
            "checked_pipelines": self.checked_pipelines,
            "available_pipelines": self.available_pipelines,
            "missing_nodes_by_pipeline": self.missing_nodes_by_pipeline,
        }


def check_backend(base_url: str = DEFAULT_BASE_URL, timeout: int = 20, pipelines: list[str] | None = None) -> BackendStatus:
    base_url = base_url.rstrip("/")
    checked_pipelines = _normalize_pipelines(pipelines)
    required_nodes = _required_nodes_for_pipelines(checked_pipelines)
    try:
        stats = _get_json(f"{base_url}/system_stats", timeout=timeout)
        object_info = _get_json(f"{base_url}/object_info", timeout=timeout)
    except urllib.error.HTTPError as exc:
        return BackendStatus(False, base_url, f"ComfyUI responded HTTP {exc.code}.", [], required_nodes, {}, checked_pipelines, [], _missing_nodes_by_pipeline([], checked_pipelines))
    except urllib.error.URLError as exc:
        return BackendStatus(False, base_url, f"Cannot reach ComfyUI at {base_url}: {exc.reason}", [], required_nodes, {}, checked_pipelines, [], _missing_nodes_by_pipeline([], checked_pipelines))
    except Exception as exc:
        return BackendStatus(False, base_url, f"Cannot inspect ComfyUI backend: {exc}", [], required_nodes, {}, checked_pipelines, [], _missing_nodes_by_pipeline([], checked_pipelines))

    node_names = sorted(str(key) for key in object_info.keys()) if isinstance(object_info, dict) else []
    missing_by_pipeline = _missing_nodes_by_pipeline(node_names, checked_pipelines)
    missing = sorted({node for nodes in missing_by_pipeline.values() for node in nodes})
    available_pipelines = [pipeline for pipeline in checked_pipelines if not missing_by_pipeline.get(pipeline)]
    ok = not missing
    message = (
        "ComfyUI audio pipelines are ready: " + ", ".join(available_pipelines)
        if ok
        else f"ComfyUI is reachable, but required audio nodes are missing: {', '.join(missing)}"
    )
    return BackendStatus(ok, base_url, message, node_names, missing, stats if isinstance(stats, dict) else {}, checked_pipelines, available_pipelines, missing_by_pipeline)


def check_models(
    *,
    base_url: str = DEFAULT_BASE_URL,
    comfyui_root: Path | None = None,
    timeout: int = 20,
    pipelines: list[str] | None = None,
) -> dict[str, Any]:
    checked_pipelines = _normalize_pipelines(pipelines)
    comfyui_root = _resolve_comfyui_root(comfyui_root)
    expected = _expected_model_paths(comfyui_root, pipelines=checked_pipelines) if comfyui_root else []
    missing_files = [item for item in expected if not Path(item["path"]).exists()]
    backend_model_presence = _model_names_visible_in_backend(base_url=base_url, timeout=timeout)
    missing_in_backend: list[str] = []
    missing_in_backend_by_pipeline: dict[str, list[str]] = {pipeline: [] for pipeline in checked_pipelines}
    if backend_model_presence.get("checked"):
        visible_text = "\n".join(backend_model_presence.get("visible_model_names", []))
        for item in _expected_model_specs(checked_pipelines):
            if item.get("backend_visible", True) and str(item["name"]) not in visible_text:
                missing_in_backend.append(str(item["name"]))
                missing_in_backend_by_pipeline.setdefault(str(item["pipeline"]), []).append(str(item["name"]))
    missing_files_by_pipeline: dict[str, list[dict[str, Any]]] = {pipeline: [] for pipeline in checked_pipelines}
    for item in missing_files:
        missing_files_by_pipeline.setdefault(str(item["pipeline"]), []).append(item)
    ok_by_pipeline = {
        pipeline: bool(comfyui_root) and not missing_files_by_pipeline.get(pipeline) and not missing_in_backend_by_pipeline.get(pipeline)
        for pipeline in checked_pipelines
    }
    ok = bool(ok_by_pipeline) and all(ok_by_pipeline.values())
    if backend_model_presence.get("checked"):
        ok = ok and not missing_in_backend
    message = "Audio pipeline model files are present." if ok else "Audio pipeline model files are missing or ComfyUI root is not configured."
    return {
        "ok": ok,
        "message": message,
        "comfyui_root": str(comfyui_root) if comfyui_root else "",
        "checked_pipelines": checked_pipelines,
        "ok_by_pipeline": ok_by_pipeline,
        "expected_files": expected,
        "missing_files": missing_files,
        "missing_files_by_pipeline": missing_files_by_pipeline,
        "backend_model_presence": backend_model_presence,
        "missing_in_backend": missing_in_backend,
        "missing_in_backend_by_pipeline": missing_in_backend_by_pipeline,
        "download_sources": [
            {
                "pipeline": str(data["pipeline"]),
                "folder": str(data["folder"]),
                "name": str(data["name"]),
                "url": str(data["url"]),
            }
            for data in _expected_model_specs(checked_pipelines)
        ],
    }


def doctor(
    *,
    base_url: str = DEFAULT_BASE_URL,
    comfyui_root: Path | None = None,
    timeout: int = 20,
    pipelines: list[str] | None = None,
) -> dict[str, Any]:
    checked_pipelines = _normalize_pipelines(pipelines)
    backend = check_backend(base_url=base_url, timeout=timeout, pipelines=checked_pipelines)
    models = check_models(base_url=base_url, comfyui_root=comfyui_root, timeout=timeout, pipelines=checked_pipelines)
    next_steps: list[str] = []
    if not backend.ok:
        if backend.available_nodes:
            next_steps.append("ComfyUI is reachable, but one or more requested audio pipeline node groups are missing or disabled.")
        else:
            next_steps.append(f"Start Stability Matrix ComfyUI and expose its API at {base_url.rstrip('/')}.")
    if not models.get("ok"):
        if not models.get("comfyui_root"):
            next_steps.append("Set COMFYUI_ROOT or pass --comfyui-root to the Stability Matrix ComfyUI package directory.")
        for item in models.get("missing_files", []):
            if isinstance(item, dict):
                next_steps.append(f"Install {item.get('name')} into models/{item.get('folder')}.")
    ready = backend.ok and bool(models.get("ok"))
    return {
        "ok": ready,
        "base_url": base_url.rstrip("/"),
        "checked_pipelines": checked_pipelines,
        "backend": backend.to_dict(),
        "models": models,
        "next_steps": next_steps,
    }


def download_models(
    *,
    comfyui_root: Path,
    overwrite: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    pipelines: list[str] | None = None,
) -> dict[str, Any]:
    comfyui_root = comfyui_root.expanduser().resolve()
    checked_pipelines = _normalize_pipelines(pipelines)
    downloads = []
    for item in _expected_model_paths(comfyui_root, pipelines=checked_pipelines):
        target = Path(item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            downloads.append({**item, "status": "skipped_exists"})
            continue
        partial = target.with_suffix(target.suffix + ".partial")
        try:
            _download_file(str(item["url"]), partial, timeout=timeout)
            partial.replace(target)
            downloads.append({**item, "status": "downloaded", "bytes": target.stat().st_size})
        except Exception as exc:
            if partial.exists():
                partial.unlink()
            downloads.append({**item, "status": "failed", "error": str(exc)})
    ok = all(item.get("status") in {"downloaded", "skipped_exists"} for item in downloads)
    return {
        "ok": ok,
        "comfyui_root": str(comfyui_root),
        "checked_pipelines": checked_pipelines,
        "downloads": downloads,
    }


def generate_audio_pack(
    *,
    spec_path: Path,
    output_dir: Path,
    manifest_path: Path,
    style_profile_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    comfyui_root: Path | None = None,
    project_root: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    project_root = (project_root or Path.cwd()).resolve()
    spec_path = _resolve_existing(spec_path, project_root=project_root)
    output_dir = _resolve_output(output_dir, project_root=project_root)
    manifest_path = _resolve_output(manifest_path, project_root=project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    spec = _read_json(spec_path)
    assets = spec.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AudioPipelineError("audio request must contain a non-empty assets array")
    needed_pipelines = sorted({_pipeline_for_asset(asset) for asset in assets if isinstance(asset, dict)})
    if style_profile_path is not None:
        style_profile_path = _resolve_existing(style_profile_path, project_root=project_root)
    style_profile = _load_style_profile(style_profile_path, spec=spec, project_root=project_root)
    backend = check_backend(base_url=base_url, timeout=min(timeout, 30), pipelines=needed_pipelines)
    if not backend.ok:
        raise AudioPipelineError(backend.message)
    models = check_models(base_url=base_url, comfyui_root=comfyui_root, timeout=min(timeout, 30), pipelines=needed_pipelines)
    if not models.get("ok"):
        raise AudioPipelineError(models.get("message", "Audio pipeline models are not ready"))

    generation_defaults = style_profile.get("generation", {}) if isinstance(style_profile.get("generation"), dict) else {}
    final_rules = style_profile.get("final_asset_rules", {}) if isinstance(style_profile.get("final_asset_rules"), dict) else {}
    style_hash = _stable_json_hash(style_profile)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project": spec.get("project", ""),
        "style_profile_id": spec.get("style_profile_id") or style_profile.get("style_profile_id", ""),
        "style_hash": style_hash,
        "backend": "comfyui",
        "base_url": base_url.rstrip("/"),
        "generated_at": int(time.time()),
        "spec_path": _as_project_path(spec_path, project_root),
        "style_profile_path": _as_project_path(style_profile_path, project_root) if style_profile_path else "",
        "output_dir": _as_project_path(output_dir, project_root),
        "backend_status": backend.to_dict(),
        "model_status": models,
        "assets": [],
    }

    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            raise AudioPipelineError(f"asset #{index} is not an object")
        asset_id = _safe_id(str(asset.get("id") or f"audio_{index}"))
        pipeline = _pipeline_for_asset(asset)
        generation = _generation_for_asset(asset, generation_defaults, pipeline=pipeline)
        tags = _compile_tags(asset=asset, spec=spec, style_profile=style_profile)
        lyrics = str(asset.get("lyrics") or "")
        seed = int(asset.get("seed") if asset.get("seed") is not None else int(time.time()) + index)
        requested_format = str(asset.get("format") or generation.get("output_format") or final_rules.get("default_format") or "wav").lower()
        prompt = _build_prompt_for_pipeline(
            pipeline=pipeline,
            asset=asset,
            asset_id=asset_id,
            tags=tags,
            lyrics=lyrics,
            seed=seed,
            generation=generation,
            style_profile=style_profile,
        )
        prompt_id = _submit_prompt(base_url=base_url, prompt=prompt, timeout=timeout)
        history = _wait_for_history(base_url=base_url, prompt_id=prompt_id, timeout=timeout)
        output_refs = _extract_audio_outputs(history, prompt_id=prompt_id)
        if not output_refs:
            raise AudioPipelineError(f"ComfyUI completed prompt {prompt_id}, but no audio output reference was found")
        raw_bytes = _download_output(base_url=base_url, output_ref=output_refs[0], timeout=timeout)
        raw_ext = "." + str(output_refs[0].get("filename", "audio.flac")).rsplit(".", 1)[-1].lower()
        raw_path = output_dir / f"{asset_id}.source{raw_ext}"
        final_path = output_dir / f"{asset_id}.{requested_format}"
        raw_path.write_bytes(raw_bytes)
        if requested_format == raw_ext.lstrip("."):
            shutil.copyfile(raw_path, final_path)
        elif requested_format == "wav":
            _convert_to_wav(raw_path, final_path)
        else:
            raise AudioPipelineError(f"unsupported requested output format: {requested_format}")
        entry = {
            "id": asset_id,
            "pipeline": pipeline,
            "type": asset.get("type", ""),
            "usage": asset.get("usage", ""),
            "description": asset.get("description", ""),
            "tags": tags,
            "lyrics": lyrics,
            "negative_tags": style_profile.get("negative_tags", ""),
            "seed": seed,
            "loop": bool(asset.get("loop", False)),
            "target_duration_seconds": float(asset.get("target_duration_seconds") or asset.get("duration_seconds") or generation.get("duration_seconds") or 0),
            "format": requested_format,
            "generation": generation,
            "prompt_id": prompt_id,
            "raw_path": _as_project_path(raw_path, project_root),
            "path": _as_project_path(final_path, project_root),
            "style_profile_id": manifest["style_profile_id"],
            "style_hash": style_hash,
        }
        entry["validation"] = _validate_audio_file(final_path, entry)
        manifest["assets"].append(entry)

    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    manifest["validation"] = validation
    manifest["listening_review"] = {
        "status": "pending",
        "reviewer": "",
        "notes": "Listening review is required before integrating generated audio.",
        "reviewed_at": None,
    }
    manifest["ready_for_integration"] = False
    report_path = _validation_report_path(manifest_path)
    manifest["manifest_path"] = _as_project_path(manifest_path, project_root)
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def validate_audio_pack(*, manifest_path: Path, project_root: Path | None = None) -> dict[str, Any]:
    project_root = (project_root or Path.cwd()).resolve()
    manifest_path = _resolve_existing(manifest_path, project_root=project_root)
    manifest = _read_json(manifest_path)
    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    listening_review = _current_listening_review(manifest)
    ready_for_integration = validation.get("status") == "passed" and listening_review.get("status") == "approved"
    report_path = _validation_report_path(manifest_path)
    manifest["validation"] = validation
    manifest["listening_review"] = listening_review
    manifest["ready_for_integration"] = ready_for_integration
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest_path": _as_project_path(manifest_path, project_root),
        "validation_report_path": _as_project_path(report_path, project_root),
        "validation": validation,
        "listening_review": listening_review,
        "ready_for_integration": ready_for_integration,
    }


def record_listening_review(
    *,
    manifest_path: Path,
    decision: str,
    reviewer: str,
    notes: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    project_root = (project_root or Path.cwd()).resolve()
    manifest_path = _resolve_existing(manifest_path, project_root=project_root)
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"approved", "rejected"}:
        raise AudioPipelineError("listening review decision must be `approved` or `rejected`")
    manifest = _read_json(manifest_path)
    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    listening_review = {
        "status": normalized_decision,
        "reviewer": reviewer.strip() or "unspecified-reviewer",
        "notes": notes.strip(),
        "reviewed_at": int(time.time()),
    }
    ready_for_integration = validation.get("status") == "passed" and normalized_decision == "approved"
    report_path = _validation_report_path(manifest_path)
    manifest["validation"] = validation
    manifest["listening_review"] = listening_review
    manifest["ready_for_integration"] = ready_for_integration
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest_path": _as_project_path(manifest_path, project_root),
        "validation_report_path": _as_project_path(report_path, project_root),
        "validation": validation,
        "listening_review": listening_review,
        "ready_for_integration": ready_for_integration,
    }


def validate_manifest_data(*, manifest: dict[str, Any], project_root: Path) -> dict[str, Any]:
    results = []
    all_ok = True
    for asset in manifest.get("assets", []):
        result = _validate_audio_file(_resolve_existing(Path(asset.get("path", "")), project_root=project_root), asset)
        results.append({"id": asset.get("id", ""), **result})
        if result["status"] != "passed":
            all_ok = False
    return {
        "status": "passed" if all_ok and results else "failed",
        "asset_count": len(results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate game audio assets through Stability Matrix ComfyUI.")
    parser.add_argument("--project-root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    parser.add_argument("--base-url", default=os.environ.get("COMFYUI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--comfyui-root", default=os.environ.get("COMFYUI_ROOT", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    check_backend_parser = sub.add_parser("check-backend")
    check_backend_parser.add_argument("--pipelines", nargs="*")
    check_models_parser = sub.add_parser("check-models")
    check_models_parser.add_argument("--pipelines", nargs="*")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--pipelines", nargs="*")

    download = sub.add_parser("download-models")
    download.add_argument("--overwrite", action="store_true")
    download.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    download.add_argument("--pipelines", nargs="*")

    gen = sub.add_parser("generate")
    gen.add_argument("--spec", required=True)
    gen.add_argument("--out", required=True)
    gen.add_argument("--manifest", required=True)
    gen.add_argument("--style-profile")
    gen.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)

    val = sub.add_parser("validate")
    val.add_argument("--manifest", required=True)

    review = sub.add_parser("review")
    review.add_argument("--manifest", required=True)
    review.add_argument("--decision", required=True, choices=["approved", "rejected"])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes", default="")

    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve()
    comfyui_root = Path(args.comfyui_root).expanduser().resolve() if args.comfyui_root else None
    try:
        if args.command == "check-backend":
            print(json.dumps(check_backend(base_url=args.base_url, pipelines=args.pipelines).to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "check-models":
            print(json.dumps(check_models(base_url=args.base_url, comfyui_root=comfyui_root, pipelines=args.pipelines), ensure_ascii=False, indent=2))
            return 0
        if args.command == "doctor":
            print(json.dumps(doctor(base_url=args.base_url, comfyui_root=comfyui_root, pipelines=args.pipelines), ensure_ascii=False, indent=2))
            return 0
        if args.command == "download-models":
            if comfyui_root is None:
                raise AudioPipelineError("download-models requires --comfyui-root or COMFYUI_ROOT")
            result = download_models(comfyui_root=comfyui_root, overwrite=args.overwrite, timeout=args.timeout, pipelines=args.pipelines)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        if args.command == "generate":
            result = generate_audio_pack(
                spec_path=Path(args.spec),
                output_dir=Path(args.out),
                manifest_path=Path(args.manifest),
                style_profile_path=Path(args.style_profile) if args.style_profile else None,
                base_url=args.base_url,
                comfyui_root=comfyui_root,
                project_root=project_root,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate":
            result = validate_audio_pack(manifest_path=Path(args.manifest), project_root=project_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            result = record_listening_review(
                manifest_path=Path(args.manifest),
                decision=args.decision,
                reviewer=args.reviewer,
                notes=args.notes,
                project_root=project_root,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 2


def _load_style_profile(style_profile_path: Path | None, *, spec: dict[str, Any], project_root: Path) -> dict[str, Any]:
    if style_profile_path is not None:
        path = style_profile_path if style_profile_path.is_absolute() else project_root / style_profile_path
        return _read_json(path.resolve())
    inline = spec.get("style")
    if isinstance(inline, dict) and inline:
        return inline
    plugin_root = Path(__file__).resolve().parents[1]
    return _read_json(plugin_root / "templates" / "style_profile.ace-step-game-audio.json")


def _generation_for_asset(asset: dict[str, Any], defaults: dict[str, Any], *, pipeline: str) -> dict[str, Any]:
    generation = dict(defaults) if pipeline == PIPELINE_ACE_STEP else {}
    if pipeline != PIPELINE_ACE_STEP and defaults.get("output_format") is not None:
        generation["output_format"] = defaults["output_format"]
    keys_by_pipeline = {
        PIPELINE_ACE_STEP: [
        "duration_seconds",
        "bpm",
        "timesignature",
        "language",
        "keyscale",
        "generate_audio_codes",
        "cfg_scale",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "steps",
        "sampler_name",
        "scheduler",
        "denoise",
        "output_format",
        ],
        PIPELINE_QWEN_TTS: [
        "language",
        "output_format",
        "speaker",
        "model_size",
        "device",
        "precision",
        "attention",
        "instruct",
        "voice_instruct",
        "max_new_tokens",
        "do_sample",
        "top_p",
        "top_k",
        "temperature",
        "repetition_penalty",
        "unload_models",
        ],
        PIPELINE_MMAUDIO: [
        "duration_seconds",
        "output_format",
        "cfg",
        "negative_prompt",
        "precision",
        "base_precision",
        "mmaudio_model",
        "vae_model",
        "synchformer_model",
        "clip_model",
        "mode",
        "mask_away_clip",
        "force_offload",
        "steps",
        ],
    }
    generation["pipeline"] = pipeline
    for key in keys_by_pipeline[pipeline]:
        if asset.get(key) is not None:
            generation[key] = asset[key]
    return generation


def _pipeline_for_asset(asset: dict[str, Any]) -> str:
    explicit = str(asset.get("pipeline") or asset.get("route") or asset.get("engine") or asset.get("generator") or "").strip()
    if explicit:
        return _normalize_pipeline_name(explicit)
    asset_type = str(asset.get("type") or "").lower().replace("-", "_").replace(" ", "_")
    usage = str(asset.get("usage") or "").lower().replace("-", "_").replace(" ", "_")
    text = f"{asset_type} {usage}"
    if any(word in text for word in ["voice_line", "dialogue", "dialog", "narration", "narrator", "speech", "human_voice", "tts"]):
        return PIPELINE_QWEN_TTS
    if any(word in text for word in ["sfx", "sound_effect", "effect", "foley", "monster", "creature", "ambience", "ambient", "environment", "ui_click", "ui_sound"]):
        return PIPELINE_MMAUDIO
    return PIPELINE_ACE_STEP


def _normalize_pipeline_name(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ace": PIPELINE_ACE_STEP,
        "ace_step": PIPELINE_ACE_STEP,
        "acestep": PIPELINE_ACE_STEP,
        "music": PIPELINE_ACE_STEP,
        "bgm": PIPELINE_ACE_STEP,
        "qwen": PIPELINE_QWEN_TTS,
        "qwen_tts": PIPELINE_QWEN_TTS,
        "qwentts": PIPELINE_QWEN_TTS,
        "tts": PIPELINE_QWEN_TTS,
        "voice": PIPELINE_QWEN_TTS,
        "speech": PIPELINE_QWEN_TTS,
        "mmaudio": PIPELINE_MMAUDIO,
        "mm_audio": PIPELINE_MMAUDIO,
        "sfx": PIPELINE_MMAUDIO,
        "sound_effect": PIPELINE_MMAUDIO,
        "monster": PIPELINE_MMAUDIO,
        "ambience": PIPELINE_MMAUDIO,
        "ambient": PIPELINE_MMAUDIO,
    }
    if normalized not in aliases:
        raise AudioPipelineError(f"unsupported audio pipeline: {value}")
    return aliases[normalized]


def _build_prompt_for_pipeline(
    *,
    pipeline: str,
    asset: dict[str, Any],
    asset_id: str,
    tags: str,
    lyrics: str,
    seed: int,
    generation: dict[str, Any],
    style_profile: dict[str, Any],
) -> dict[str, Any]:
    if pipeline == PIPELINE_ACE_STEP:
        return _build_ace_prompt(asset_id=asset_id, tags=tags, lyrics=lyrics, seed=seed, generation=generation, style_profile=style_profile)
    if pipeline == PIPELINE_QWEN_TTS:
        return _build_qwen_tts_prompt(asset=asset, asset_id=asset_id, seed=seed, generation=generation, style_profile=style_profile)
    if pipeline == PIPELINE_MMAUDIO:
        return _build_mmaudio_prompt(asset=asset, asset_id=asset_id, tags=tags, seed=seed, generation=generation, style_profile=style_profile)
    raise AudioPipelineError(f"unsupported audio pipeline: {pipeline}")


def _compile_tags(*, asset: dict[str, Any], spec: dict[str, Any], style_profile: dict[str, Any]) -> str:
    parts = [
        str(style_profile.get("base_tags_prefix") or "").strip(),
        str(asset.get("tags") or asset.get("description") or "").strip(),
    ]
    must_have = asset.get("must_have")
    if isinstance(must_have, list) and must_have:
        parts.append("must include: " + ", ".join(str(item) for item in must_have))
    avoid = asset.get("avoid")
    if isinstance(avoid, list) and avoid:
        parts.append("avoid: " + ", ".join(str(item) for item in avoid))
    project_style = spec.get("style")
    if isinstance(project_style, dict) and project_style.get("extra_tags"):
        parts.append(str(project_style["extra_tags"]))
    return ", ".join(part for part in parts if part)


def _build_ace_prompt(*, asset_id: str, tags: str, lyrics: str, seed: int, generation: dict[str, Any], style_profile: dict[str, Any]) -> dict[str, Any]:
    model_files = style_profile.get("model_files", {}) if isinstance(style_profile.get("model_files"), dict) else {}
    duration = float(generation.get("duration_seconds") or 6.0)
    bpm = int(generation.get("bpm") or 110)
    timesignature = str(generation.get("timesignature") or "4")
    language = str(generation.get("language") or "en")
    keyscale = str(generation.get("keyscale") or "E minor")
    cfg_scale = float(generation.get("cfg_scale") or 2.0)
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": str(model_files.get("unet_name") or "acestep_v1.5_turbo.safetensors"),
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": str(model_files.get("clip_name1") or "qwen_0.6b_ace15.safetensors"),
                "clip_name2": str(model_files.get("clip_name2") or "qwen_4b_ace15.safetensors"),
                "type": "ace",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": str(model_files.get("vae_name") or "ace_1.5_vae.safetensors")},
        },
        "4": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["1", 0], "shift": 3.0},
        },
        "5": {
            "class_type": "EmptyAceStep1.5LatentAudio",
            "inputs": {"seconds": duration, "batch_size": 1},
        },
        "6": {
            "class_type": "TextEncodeAceStepAudio1.5",
            "inputs": {
                "clip": ["2", 0],
                "tags": tags,
                "lyrics": lyrics,
                "seed": seed,
                "bpm": bpm,
                "duration": duration,
                "timesignature": timesignature,
                "language": language,
                "keyscale": keyscale,
                "generate_audio_codes": bool(generation.get("generate_audio_codes", True)),
                "cfg_scale": cfg_scale,
                "temperature": float(generation.get("temperature") or 0.85),
                "top_p": float(generation.get("top_p") or 0.9),
                "top_k": int(generation.get("top_k") or 0),
                "min_p": float(generation.get("min_p") or 0.0),
            },
        },
        "7": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["6", 0]},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": int(generation.get("steps") or 8),
                "cfg": 1.0,
                "sampler_name": str(generation.get("sampler_name") or "euler"),
                "scheduler": str(generation.get("scheduler") or "simple"),
                "denoise": float(generation.get("denoise") or 1.0),
            },
        },
        "9": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
        },
        "10": {
            "class_type": "SaveAudio",
            "inputs": {"audio": ["9", 0], "filename_prefix": f"audio_pipeline/{asset_id}"},
        },
    }


def _build_qwen_tts_prompt(*, asset: dict[str, Any], asset_id: str, seed: int, generation: dict[str, Any], style_profile: dict[str, Any]) -> dict[str, Any]:
    text = _asset_text(asset)
    if not text:
        raise AudioPipelineError(f"QwenTTS asset {asset_id} requires text, script, line, dialogue, or description")
    voice = asset.get("voice")
    voice_defaults = style_profile.get("voice") if isinstance(style_profile.get("voice"), dict) else {}
    if not isinstance(voice_defaults, dict):
        voice_defaults = {}
    if isinstance(voice, dict):
        voice_data = {**voice_defaults, **voice}
    else:
        voice_data = dict(voice_defaults)
        if voice:
            voice_data["description"] = str(voice)
    instruct = (
        str(generation.get("instruct") or generation.get("voice_instruct") or asset.get("instruct") or asset.get("voice_instruct") or voice_data.get("instruct") or voice_data.get("description") or "").strip()
    )
    if not instruct:
        instruct = "Clear game voice line, natural pacing, no background music."
    return {
        "1": {
            "class_type": "AILab_Qwen3TTSCustomVoice_Advanced",
            "inputs": {
                "text": text,
                "speaker": str(asset.get("speaker") or generation.get("speaker") or voice_data.get("speaker") or "Ryan"),
                "model_size": str(asset.get("model_size") or generation.get("model_size") or voice_data.get("model_size") or "0.6B"),
                "device": str(generation.get("device") or "auto"),
                "precision": str(generation.get("precision") or "bf16"),
                "language": _qwen_language(str(asset.get("language") or generation.get("language") or voice_data.get("language") or "Auto")),
                "instruct": instruct,
                "max_new_tokens": max(256, int(generation.get("max_new_tokens") or asset.get("max_new_tokens") or 512)),
                "do_sample": _as_bool(generation.get("do_sample"), False),
                "top_p": float(generation.get("top_p") or 0.9),
                "top_k": int(generation.get("top_k") or 50),
                "temperature": float(generation.get("temperature") or 0.9),
                "repetition_penalty": float(generation.get("repetition_penalty") or 1.0),
                "attention": str(generation.get("attention") or "auto"),
                "unload_models": _as_bool(generation.get("unload_models"), True),
                "seed": seed,
            },
        },
        "2": {
            "class_type": "SaveAudio",
            "inputs": {"audio": ["1", 0], "filename_prefix": f"audio_pipeline/{asset_id}"},
        },
    }


def _build_mmaudio_prompt(*, asset: dict[str, Any], asset_id: str, tags: str, seed: int, generation: dict[str, Any], style_profile: dict[str, Any]) -> dict[str, Any]:
    model_files = style_profile.get("model_files", {}) if isinstance(style_profile.get("model_files"), dict) else {}
    prompt = str(asset.get("prompt") or tags or asset.get("description") or "").strip()
    if not prompt:
        raise AudioPipelineError(f"MMAudio asset {asset_id} requires prompt, tags, or description")
    if "game" not in prompt.lower():
        prompt = f"{prompt}, game audio asset"
    negative_prompt = _negative_prompt(asset=asset, generation=generation, style_profile=style_profile)
    return {
        "1": {
            "class_type": "MMAudioModelLoader",
            "inputs": {
                "mmaudio_model": str(generation.get("mmaudio_model") or model_files.get("mmaudio_model") or "mmaudio_large_44k_v2_fp16.safetensors"),
                "base_precision": str(generation.get("base_precision") or "fp16"),
            },
        },
        "2": {
            "class_type": "MMAudioFeatureUtilsLoader",
            "inputs": {
                "vae_model": str(generation.get("vae_model") or model_files.get("mmaudio_vae_model") or "mmaudio_vae_44k_fp16.safetensors"),
                "synchformer_model": str(generation.get("synchformer_model") or model_files.get("mmaudio_synchformer_model") or "mmaudio_synchformer_fp16.safetensors"),
                "clip_model": str(generation.get("clip_model") or model_files.get("mmaudio_clip_model") or "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors"),
                "mode": str(generation.get("mode") or "44k"),
                "precision": str(generation.get("precision") or "fp16"),
            },
        },
        "3": {
            "class_type": "MMAudioSampler",
            "inputs": {
                "mmaudio_model": ["1", 0],
                "feature_utils": ["2", 0],
                "duration": float(generation.get("duration_seconds") or asset.get("duration_seconds") or 2.0),
                "steps": int(generation.get("steps") or 8),
                "cfg": float(generation.get("cfg") or generation.get("cfg_scale") or 4.5),
                "seed": seed,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "mask_away_clip": _as_bool(generation.get("mask_away_clip"), False),
                "force_offload": _as_bool(generation.get("force_offload"), True),
            },
        },
        "4": {
            "class_type": "SaveAudio",
            "inputs": {"audio": ["3", 0], "filename_prefix": f"audio_pipeline/{asset_id}"},
        },
    }


def _asset_text(asset: dict[str, Any]) -> str:
    for key in ["text", "script", "line", "dialogue", "dialog", "content"]:
        value = asset.get(key)
        if value:
            return str(value).strip()
    return str(asset.get("description") or "").strip()


def _qwen_language(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "": "Auto",
        "auto": "Auto",
        "zh": "Chinese",
        "zh_cn": "Chinese",
        "cn": "Chinese",
        "chinese": "Chinese",
        "中文": "Chinese",
        "en": "English",
        "english": "English",
        "ja": "Japanese",
        "jp": "Japanese",
        "japanese": "Japanese",
        "ko": "Korean",
        "kr": "Korean",
        "korean": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "pt": "Portuguese",
        "ru": "Russian",
        "it": "Italian",
    }
    return mapping.get(normalized, value if value in {"Auto", "Chinese", "English", "Japanese", "Korean", "French", "German", "Spanish", "Portuguese", "Russian", "Italian"} else "Auto")


def _negative_prompt(*, asset: dict[str, Any], generation: dict[str, Any], style_profile: dict[str, Any]) -> str:
    parts = [
        str(generation.get("negative_prompt") or "").strip(),
        str(asset.get("negative_prompt") or "").strip(),
        str(style_profile.get("negative_tags") or "").strip(),
    ]
    avoid = asset.get("avoid")
    if isinstance(avoid, list) and avoid:
        parts.append(", ".join(str(item) for item in avoid))
    if _pipeline_for_asset(asset) == PIPELINE_MMAUDIO:
        parts.append("music, melody, vocals, human speech, clipping, distortion, watermark")
    return ", ".join(part for part in parts if part)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _submit_prompt(*, base_url: str, prompt: dict[str, Any], timeout: int) -> str:
    payload = {"prompt": prompt, "client_id": f"audio-pipeline-{uuid.uuid4()}"}
    data = _post_json(f"{base_url.rstrip('/')}/prompt", payload, timeout=timeout)
    prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
    if not prompt_id:
        raise AudioPipelineError(f"ComfyUI did not return prompt_id: {data}")
    return str(prompt_id)


def _wait_for_history(*, base_url: str, prompt_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            data = _get_json(f"{base_url.rstrip('/')}/history/{urllib.parse.quote(prompt_id)}", timeout=20)
            if isinstance(data, dict) and data:
                entry = data.get(prompt_id)
                if isinstance(entry, dict):
                    status = entry.get("status")
                    if isinstance(status, dict) and status.get("status_str") == "error":
                        raise AudioPipelineError(f"ComfyUI prompt failed: {status}")
                    if entry.get("outputs"):
                        return data
        except AudioPipelineError:
            raise
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise AudioPipelineError(f"Timed out waiting for ComfyUI prompt {prompt_id}. Last error: {last_error}")


def _extract_audio_outputs(history: dict[str, Any], *, prompt_id: str) -> list[dict[str, Any]]:
    entry = history.get(prompt_id) if isinstance(history, dict) else None
    outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
    found: list[dict[str, Any]] = []
    for node_output in outputs.values() if isinstance(outputs, dict) else []:
        if not isinstance(node_output, dict):
            continue
        for key in ["audio", "audios"]:
            values = node_output.get(key)
            if isinstance(values, dict):
                values = [values]
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and item.get("filename"):
                        found.append(item)
    return found


def _download_output(*, base_url: str, output_ref: dict[str, Any], timeout: int) -> bytes:
    query = urllib.parse.urlencode(
        {
            "filename": str(output_ref.get("filename") or ""),
            "subfolder": str(output_ref.get("subfolder") or ""),
            "type": str(output_ref.get("type") or "output"),
        }
    )
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/view?{query}", timeout=timeout) as response:
        return response.read()


def _convert_to_wav(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioPipelineError("ffmpeg is required to convert generated ComfyUI audio to wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-acodec", "pcm_s16le", "-ar", "48000", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AudioPipelineError(f"ffmpeg conversion failed: {completed.stderr[-2000:]}")


def _validate_audio_file(path: Path, asset: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {"status": "failed", "errors": [f"file does not exist: {path}"], "warnings": warnings}
    if path.stat().st_size <= 0:
        errors.append("audio file is empty")
    metrics: dict[str, Any] = {"path": str(path), "bytes": path.stat().st_size, "format": path.suffix.lower().lstrip(".")}
    if path.suffix.lower() == ".wav":
        wav_metrics = _wav_metrics(path)
        metrics.update(wav_metrics)
        duration = float(wav_metrics.get("duration_seconds") or 0)
        if duration <= 0:
            errors.append("wav duration is zero")
        target_duration = float(asset.get("target_duration_seconds") or 0)
        if target_duration > 0 and abs(duration - target_duration) > max(0.75, target_duration * 0.35):
            warnings.append(f"duration {duration:.2f}s differs from target {target_duration:.2f}s")
        if float(wav_metrics.get("rms", 0)) <= 1:
            errors.append("audio appears silent or nearly silent")
    else:
        warnings.append("non-wav validation is limited to existence and byte size")
    return {"status": "passed" if not errors else "failed", "errors": errors, "warnings": warnings, **metrics}


def _wav_metrics(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        frame_count = handle.getnframes()
        duration = frame_count / sample_rate if sample_rate else 0
        frames = handle.readframes(min(frame_count, sample_rate * 15))
    rms = 0.0
    peak = 0
    if frames and sample_width in {1, 2, 4}:
        import audioop

        rms = float(audioop.rms(frames, sample_width))
        peak = int(audioop.max(frames, sample_width))
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "frame_count": frame_count,
        "duration_seconds": duration,
        "rms": rms,
        "peak": peak,
    }


def _current_listening_review(manifest: dict[str, Any]) -> dict[str, Any]:
    review = manifest.get("listening_review")
    if isinstance(review, dict):
        return review
    return {
        "status": "pending",
        "reviewer": "",
        "notes": "Listening review is required before integrating generated audio.",
        "reviewed_at": None,
    }


def _normalize_pipelines(pipelines: list[str] | None) -> list[str]:
    if not pipelines:
        return list(SUPPORTED_PIPELINES)
    normalized = []
    for item in pipelines:
        pipeline = _normalize_pipeline_name(str(item))
        if pipeline not in normalized:
            normalized.append(pipeline)
    return normalized


def _required_nodes_for_pipelines(pipelines: list[str]) -> list[str]:
    return sorted({node for pipeline in pipelines for node in REQUIRED_NODES_BY_PIPELINE.get(pipeline, [])})


def _missing_nodes_by_pipeline(node_names: list[str], pipelines: list[str]) -> dict[str, list[str]]:
    available = set(node_names)
    return {
        pipeline: [node for node in REQUIRED_NODES_BY_PIPELINE.get(pipeline, []) if node not in available]
        for pipeline in pipelines
    }


def _expected_model_paths(comfyui_root: Path | None, pipelines: list[str] | None = None) -> list[dict[str, Any]]:
    if comfyui_root is None:
        return []
    result = []
    for data in _expected_model_specs(_normalize_pipelines(pipelines)):
        folder = str(data["folder"])
        result.append(
            {
                "pipeline": str(data["pipeline"]),
                "folder": folder,
                "name": str(data["name"]),
                "path": str(comfyui_root / "models" / folder / str(data["name"])),
                "url": str(data["url"]),
                "backend_visible": bool(data.get("backend_visible", True)),
            }
        )
    return result


def _model_folder(key: str, data: dict[str, Any]) -> str:
    return str(data.get("folder") or key)


def _expected_model_specs(pipelines: list[str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if PIPELINE_ACE_STEP in pipelines:
        for key, data in ACE_MODEL_FILES.items():
            specs.append(
                {
                    "pipeline": PIPELINE_ACE_STEP,
                    "folder": _model_folder(key, data),
                    "name": str(data["name"]),
                    "url": str(data["url"]),
                    "backend_visible": True,
                }
            )
    if PIPELINE_QWEN_TTS in pipelines:
        for data in QWEN_TTS_MODEL_FILES:
            specs.append({"pipeline": PIPELINE_QWEN_TTS, **data})
    if PIPELINE_MMAUDIO in pipelines:
        for data in MMAUDIO_MODEL_FILES:
            specs.append({"pipeline": PIPELINE_MMAUDIO, "backend_visible": True, **data})
    return specs


def _model_names_visible_in_backend(*, base_url: str, timeout: int) -> dict[str, Any]:
    try:
        object_info = _get_json(f"{base_url.rstrip('/')}/object_info", timeout=timeout)
    except Exception as exc:
        return {"checked": False, "message": str(exc), "visible_model_names": []}
    names: set[str] = set()
    _collect_strings(object_info, names)
    return {"checked": True, "message": "object_info inspected", "visible_model_names": sorted(names)}


def _collect_strings(value: Any, names: set[str]) -> None:
    if isinstance(value, str):
        if value.endswith(".safetensors") or value.endswith(".ckpt") or value.endswith(".pt"):
            names.add(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_strings(item, names)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, names)


def _resolve_comfyui_root(path: Path | None) -> Path | None:
    if path is not None:
        return path.expanduser().resolve()
    value = os.environ.get("COMFYUI_ROOT")
    if value:
        return Path(value).expanduser().resolve()
    return None


def _get_json(url: str, *, timeout: int) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, target: Path, *, timeout: int) -> None:
    url = _apply_hf_endpoint(url)
    request = urllib.request.Request(url, headers={"User-Agent": "codex-skill-runtime-audio-pipeline/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with target.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def _apply_hf_endpoint(url: str) -> str:
    endpoint = os.environ.get("HF_ENDPOINT") or os.environ.get("HUGGINGFACE_ENDPOINT") or ""
    endpoint = endpoint.rstrip("/")
    if endpoint and url.startswith("https://huggingface.co/"):
        return endpoint + "/" + url.removeprefix("https://huggingface.co/")
    return url


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _stable_json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + __import__("hashlib").sha256(raw).hexdigest()


def _safe_id(value: str) -> str:
    keep = []
    for char in value.lower().strip():
        if char.isalnum() or char in {"-", "_"}:
            keep.append(char)
        elif char.isspace():
            keep.append("_")
    cleaned = "".join(keep).strip("_")
    return cleaned or "audio_asset"


def _resolve_existing(path: Path, *, project_root: Path) -> Path:
    candidate = path if path.is_absolute() else project_root / path
    candidate = candidate.resolve()
    if not candidate.exists():
        raise AudioPipelineError(f"path does not exist: {candidate}")
    return candidate


def _resolve_output(path: Path, *, project_root: Path) -> Path:
    candidate = path if path.is_absolute() else project_root / path
    candidate = candidate.resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise AudioPipelineError(f"output path must stay inside project root: {candidate}") from exc
    return candidate


def _as_project_path(path: Path | None, project_root: Path) -> str:
    if path is None:
        return ""
    path = path.resolve()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _validation_report_path(manifest_path: Path) -> Path:
    if manifest_path.name == "audio_manifest.json":
        return manifest_path.with_name("audio_validation_report.json")
    return manifest_path.with_name(f"{manifest_path.stem}.validation_report.json")


if __name__ == "__main__":
    raise SystemExit(main())
