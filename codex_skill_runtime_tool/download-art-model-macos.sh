#!/usr/bin/env bash
set -euo pipefail

STABILITY_ROOT="${STABILITY_ROOT:-/Applications/Data}"
MODEL_DIR="${MODEL_DIR:-${STABILITY_ROOT}/Packages/Stable Diffusion WebUI reForge/models/Stable-diffusion}"
MODEL_NAME="${MODEL_NAME:-animagine-xl-3.1.safetensors}"
MODEL_URL="${MODEL_URL:-https://huggingface.co/cagliostrolab/animagine-xl-3.1/resolve/main/animagine-xl-3.1.safetensors}"
TARGET="${MODEL_DIR}/${MODEL_NAME}"
PARTIAL="${TARGET}.partial"

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7897}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"

mkdir -p "${MODEL_DIR}"
if [[ -s "${TARGET}" ]]; then
  echo "[ OK ] Model already exists: ${TARGET}"
  ls -lh "${TARGET}"
  exit 0
fi

echo "[RUN ] Downloading art checkpoint:"
echo "       ${MODEL_URL}"
echo "       -> ${TARGET}"
curl -L --fail --continue-at - --retry 20 --retry-delay 3 --retry-all-errors --output "${PARTIAL}" "${MODEL_URL}"
mv "${PARTIAL}" "${TARGET}"
echo "[ OK ] Downloaded model:"
ls -lh "${TARGET}"
