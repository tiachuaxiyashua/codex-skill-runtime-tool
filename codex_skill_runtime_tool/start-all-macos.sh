#!/usr/bin/env bash
set -euo pipefail

echo "[INFO] start-all-macos.sh is deprecated."
echo "[INFO] Use start-runtime-macos.sh for runtime UI, and manage external services manually from the UI."
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start-runtime-macos.sh" "$@"
