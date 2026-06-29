#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${DETACHED_RUNTIME_UI:-0}" != "1" ]]; then
  exec "${TOOL_ROOT}/start-runtime-shortcut-macos.sh" "$@"
fi

WORKSPACE_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
RUNTIME_ENV="${RUNTIME_ENV:-${TOOL_ROOT}/config/skill-runtime.env}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
URL="http://${HOST}:${PORT}"
OPEN_URL="${URL}/?v=20260617-chatstream-v3"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  for candidate in \
    "${TOOL_ROOT}/.skill-runtime/skill-python/bin/python" \
    "/Applications/Data/Assets/Python/cpython-3.12.12-macos-aarch64-none/bin/python3.12" \
    "/opt/homebrew/bin/python3.12" \
    "/opt/homebrew/bin/python3" \
    "$(command -v python3 || true)"; do
    if [[ -n "${candidate}" && -x "${candidate}" ]] && "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[FAIL] Python 3.10+ not found." >&2
  exit 1
fi

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7897}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"
export SKILL_RUNTIME_TOOL_ROOT="${TOOL_ROOT}"
export SKILL_RUNTIME_WORKSPACE_ROOT="${WORKSPACE_ROOT}"
export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

kill_pid_tree() {
  local pid="$1"
  [[ -z "${pid}" || "${pid}" == "$$" ]] && return
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    return
  fi
  local children
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  while read -r child; do
    [[ -z "${child}" ]] && continue
    kill_pid_tree "${child}"
  done <<<"${children}"
  kill "${pid}" >/dev/null 2>&1 || true
}

force_kill_pid_tree() {
  local pid="$1"
  [[ -z "${pid}" || "${pid}" == "$$" ]] && return
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    return
  fi
  local children
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  while read -r child; do
    [[ -z "${child}" ]] && continue
    force_kill_pid_tree "${child}"
  done <<<"${children}"
  kill -9 "${pid}" >/dev/null 2>&1 || true
}

cleanup_runtime_processes() {
  local pattern pids
  for pattern in "${TOOL_ROOT}/runtime-ui.py" "${TOOL_ROOT}/codex-skill-runtime-core/core_cli.py"; do
    pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
    while read -r pid; do
      [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
      echo "[RUN ] Stopping previous runtime process: pid=${pid}"
      kill_pid_tree "${pid}"
    done <<<"${pids}"
  done
  sleep 0.5
  for pattern in "${TOOL_ROOT}/runtime-ui.py" "${TOOL_ROOT}/codex-skill-runtime-core/core_cli.py"; do
    pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
    while read -r pid; do
      [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
      echo "[RUN ] Force stopping previous runtime process: pid=${pid}"
      force_kill_pid_tree "${pid}"
    done <<<"${pids}"
  done
}

stop_existing_listener() {
  local pids
  curl -fsS -X POST -H "Content-Type: application/json" \
    --data '{"source":"startup-replace"}' \
    "${URL}/api/ui/shutdown" >/dev/null 2>&1 || true
  sleep 0.5
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "${pids}" ]] && return
  echo "[RUN ] Stopping existing listener on ${URL}: ${pids}"
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    kill_pid_tree "${pid}"
  done <<<"${pids}"
  for _ in $(seq 1 20); do
    [[ -z "$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)" ]] && return
    sleep 0.25
  done
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    force_kill_pid_tree "${pid}"
  done <<<"${pids}"
}

stop_existing_listener
cleanup_runtime_processes

mkdir -p "${TOOL_ROOT}/.skill-runtime/ui-processes"
stdout="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.detached.stdout.log"
stderr="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.detached.stderr.log"
echo "[RUN ] Starting Runtime UI in detached mode"
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
      open "${OPEN_URL}"
    fi
    exit 0
  fi
  sleep 0.5
done

echo "[FAIL] Runtime UI did not become healthy." >&2
echo "       stdout: ${stdout}" >&2
echo "       stderr: ${stderr}" >&2
exit 1
