from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


DEFAULT_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_TIMEOUT = 300


class AssetPipelineError(RuntimeError):
    pass


@dataclass
class ForgeStatus:
    ok: bool
    base_url: str
    models: list[dict[str, Any]]
    current_model: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "base_url": self.base_url,
            "model_count": len(self.models),
            "models": self.models,
            "current_model": self.current_model,
            "message": self.message,
        }


def check_backend(base_url: str = DEFAULT_BASE_URL, timeout: int = 20) -> ForgeStatus:
    base_url = base_url.rstrip("/")
    try:
        models_payload = _get_json(f"{base_url}/sdapi/v1/sd-models", timeout=timeout)
        options = _get_json(f"{base_url}/sdapi/v1/options", timeout=timeout)
    except urllib.error.HTTPError as exc:
        return ForgeStatus(False, base_url, [], "", f"Forge responded HTTP {exc.code}; ensure Stability Matrix starts Forge with --api.")
    except urllib.error.URLError as exc:
        return ForgeStatus(False, base_url, [], "", f"Cannot reach Forge at {base_url}: {exc.reason}")
    except Exception as exc:
        return ForgeStatus(False, base_url, [], "", f"Cannot inspect Forge backend: {exc}")

    models = _unwrap_powershell_value(models_payload)
    if not isinstance(models, list):
        models = []
    current_model = ""
    if isinstance(options, dict):
        current_model = str(options.get("sd_model_checkpoint") or "")
    if not models:
        return ForgeStatus(False, base_url, [], current_model, "Forge API is reachable, but no checkpoint models are loaded.")
    if not current_model:
        return ForgeStatus(False, base_url, models, current_model, "Forge API is reachable, but sd_model_checkpoint is empty.")
    return ForgeStatus(True, base_url, models, current_model, "Forge API is ready.")


def generate_asset_pack(
    *,
    spec_path: Path,
    output_dir: Path,
    manifest_path: Path,
    style_profile_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
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
    if style_profile_path is not None:
        style_profile_path = _resolve_existing(style_profile_path, project_root=project_root)
    style_profile = _load_style_profile(style_profile_path, spec=spec, project_root=project_root)
    status = check_backend(base_url=base_url, timeout=min(timeout, 30))
    if not status.ok:
        raise AssetPipelineError(status.message)

    style_hash = _stable_json_hash(style_profile)
    generation_defaults = style_profile.get("generation", {}) if isinstance(style_profile.get("generation"), dict) else {}
    final_rules = style_profile.get("final_asset_rules", {}) if isinstance(style_profile.get("final_asset_rules"), dict) else {}
    assets = spec.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetPipelineError("asset request must contain a non-empty assets array")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project": spec.get("project", ""),
        "style_profile_id": spec.get("style_profile_id") or style_profile.get("style_profile_id", ""),
        "style_hash": style_hash,
        "backend": "forge",
        "base_url": base_url.rstrip("/"),
        "model": status.current_model,
        "generated_at": int(time.time()),
        "spec_path": _as_project_path(spec_path, project_root),
        "style_profile_path": _as_project_path(style_profile_path, project_root) if style_profile_path else "",
        "output_dir": _as_project_path(output_dir, project_root),
        "assets": [],
    }

    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            raise AssetPipelineError(f"asset #{index} is not an object")
        asset_id = _safe_id(str(asset.get("id") or f"asset_{index}"))
        transparency_strategy = _transparency_strategy(asset=asset, style_profile=style_profile)
        prompt = _compile_prompt(asset=asset, spec=spec, style_profile=style_profile, transparency_strategy=transparency_strategy)
        negative_prompt = _compile_negative_prompt(asset=asset, style_profile=style_profile)
        width = int(asset.get("width") or generation_defaults.get("width") or 512)
        height = int(asset.get("height") or generation_defaults.get("height") or 512)
        target_width = int(asset.get("target_width") or final_rules.get("default_target_width") or width)
        target_height = int(asset.get("target_height") or final_rules.get("default_target_height") or height)
        seed = int(asset.get("seed") if asset.get("seed") is not None else int(time.time()) + index)
        transparent = bool(asset.get("transparent", True))

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": int(asset.get("steps") or generation_defaults.get("steps") or 20),
            "cfg_scale": float(asset.get("cfg_scale") or generation_defaults.get("cfg_scale") or 6.0),
            "sampler_name": str(asset.get("sampler_name") or generation_defaults.get("sampler_name") or "Euler a"),
            "seed": seed,
            "batch_size": 1,
            "n_iter": 1,
            "save_images": False,
            "do_not_save_samples": True,
        }
        response = _post_json(f"{base_url.rstrip('/')}/sdapi/v1/txt2img", payload, timeout=timeout)
        image_data = _first_image_data(response)

        raw_path = output_dir / f"{asset_id}.source.png"
        final_path = output_dir / f"{asset_id}.png"
        raw_path.write_bytes(image_data)
        _postprocess_png(
            raw_path=raw_path,
            final_path=final_path,
            target_size=(target_width, target_height),
            transparent=transparent,
            transparency_strategy=transparency_strategy,
            chroma_key=str(style_profile.get("chroma_key") or "#00ff00"),
            padding_ratio=float(final_rules.get("padding_ratio", 0.12)),
            rembg_model_cache=project_root / ".asset_pipeline" / "rembg_models",
        )
        asset_entry = {
            "id": asset_id,
            "type": asset.get("type", ""),
            "usage": asset.get("usage", ""),
            "description": asset.get("description", ""),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "generation": payload,
            "raw_path": _as_project_path(raw_path, project_root),
            "path": _as_project_path(final_path, project_root),
            "target_width": target_width,
            "target_height": target_height,
            "transparent": transparent,
            "transparency_strategy": transparency_strategy,
            "style_profile_id": manifest["style_profile_id"],
            "style_hash": style_hash,
        }
        asset_entry["validation"] = _validate_image(final_path, asset_entry)
        manifest["assets"].append(asset_entry)

    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    manifest["validation"] = validation
    manifest["visual_review"] = {
        "status": "pending",
        "reviewer": "",
        "notes": "Visual review is required before integrating generated assets.",
        "reviewed_at": None,
    }
    manifest["ready_for_integration"] = False
    report_path = _validation_report_path(manifest_path)
    manifest["manifest_path"] = _as_project_path(manifest_path, project_root)
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def validate_asset_pack(*, manifest_path: Path, project_root: Path | None = None) -> dict[str, Any]:
    project_root = (project_root or Path.cwd()).resolve()
    manifest_path = _resolve_existing(manifest_path, project_root=project_root)
    manifest = _read_json(manifest_path)
    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    visual_review = _current_visual_review(manifest)
    ready_for_integration = validation.get("status") == "passed" and visual_review.get("status") == "approved"
    report_path = _validation_report_path(manifest_path)
    manifest["validation"] = validation
    manifest["visual_review"] = visual_review
    manifest["ready_for_integration"] = ready_for_integration
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest_path": _as_project_path(manifest_path, project_root),
        "validation_report_path": _as_project_path(report_path, project_root),
        "validation": validation,
        "visual_review": visual_review,
        "ready_for_integration": ready_for_integration,
    }


def record_visual_review(
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
        raise AssetPipelineError("visual review decision must be `approved` or `rejected`")
    manifest = _read_json(manifest_path)
    validation = validate_manifest_data(manifest=manifest, project_root=project_root)
    visual_review = {
        "status": normalized_decision,
        "reviewer": reviewer.strip() or "unspecified-reviewer",
        "notes": notes.strip(),
        "reviewed_at": int(time.time()),
    }
    ready_for_integration = validation.get("status") == "passed" and normalized_decision == "approved"
    report_path = _validation_report_path(manifest_path)
    manifest["validation"] = validation
    manifest["visual_review"] = visual_review
    manifest["ready_for_integration"] = ready_for_integration
    manifest["validation_report_path"] = _as_project_path(report_path, project_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest_path": _as_project_path(manifest_path, project_root),
        "validation_report_path": _as_project_path(report_path, project_root),
        "validation": validation,
        "visual_review": visual_review,
        "ready_for_integration": ready_for_integration,
    }


def validate_manifest_data(*, manifest: dict[str, Any], project_root: Path) -> dict[str, Any]:
    results = []
    all_ok = True
    for asset in manifest.get("assets", []):
        result = _validate_image(_resolve_existing(Path(asset.get("path", "")), project_root=project_root), asset)
        results.append({"id": asset.get("id", ""), **result})
        if result["status"] != "passed":
            all_ok = False
    return {
        "status": "passed" if all_ok and results else "failed",
        "asset_count": len(results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate 2D game art assets through Forge/A1111.")
    parser.add_argument("--project-root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    parser.add_argument("--base-url", default=os.environ.get("FORGE_BASE_URL", DEFAULT_BASE_URL))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-backend")

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
    try:
        if args.command == "check-backend":
            print(json.dumps(check_backend(base_url=args.base_url).to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "generate":
            result = generate_asset_pack(
                spec_path=Path(args.spec),
                output_dir=Path(args.out),
                manifest_path=Path(args.manifest),
                style_profile_path=Path(args.style_profile) if args.style_profile else None,
                base_url=args.base_url,
                project_root=project_root,
                timeout=args.timeout,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate":
            result = validate_asset_pack(manifest_path=Path(args.manifest), project_root=project_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            result = record_visual_review(
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
        return _normalize_style_profile(_read_json(path.resolve()))
    inline = spec.get("style")
    if isinstance(inline, dict) and inline:
        return _normalize_style_profile(inline)
    plugin_root = Path(__file__).resolve().parents[1]
    default_path = plugin_root / "templates" / "style_profile.animagine-xl-2d-game.json"
    return _normalize_style_profile(_read_json(default_path))


def _normalize_style_profile(style_profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(style_profile)
    if not isinstance(normalized.get("generation"), dict):
        generation: dict[str, Any] = {}
        for candidate_key in ["backend_generation_settings", "generation_settings"]:
            candidate = normalized.get(candidate_key)
            if isinstance(candidate, dict):
                generation.update(candidate)
        backend = normalized.get("backend")
        if isinstance(backend, dict):
            for key in ["sampler_name", "steps", "cfg_scale", "width", "height", "seed", "batch_size", "n_iter"]:
                if key in backend and key not in generation:
                    generation[key] = backend[key]
        if generation:
            sampler = str(generation.get("sampler_name") or "").strip()
            if sampler.lower() == "dpm++ 2m karras":
                generation["sampler_name"] = "DPM++ 2M"
            normalized["generation"] = generation
    return normalized


def _transparency_strategy(*, asset: dict[str, Any], style_profile: dict[str, Any]) -> str:
    value = (
        asset.get("transparency_strategy")
        or asset.get("transparent_strategy")
        or style_profile.get("transparency_strategy")
        or style_profile.get("transparent_strategy")
        or "chroma_key"
    )
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "rmbg": "rembg",
        "background_removal": "rembg",
        "segmentation": "rembg",
        "key": "chroma_key",
        "color_key": "chroma_key",
        "chromakey": "chroma_key",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"chroma_key", "rembg"}:
        raise AssetPipelineError(f"unsupported transparency strategy: {value}")
    return normalized


def _compile_prompt(*, asset: dict[str, Any], spec: dict[str, Any], style_profile: dict[str, Any], transparency_strategy: str) -> str:
    parts = [
        str(style_profile.get("base_prompt_prefix") or "").strip(),
        str(asset.get("description") or "").strip(),
    ]
    asset_type = str(asset.get("type") or "").strip()
    camera_rules = style_profile.get("camera_rules", {})
    if isinstance(camera_rules, dict) and asset_type in camera_rules:
        parts.append(str(camera_rules[asset_type]))
    must_have = asset.get("must_have")
    if isinstance(must_have, list) and must_have:
        parts.append("must include: " + ", ".join(str(item) for item in must_have))
    if bool(asset.get("transparent", True)) and transparency_strategy == "chroma_key":
        key = str(style_profile.get("chroma_key") or "#00ff00")
        parts.append(f"flat solid {key} chroma key background, no shadow on background, no text")
    elif bool(asset.get("transparent", True)) and transparency_strategy == "rembg":
        parts.append("single isolated object, plain simple background, no cast shadow, no text")
    project_style = spec.get("style")
    if isinstance(project_style, dict):
        extra = project_style.get("extra_prompt")
        if extra:
            parts.append(str(extra))
    return ", ".join(part for part in parts if part)


def _compile_negative_prompt(*, asset: dict[str, Any], style_profile: dict[str, Any]) -> str:
    parts = [str(style_profile.get("negative_prompt") or "").strip()]
    avoid = asset.get("avoid")
    if isinstance(avoid, list) and avoid:
        parts.append(", ".join(str(item) for item in avoid))
    return ", ".join(part for part in parts if part)


def _postprocess_png(
    *,
    raw_path: Path,
    final_path: Path,
    target_size: tuple[int, int],
    transparent: bool,
    transparency_strategy: str,
    chroma_key: str,
    padding_ratio: float,
    rembg_model_cache: Path,
) -> None:
    image = Image.open(raw_path).convert("RGBA")
    if transparent:
        if transparency_strategy == "rembg":
            image = _remove_background_rembg(image, model_cache=rembg_model_cache)
        else:
            image = _remove_chroma_key(image, chroma_key)
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    target_w, target_h = target_size
    pad_w = max(0, int(target_w * padding_ratio))
    pad_h = max(0, int(target_h * padding_ratio))
    fit_w = max(1, target_w - pad_w * 2)
    fit_h = max(1, target_h - pad_h * 2)
    image.thumbnail((fit_w, fit_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0 if transparent else 255))
    x = (target_w - image.width) // 2
    y = (target_h - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    final_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(final_path)


def _remove_background_rembg(image: Image.Image, *, model_cache: Path) -> Image.Image:
    model_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("U2NET_HOME", str(model_cache))
    try:
        from rembg import new_session, remove
    except Exception as exc:
        raise AssetPipelineError(
            "rembg transparency strategy requires the `rembg` Python package. "
            "Install it in the runtime Python environment or use transparency_strategy=chroma_key."
        ) from exc
    session = new_session("u2netp")
    result = remove(image, session=session)
    if not isinstance(result, Image.Image):
        result = Image.open(result)
    return result.convert("RGBA")


def _remove_chroma_key(image: Image.Image, chroma_key: str) -> Image.Image:
    key = _hex_color(chroma_key)
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            distance = ((r - key[0]) ** 2 + (g - key[1]) ** 2 + (b - key[2]) ** 2) ** 0.5
            if distance < 42:
                pixels[x, y] = (r, g, b, 0)
            elif distance < 96:
                alpha = int(a * min(1.0, max(0.0, (distance - 42) / 54)))
                pixels[x, y] = (r, g, b, alpha)
    return image


def _validate_image(path: Path, asset: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        image = Image.open(path).convert("RGBA")
    except Exception as exc:
        return {"status": "failed", "errors": [f"cannot open image: {exc}"], "warnings": []}
    expected_w = int(asset.get("target_width") or image.width)
    expected_h = int(asset.get("target_height") or image.height)
    if image.size != (expected_w, expected_h):
        errors.append(f"expected {expected_w}x{expected_h}, got {image.width}x{image.height}")
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    if bool(asset.get("transparent", True)):
        if extrema[0] > 8:
            errors.append("alpha channel exists but no clearly transparent pixels were found")
        corners = [alpha.getpixel((0, 0)), alpha.getpixel((image.width - 1, 0)), alpha.getpixel((0, image.height - 1)), alpha.getpixel((image.width - 1, image.height - 1))]
        if max(corners) > 16:
            warnings.append("one or more corners are not fully transparent after chroma-key removal")
    bbox = alpha.getbbox()
    if bbox is None:
        errors.append("image is fully transparent")
    else:
        coverage = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / float(image.width * image.height)
        if coverage < 0.08:
            warnings.append(f"subject coverage is small: {coverage:.2f}")
        if coverage > 0.98:
            warnings.append(f"subject coverage fills nearly the whole image: {coverage:.2f}")
    return {"status": "passed" if not errors else "failed", "errors": errors, "warnings": warnings, "width": image.width, "height": image.height, "alpha_extrema": extrema}


def _get_json(url: str, *, timeout: int) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _first_image_data(response: Any) -> bytes:
    if not isinstance(response, dict):
        raise AssetPipelineError("txt2img response was not an object")
    images = response.get("images")
    if not isinstance(images, list) or not images:
        raise AssetPipelineError("txt2img response did not contain images")
    data = str(images[0])
    if "," in data and data.split(",", 1)[0].startswith("data:image"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise AssetPipelineError(f"{path} must contain a JSON object")
    return data


def _stable_json_hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _resolve_existing(path: Path, *, project_root: Path) -> Path:
    resolved = path if path.is_absolute() else project_root / path
    resolved = resolved.resolve()
    if not resolved.exists():
        raise AssetPipelineError(f"path does not exist: {resolved}")
    return resolved


def _resolve_output(path: Path, *, project_root: Path) -> Path:
    resolved = path if path.is_absolute() else project_root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise AssetPipelineError(f"output path must stay under project root: {resolved}") from exc
    return resolved


def _as_project_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        return str(path.resolve())


def _validation_report_path(manifest_path: Path) -> Path:
    if manifest_path.name == "asset_manifest.json":
        return manifest_path.parent / "validation_report.json"
    return manifest_path.with_name(f"{manifest_path.stem}.validation_report.json")


def _current_visual_review(manifest: dict[str, Any]) -> dict[str, Any]:
    review = manifest.get("visual_review")
    if isinstance(review, dict) and str(review.get("status") or "") in {"pending", "approved", "rejected"}:
        return review
    return {
        "status": "pending",
        "reviewer": "",
        "notes": "Visual review is required before integrating generated assets.",
        "reviewed_at": None,
    }


def _safe_id(value: str) -> str:
    allowed = []
    for char in value.strip():
        allowed.append(char if char.isalnum() or char in "-_" else "_")
    result = "".join(allowed).strip("_")
    return result or "asset"


def _hex_color(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return (0, 255, 0)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (0, 255, 0)


def _unwrap_powershell_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and set(value).issubset({"value", "Count"}):
        return value["value"]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
