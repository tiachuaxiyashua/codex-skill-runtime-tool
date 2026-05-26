from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"


def compact_observations(
    observations: list[dict[str, Any]],
    *,
    session_dir: Path,
    threshold_chars: int = 50000,
    keep_recent_steps: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist and replace old strict-loop observations when context grows.

    Claude Code's cached microcompact uses provider cache-editing primitives.
    This runtime cannot depend on those private primitives, so it preserves the
    execution effect that matters for skills: old bulky tool results stop
    bloating later prompts, while full evidence remains available on disk.
    """

    serialized = json.dumps(observations, ensure_ascii=False, default=str)
    if len(serialized) <= threshold_chars:
        return observations, []

    compacted = copy.deepcopy(observations)
    records: list[dict[str, Any]] = []
    cutoff = max(0, len(compacted) - keep_recent_steps)
    for observation_index, observation in enumerate(compacted[:cutoff]):
        actions = observation.get("actions")
        if not isinstance(actions, list):
            continue
        for action_index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            data = action.get("data")
            data_text = json.dumps(data, ensure_ascii=False, default=str)
            if len(data_text) < 2000:
                continue
            rel_path = Path("microcompact") / f"step-{observation.get('step', observation_index)}-action-{action_index}.json"
            output = session_dir / rel_path
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                output.write_text(json.dumps(action, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            record = {
                "step": observation.get("step", observation_index),
                "action_index": action_index,
                "tool": action.get("tool"),
                "status": action.get("status"),
                "path": str(output),
                "bytes": len(data_text.encode("utf-8", errors="replace")),
                "replacement_text": TIME_BASED_MC_CLEARED_MESSAGE,
            }
            action["data"] = {
                "_microcompact": {
                    "message": TIME_BASED_MC_CLEARED_MESSAGE,
                    "full_result_path": str(output),
                    "tool": action.get("tool"),
                    "status": action.get("status"),
                }
            }
            records.append(record)

    if records:
        manifest = session_dir / "microcompact" / "manifest.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return compacted, records
