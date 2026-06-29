#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_DEPS_ROOT="${TOOL_ROOT}/.skill-runtime/native-deps"

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7897}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export UV_INDEX_URL="${UV_INDEX_URL:-${PIP_INDEX_URL}}"
export COMMANDLINE_ARGS="${COMMANDLINE_ARGS:---api --skip-python-version-check --skip-torch-cuda-test}"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"
export PATH="${NATIVE_DEPS_ROOT}/bin:/opt/homebrew/bin:${HOME}/.local/bin:${PATH}"
export PKG_CONFIG_PATH="${NATIVE_DEPS_ROOT}/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig:${PKG_CONFIG_PATH:-}"

if [[ ! -x "/Applications/Stability Matrix.app/Contents/MacOS/StabilityMatrix.Avalonia" ]]; then
  echo "[FAIL] Stability Matrix app not found at /Applications/Stability Matrix.app" >&2
  exit 1
fi

echo "[RUN ] Starting Stability Matrix with project-local native dependencies."
echo "       PATH includes: ${NATIVE_DEPS_ROOT}/bin"
exec "/Applications/Stability Matrix.app/Contents/MacOS/StabilityMatrix.Avalonia"
