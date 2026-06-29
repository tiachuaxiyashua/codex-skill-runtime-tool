#!/usr/bin/env bash
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
RUNTIME_ENV="${RUNTIME_ENV:-${TOOL_ROOT}/config/skill-runtime.env}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
URL="http://${HOST}:${PORT}"
OPEN_URL="${URL}/?v=20260617-chatstream-v3&shutdown_on_close=1"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
UI_IDLE_TIMEOUT_SECONDS="${UI_IDLE_TIMEOUT_SECONDS:-10}"
UI_OPEN_TIMEOUT_SECONDS="${UI_OPEN_TIMEOUT_SECONDS:-30}"
REUSE_EXISTING_RUNTIME="${REUSE_EXISTING_RUNTIME:-0}"

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

runtime_pid=""
runtime_started="0"

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
  local patterns pids
  patterns=(
    "${TOOL_ROOT}/runtime-ui.py"
    "${TOOL_ROOT}/codex-skill-runtime-core/core_cli.py"
  )
  for pattern in "${patterns[@]}"; do
    pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
    while read -r pid; do
      [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
      echo "[RUN ] Stopping previous runtime process: pid=${pid}"
      kill_pid_tree "${pid}"
    done <<<"${pids}"
  done
  sleep 0.5
  for pattern in "${patterns[@]}"; do
    pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
    while read -r pid; do
      [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
      echo "[RUN ] Force stopping previous runtime process: pid=${pid}"
      force_kill_pid_tree "${pid}"
    done <<<"${pids}"
  done
}

json_field() {
  local field="$1"
  "${PYTHON_BIN}" -c 'import json,sys; data=json.load(sys.stdin); value=data.get(sys.argv[1]); print("" if value is None else value)' "${field}"
}

cleanup() {
  curl -fsS -X POST -H "Content-Type: application/json" \
    --data '{"source":"shortcut-cleanup"}' \
    "${URL}/api/ui/shutdown" >/dev/null 2>&1 || true
  curl -fsS -X POST -H "Content-Type: application/json" \
    --data '{"closing":true,"source":"shortcut-cleanup"}' \
    "${URL}/api/ui/heartbeat" >/dev/null 2>&1 || true
  if [[ "${runtime_started}" == "1" && -n "${runtime_pid}" ]]; then
    kill "${runtime_pid}" >/dev/null 2>&1 || true
    wait "${runtime_pid}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

stop_existing_listener() {
  local pids
  curl -fsS -X POST -H "Content-Type: application/json" \
    --data '{"source":"startup-replace"}' \
    "${URL}/api/ui/shutdown" >/dev/null 2>&1 || true
  sleep 0.5
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return
  fi
  echo "[RUN ] Stopping existing listener on ${URL}: ${pids}"
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    kill_pid_tree "${pid}"
  done <<<"${pids}"
  for _ in $(seq 1 20); do
    if [[ -z "$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)" ]]; then
      return
    fi
    sleep 0.25
  done
  pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    force_kill_pid_tree "${pid}"
  done <<<"${pids}"
}

start_runtime() {
  mkdir -p "${TOOL_ROOT}/.skill-runtime/ui-processes"
  stdout="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.shortcut.stdout.log"
  stderr="${TOOL_ROOT}/.skill-runtime/ui-processes/runtime-ui.shortcut.stderr.log"
  echo "[RUN ] Starting Runtime UI for shortcut session"
  cd "${WORKSPACE_ROOT}"
  "${PYTHON_BIN}" -B "${TOOL_ROOT}/runtime-ui.py" \
    --runtime-env "${RUNTIME_ENV}" \
    --host "${HOST}" \
    --port "${PORT}" \
    >"${stdout}" 2>"${stderr}" &
  runtime_pid="$!"
  runtime_started="1"
  for _ in $(seq 1 40); do
    if curl -fsS --connect-timeout 2 "${URL}/api/health" >/dev/null 2>&1; then
      echo "[ OK ] Runtime UI started: ${URL}"
      return
    fi
    sleep 0.5
  done
  echo "[FAIL] Runtime UI did not become healthy." >&2
  echo "       stdout: ${stdout}" >&2
  echo "       stderr: ${stderr}" >&2
  exit 1
}

if curl -fsS --connect-timeout 2 "${URL}/api/health" >/dev/null 2>&1; then
  if [[ "${REUSE_EXISTING_RUNTIME}" == "1" ]]; then
    echo "[ OK ] Runtime UI is already running and will be reused: ${URL}"
  else
    stop_existing_listener
    cleanup_runtime_processes
    start_runtime
  fi
else
  stop_existing_listener
  cleanup_runtime_processes
  start_runtime
fi

curl -fsS -X POST -H "Content-Type: application/json" --data '{}' "${URL}/api/ui/heartbeat/reset" >/dev/null 2>&1 || true
if [[ "${OPEN_BROWSER}" != "0" ]]; then
  echo "[OPEN] ${OPEN_URL}"
  open "${OPEN_URL}"
else
  echo "[OPEN] Browser launch disabled. Open manually: ${OPEN_URL}"
  if [[ "${runtime_started}" == "1" && -n "${runtime_pid}" ]]; then
    wait "${runtime_pid}"
  fi
  exit 0
fi

echo "[INFO] Keep this shortcut script running."
echo "[INFO] Close the Runtime UI page to stop services started from the UI."
echo "[INFO] Do not open frontend/index.html directly as file://; that page cannot stop the runtime."

opened_deadline=$((SECONDS + UI_OPEN_TIMEOUT_SECONDS))
while true; do
  status="$(curl -fsS --connect-timeout 2 "${URL}/api/ui/heartbeat" 2>/dev/null || true)"
  seen="$(printf '%s' "${status}" | json_field seen 2>/dev/null || true)"
  if [[ "${seen}" == "True" || "${seen}" == "true" ]]; then
    break
  fi
  if [[ "${SECONDS}" -ge "${opened_deadline}" ]]; then
    echo "[WARN] Runtime UI page did not connect within ${UI_OPEN_TIMEOUT_SECONDS}s."
    echo "       Expected browser URL: ${URL}"
    echo "       If you see file://.../frontend/index.html, close it and use this http URL."
    exit 1
  fi
  sleep 1
done

while true; do
  status="$(curl -fsS --connect-timeout 2 "${URL}/api/ui/heartbeat" 2>/dev/null || true)"
  if [[ -z "${status}" ]]; then
    break
  fi
  closing="$(printf '%s' "${status}" | json_field closing_pending)"
  seen="$(printf '%s' "${status}" | json_field seen)"
  age="$(printf '%s' "${status}" | json_field last_seen_age_seconds)"
  if [[ "${closing}" == "True" || "${closing}" == "true" ]]; then
    break
  fi
  if [[ "${seen}" == "True" || "${seen}" == "true" ]]; then
    if "${PYTHON_BIN}" - "${age}" "${UI_IDLE_TIMEOUT_SECONDS}" <<'PY'
import sys
try:
    age = float(sys.argv[1])
    limit = float(sys.argv[2])
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if age > limit else 1)
PY
    then
      break
    fi
  fi
  sleep 2
done

echo "[DONE] Runtime UI page closed or heartbeat stopped; cleaning up shortcut session."
