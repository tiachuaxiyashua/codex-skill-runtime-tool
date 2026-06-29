#!/usr/bin/env bash
cd "$(dirname "$0")/.."
./codex_skill_runtime_tool/start-runtime-shortcut-macos.sh
echo
echo "Runtime shortcut session has ended."
read -r -p "Press Enter to close this window..."
