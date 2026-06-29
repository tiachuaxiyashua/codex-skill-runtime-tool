#!/usr/bin/env bash
set -euo pipefail

PROXY_URL="${PROXY_URL:-http://127.0.0.1:7897}"
TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MICROMAMBA_ROOT="${TOOL_ROOT}/.skill-runtime/micromamba"
NATIVE_DEPS_ROOT="${TOOL_ROOT}/.skill-runtime/native-deps"

export HTTP_PROXY="${HTTP_PROXY:-${PROXY_URL}}"
export HTTPS_PROXY="${HTTPS_PROXY:-${PROXY_URL}}"
export ALL_PROXY="${ALL_PROXY:-${PROXY_URL}}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export UV_INDEX_URL="${UV_INDEX_URL:-${PIP_INDEX_URL}}"

git config --global http.proxy "${HTTP_PROXY}"
git config --global https.proxy "${HTTPS_PROXY}"

mkdir -p "${MICROMAMBA_ROOT}"
if [[ ! -x "${MICROMAMBA_ROOT}/bin/micromamba" ]]; then
  echo "[RUN ] Installing project-local micromamba."
  curl -L --fail https://micro.mamba.pm/api/micromamba/osx-arm64/latest | tar -xvj -C "${MICROMAMBA_ROOT}" bin/micromamba
fi

echo "[RUN ] Installing project-local native build/runtime dependencies."
"${MICROMAMBA_ROOT}/bin/micromamba" create -y -p "${NATIVE_DEPS_ROOT}" -c conda-forge pkg-config cairo cmake zlib libzlib freetype expat ffmpeg

export PATH="${NATIVE_DEPS_ROOT}/bin:/opt/homebrew/bin:${HOME}/.local/bin:${PATH}"
export PKG_CONFIG_PATH="${NATIVE_DEPS_ROOT}/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig:${PKG_CONFIG_PATH:-}"

launchctl setenv HTTP_PROXY "${HTTP_PROXY}"
launchctl setenv HTTPS_PROXY "${HTTPS_PROXY}"
launchctl setenv ALL_PROXY "${ALL_PROXY}"
launchctl setenv NO_PROXY "${NO_PROXY}"
launchctl setenv no_proxy "${no_proxy}"
launchctl setenv PIP_INDEX_URL "${PIP_INDEX_URL}"
launchctl setenv PIP_TRUSTED_HOST "${PIP_TRUSTED_HOST}"
launchctl setenv UV_INDEX_URL "${UV_INDEX_URL}"
launchctl setenv PATH "${NATIVE_DEPS_ROOT}/bin:/opt/homebrew/bin:${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
launchctl setenv PKG_CONFIG_PATH "${NATIVE_DEPS_ROOT}/lib/pkgconfig:/opt/homebrew/lib/pkgconfig:/opt/homebrew/share/pkgconfig"

echo "[ OK ] Git proxy: $(git config --global --get https.proxy)"
echo "[ OK ] pkg-config: $(command -v pkg-config)"
echo "[ OK ] cairo: $(pkg-config --modversion cairo)"
echo "[ OK ] cmake: $(command -v cmake)"
echo "[ OK ] ffmpeg: $(command -v ffmpeg)"
echo "[ OK ] pip index: ${PIP_INDEX_URL}"
echo
echo "Restart Stability Matrix from Terminal so it inherits PATH/PKG_CONFIG_PATH:"
echo '  ./codex_skill_runtime_tool/start-stability-matrix.sh'
