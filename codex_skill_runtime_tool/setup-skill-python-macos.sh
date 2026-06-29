#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STABILITY_ROOT="${STABILITY_ROOT:-/Applications/Data}"
PYTHON_BIN="${PYTHON_BIN:-${STABILITY_ROOT}/Assets/Python/cpython-3.12.12-macos-aarch64-none/bin/python3.12}"
VENV_ROOT="${SKILL_RUNTIME_PLUGIN_PYTHON_VENV:-${TOOL_ROOT}/.skill-runtime/skill-python}"

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7897}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[FAIL] Python missing: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
  echo "[RUN ] Creating project-local skill Python venv: ${VENV_ROOT}"
  "${PYTHON_BIN}" -m venv "${VENV_ROOT}"
fi

echo "[RUN ] Installing lightweight skill script dependencies."
"${VENV_ROOT}/bin/python" -m pip install --upgrade pip wheel setuptools
"${VENV_ROOT}/bin/python" -m pip install Pillow

if [[ "${INSTALL_REMBG:-false}" == "true" ]]; then
  echo "[RUN ] Installing optional rembg support."
  "${VENV_ROOT}/bin/python" -m pip install rembg
else
  echo "[WARN] rembg not installed by default. Run INSTALL_REMBG=true $0 if rembg transparency is required."
fi

echo "[ OK ] skill python: ${VENV_ROOT}/bin/python"
echo "       Add to PATH for plugin MCP: ${VENV_ROOT}/bin"
