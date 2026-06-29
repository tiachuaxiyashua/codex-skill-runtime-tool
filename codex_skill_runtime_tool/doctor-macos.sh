#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
RUNTIME_ENV="${RUNTIME_ENV:-${TOOL_ROOT}/config/skill-runtime.env}"
NATIVE_DEPS_ROOT="${TOOL_ROOT}/.skill-runtime/native-deps"
STABILITY_ROOT="${STABILITY_ROOT:-/Applications/Data}"
GODOT_EXE="${GODOT_EXE:-/Applications/Godot.app/Contents/MacOS/Godot}"
PYTHON_BIN="${PYTHON_BIN:-/Applications/Data/Assets/Python/cpython-3.12.12-macos-aarch64-none/bin/python3.12}"

pass() { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; FAILED=1; }

FAILED=0

echo "Codex Skill Runtime macOS doctor"
echo "workspace: ${WORKSPACE_ROOT}"
echo

if [[ -f "${RUNTIME_ENV}" ]]; then
  pass "runtime env: ${RUNTIME_ENV}"
else
  fail "runtime env missing: ${RUNTIME_ENV}"
fi

if [[ -x "${PYTHON_BIN}" ]]; then
  pass "python: $("${PYTHON_BIN}" --version 2>&1) (${PYTHON_BIN})"
else
  fail "python missing: ${PYTHON_BIN}"
fi

if [[ -x "${GODOT_EXE}" ]]; then
  pass "godot: $("${GODOT_EXE}" --version 2>/dev/null || true)"
else
  fail "godot missing: ${GODOT_EXE}"
fi

if [[ -x "${NATIVE_DEPS_ROOT}/bin/pkg-config" ]]; then
  export PATH="${NATIVE_DEPS_ROOT}/bin:/opt/homebrew/bin:${HOME}/.local/bin:${PATH}"
  export PKG_CONFIG_PATH="${NATIVE_DEPS_ROOT}/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig:${PKG_CONFIG_PATH:-}"
  pass "pkg-config: $(command -v pkg-config)"
  for dep in cairo zlib freetype2 expat; do
    if pkg-config --exists "${dep}"; then
      pass "${dep}: $(pkg-config --modversion "${dep}")"
    else
      fail "${dep}: pkg-config entry missing"
    fi
  done
else
  fail "native deps missing. Run: ${TOOL_ROOT}/setup-macos-deps.sh"
fi

COMFY_ROOT="${STABILITY_ROOT}/Packages/ComfyUI"
if [[ -x "${COMFY_ROOT}/venv/bin/python" ]]; then
  pass "ComfyUI venv: ${COMFY_ROOT}/venv/bin/python"
  "${COMFY_ROOT}/venv/bin/python" - <<'PY' || FAILED=1
import torch
print(f"[ OK ] ComfyUI torch: {torch.__version__}, mps={torch.backends.mps.is_available()}")
PY
else
  fail "ComfyUI venv missing: ${COMFY_ROOT}/venv/bin/python"
fi

REFORGE_ROOT="${STABILITY_ROOT}/Packages/Stable Diffusion WebUI reForge"
if [[ -x "${REFORGE_ROOT}/venv/bin/python" ]]; then
  pass "reForge venv: ${REFORGE_ROOT}/venv/bin/python"
  "${REFORGE_ROOT}/venv/bin/python" - <<'PY' || FAILED=1
import cairo
import torch
import gradio
print(f"[ OK ] reForge pycairo: {cairo.cairo_version_string()}")
print(f"[ OK ] reForge torch: {torch.__version__}, mps={torch.backends.mps.is_available()}")
print(f"[ OK ] reForge gradio: {gradio.__version__}")
PY
else
  fail "reForge venv missing: ${REFORGE_ROOT}/venv/bin/python"
fi

checkpoint_count=0
if [[ -d "${REFORGE_ROOT}/models/Stable-diffusion" ]]; then
  checkpoint_count="$(find "${REFORGE_ROOT}/models/Stable-diffusion" -type f \( -name '*.safetensors' -o -name '*.ckpt' \) 2>/dev/null | wc -l | tr -d ' ')"
fi
if [[ "${checkpoint_count}" -gt 0 ]]; then
  pass "Forge checkpoints: ${checkpoint_count}"
else
  warn "Forge checkpoint model missing. The API can start, but image generation needs a .safetensors or .ckpt model."
fi

if curl -fsS --connect-timeout 2 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
  pass "ComfyUI API: http://127.0.0.1:8188/system_stats"
else
  warn "ComfyUI API is not running. Start it from Stability Matrix or the Runtime UI external services panel when needed."
fi

if curl -fsS --connect-timeout 2 http://127.0.0.1:7860/sdapi/v1/options >/dev/null 2>&1; then
  pass "Forge API: http://127.0.0.1:7860/sdapi/v1/options"
else
  warn "Forge API is not running. Start it from Stability Matrix or the Runtime UI external services panel when needed."
fi

if [[ -x "${PYTHON_BIN}" && -f "${TOOL_ROOT}/codex-skill-runtime-core/core_cli.py" ]]; then
  if "${PYTHON_BIN}" -B "${TOOL_ROOT}/codex-skill-runtime-core/core_cli.py" --runtime-env "${RUNTIME_ENV}" inspect >/tmp/skill-runtime-inspect.json; then
    skill_count="$("${PYTHON_BIN}" - <<'PY'
import json
data=json.load(open('/tmp/skill-runtime-inspect.json'))
print(len(data.get('skills', [])))
PY
)"
    pass "runtime inspect: ${skill_count} skills"
  else
    fail "runtime inspect failed"
  fi
fi

echo
if [[ "${FAILED}" -eq 0 ]]; then
  pass "doctor completed"
else
  fail "doctor found blocking problems"
fi
exit "${FAILED}"
