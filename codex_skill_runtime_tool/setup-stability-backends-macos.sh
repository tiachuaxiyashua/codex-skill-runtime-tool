#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
STABILITY_ROOT="${STABILITY_ROOT:-/Applications/Data}"
PYTHON_BIN="${PYTHON_BIN:-${STABILITY_ROOT}/Assets/Python/cpython-3.12.12-macos-aarch64-none/bin/python3.12}"
NATIVE_DEPS_ROOT="${TOOL_ROOT}/.skill-runtime/native-deps"
COMFY_ROOT="${STABILITY_ROOT}/Packages/ComfyUI"
REFORGE_ROOT="${STABILITY_ROOT}/Packages/Stable Diffusion WebUI reForge"

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7897}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export UV_INDEX_URL="${UV_INDEX_URL:-${PIP_INDEX_URL}}"
export PATH="${NATIVE_DEPS_ROOT}/bin:/opt/homebrew/bin:${HOME}/.local/bin:${PATH}"
export PKG_CONFIG_PATH="${NATIVE_DEPS_ROOT}/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig:${PKG_CONFIG_PATH:-}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[FAIL] Stability Matrix Python missing: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -x "${NATIVE_DEPS_ROOT}/bin/pkg-config" ]]; then
  echo "[RUN ] Installing project-local native dependencies first."
  "${TOOL_ROOT}/setup-macos-deps.sh"
fi

mkdir -p "${STABILITY_ROOT}/Packages"

if [[ ! -d "${COMFY_ROOT}/.git" ]]; then
  echo "[RUN ] Cloning ComfyUI v0.24.0 into Stability Matrix packages."
  rm -rf "${COMFY_ROOT}"
  git clone --branch v0.24.0 https://github.com/comfyanonymous/ComfyUI "${COMFY_ROOT}"
fi

if [[ ! -x "${COMFY_ROOT}/venv/bin/python" ]]; then
  echo "[RUN ] Creating ComfyUI venv."
  "${PYTHON_BIN}" -m venv "${COMFY_ROOT}/venv"
fi

echo "[RUN ] Installing ComfyUI dependencies."
"${COMFY_ROOT}/venv/bin/python" -m pip install --upgrade pip wheel setuptools
"${COMFY_ROOT}/venv/bin/python" -m pip install -r "${COMFY_ROOT}/requirements.txt"

if [[ -x "${REFORGE_ROOT}/venv/bin/python" ]]; then
  echo "[RUN ] Installing reForge dependencies."
  "${REFORGE_ROOT}/venv/bin/python" -m pip install --upgrade pip wheel setuptools
  "${REFORGE_ROOT}/venv/bin/python" -m pip install "pycairo==1.29.0"
  "${REFORGE_ROOT}/venv/bin/python" -m pip install -r "${REFORGE_ROOT}/requirements_versions.txt"
else
  echo "[WARN] reForge venv not found: ${REFORGE_ROOT}/venv/bin/python"
  echo "       Install reForge in Stability Matrix, then rerun this script."
fi

echo "[ OK ] Stability backends are prepared."
echo "       Run doctor: ${TOOL_ROOT}/doctor-macos.sh"
