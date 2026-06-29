#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]]; then
  exec "${TOOL_ROOT}/start-runtime-macos.sh" "$@"
fi

WORKSPACE_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
RUNTIME_ENV="${RUNTIME_ENV:-${TOOL_ROOT}/config/skill-runtime.env}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
URL="http://${HOST}:${PORT}"
OPEN_URL="${URL}/?v=20260617-chatstream-v3"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python || true)}"
if [[ -z "${PYTHON_BIN}" ]] || ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "[FAIL] Python 3.10+ not found." >&2
  exit 1
fi

export SKILL_RUNTIME_TOOL_ROOT="${TOOL_ROOT}"
export SKILL_RUNTIME_WORKSPACE_ROOT="${WORKSPACE_ROOT}"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

stop_existing_listener() {
  local pids
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "${pids}" ]] && return
  echo "[RUN ] Stopping existing listener on ${URL}: ${pids}"
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    kill "${pid}" >/dev/null 2>&1 || true
  done <<<"${pids}"
}

stop_existing_listener

mkdir -p "${TOOL_ROOT}/.skill-runtime/ui-processes"
stdout="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.stdout.log"
stderr="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.stderr.log"
echo "[RUN ] Starting Runtime UI"
cd "${WORKSPACE_ROOT}"
nohup "${PYTHON_BIN}" -B "${TOOL_ROOT}/runtime-ui.py" \
  --runtime-env "${RUNTIME_ENV}" \
  --host "${HOST}" \
  --port "${PORT}" \
  >"${stdout}" 2>"${stderr}" &

for _ in $(seq 1 40); do
  if curl -fsS --connect-timeout 2 "${URL}/api/health" >/dev/null 2>&1; then
    echo "[ OK ] Runtime UI started: ${URL}"
    if [[ "${OPEN_BROWSER}" != "0" ]]; then
      if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${OPEN_URL}" >/dev/null 2>&1 || true
      else
        echo "[OPEN] ${OPEN_URL}"
      fi
    fi
    exit 0
  fi
  sleep 0.5
done

echo "[FAIL] Runtime UI did not become healthy." >&2
echo "       stdout: ${stdout}" >&2
echo "       stderr: ${stderr}" >&2
exit 1
