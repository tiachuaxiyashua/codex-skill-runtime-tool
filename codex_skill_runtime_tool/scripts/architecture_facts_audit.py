#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SELECTED_FILES = {
    "server.py": Path("codex_skill_runtime_tool/codex-skill-runtime-ui/backend/server.py"),
    "tool_executor.py": Path("codex_skill_runtime_tool/codex-skill-runtime-core/runtime/tool_executor.py"),
    "runtime.py": Path("codex_skill_runtime_tool/codex-skill-runtime-core/runtime/runtime.py"),
    "selftest.py": Path("codex_skill_runtime_tool/codex-skill-runtime-core/runtime/selftest.py"),
    "app.js": Path("codex_skill_runtime_tool/codex-skill-runtime-ui/frontend/app.js"),
}

LARGE_SKILL_DIRS = {
    ".claude-plugin",
    "agents",
    "hooks",
    "mcp",
    "schemas",
    "scripts",
    "skills",
    "templates",
}

BRIDGE_SKILL_DIRS = {".claude-plugin", "scripts", "skills"}


@dataclass
class SkillSkeleton:
    name: str
    path: str
    kind: str
    directories: list[str]


def resolve_repo_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _ in handle)


def count_python_files(root: Path, *, exclude_references: bool = False) -> int:
    total = 0
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or ".git" in path.parts:
            continue
        if exclude_references and "references" in path.parts:
            continue
        total += 1
    return total


def git_status_counts(root: Path) -> dict[str, int]:
    result = _run_git(root, ["status", "--short"])
    counts = Counter()
    for line in result.splitlines():
        if not line:
            continue
        counts[line[:2]] += 1
    return {
        "modified": counts[" M"],
        "deleted": counts[" D"],
        "added": counts["A "],
        "renamed": counts["R "],
        "copied": counts["C "],
        "untracked": counts["??"],
        "ignored": counts["!!"],
        "other": sum(counts.values()) - sum(counts[prefix] for prefix in (" M", " D", "A ", "R ", "C ", "??", "!!")),
        "total": sum(counts.values()),
    }


def git_tracked_diff(root: Path) -> dict[str, Any]:
    shortstat = _run_git(root, ["diff", "--shortstat"]).strip()
    if not shortstat:
        return {
            "files_changed": 0,
            "insertions": 0,
            "deletions": 0,
            "net_change": 0,
            "shortstat": "",
        }
    match = re.search(
        r"(?P<files>\d+)\s+files? changed(?:,\s+(?P<insertions>\d+)\s+insertions?\(\+\))?"
        r"(?:,\s+(?P<deletions>\d+)\s+deletions?\(-\))?",
        shortstat,
    )
    if not match:
        raise ValueError(f"unexpected git shortstat format: {shortstat}")
    files_changed = int(match.group("files"))
    insertions = int(match.group("insertions") or 0)
    deletions = int(match.group("deletions") or 0)
    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "net_change": insertions - deletions,
        "shortstat": shortstat,
    }


def skill_skeleton(path: Path) -> SkillSkeleton:
    dirs = sorted(
        entry.name
        for entry in path.iterdir()
        if entry.is_dir() and entry.name not in {"__pycache__"}
    )
    dir_set = set(dirs)
    if LARGE_SKILL_DIRS.issubset(dir_set):
        kind = "large-skill"
    elif BRIDGE_SKILL_DIRS.issubset(dir_set):
        kind = "bridge-skill"
    else:
        kind = "custom-skill"
    return SkillSkeleton(name=path.name, path=str(path), kind=kind, directories=dirs)


def collect_skill_skeletons(root: Path) -> list[SkillSkeleton]:
    skills: list[SkillSkeleton] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.is_dir() and entry.name.endswith("_skill"):
            skills.append(skill_skeleton(entry))
    return skills


def build_snapshot(repo_root: Path) -> dict[str, Any]:
    selected_files = {}
    for label, relative in SELECTED_FILES.items():
        path = repo_root / relative
        selected_files[label] = {
            "path": str(path),
            "exists": path.exists(),
            "lines": count_lines(path) if path.exists() else None,
        }

    skeletons = collect_skill_skeletons(repo_root)
    return {
        "repo_root": str(repo_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python_files": {
            "total": count_python_files(repo_root),
            "excluding_references": count_python_files(repo_root, exclude_references=True),
        },
        "selected_files": selected_files,
        "tracked_diff": git_tracked_diff(repo_root),
        "status_counts": git_status_counts(repo_root),
        "skill_skeletons": [asdict(skeleton) for skeleton in skeletons],
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# Repository Architecture Facts Snapshot",
        "",
        f"- Repo root: `{snapshot['repo_root']}`",
        f"- Generated at: `{snapshot['generated_at']}`",
        f"- Python files (total): {snapshot['python_files']['total']}",
        f"- Python files (excluding references): {snapshot['python_files']['excluding_references']}",
        "",
        "## Selected files",
        "",
        "| File | Exists | Lines |",
        "|---|---:|---:|",
    ]
    for label, data in snapshot["selected_files"].items():
        lines.append(f"| `{label}` | {str(data['exists']).lower()} | {data['lines'] if data['lines'] is not None else 'n/a'} |")
    lines.extend(
        [
            "",
            "## Tracked diff",
            "",
            f"- Files changed: {snapshot['tracked_diff']['files_changed']}",
            f"- Insertions: {snapshot['tracked_diff']['insertions']}",
            f"- Deletions: {snapshot['tracked_diff']['deletions']}",
            f"- Net change: {snapshot['tracked_diff']['net_change']}",
            "",
            "## Skill skeletons",
            "",
            "| Skill | Kind | Top-level dirs |",
            "|---|---|---|",
        ]
    )
    for item in snapshot["skill_skeletons"]:
        dirs = ", ".join(item["directories"])
        lines.append(f"| `{item['name']}` | {item['kind']} | {dirs} |")
    lines.extend(
        [
            "",
            "## Note",
            "",
            "This snapshot is regenerable from the current workspace. The human-readable diagnosis lives in `项目问题分析图谱.md`.",
        ]
    )
    return "\n".join(lines)


def run_selftest(snapshot: dict[str, Any]) -> None:
    total = 0
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        nonlocal total
        total += 1
        if condition:
            print(f"PASS: {label}")
        else:
            print(f"FAIL: {label}")
            failures.append(label)

    repo_root = Path(snapshot["repo_root"])
    check(repo_root.exists() and repo_root.is_dir(), "repo-root-resolves")
    check(snapshot["python_files"]["total"] > 0, "python-file-count-positive")
    check(snapshot["python_files"]["excluding_references"] > 0, "python-file-count-without-references-positive")

    for label, data in snapshot["selected_files"].items():
        check(data["exists"], f"{label}-exists")
        check(isinstance(data["lines"], int) and data["lines"] > 0, f"{label}-line-count-int")

    diff = snapshot["tracked_diff"]
    check(isinstance(diff["files_changed"], int), "tracked-diff-files-int")
    check(isinstance(diff["insertions"], int), "tracked-diff-insertions-int")
    check(isinstance(diff["deletions"], int), "tracked-diff-deletions-int")
    check(isinstance(diff["net_change"], int), "tracked-diff-net-int")

    skeletons = {item["name"]: item for item in snapshot["skill_skeletons"]}
    art = skeletons.get("art_pipeline_skill")
    audio = skeletons.get("audio_pipeline_skill")
    bridge = skeletons.get("godot_tool_bridge_skill")

    check(art is not None and audio is not None and bridge is not None, "skill-skeletons-present")
    if art and audio:
        check(art["kind"] == "large-skill", "art-skill-kind-large")
        check(audio["kind"] == "large-skill", "audio-skill-kind-large")
        check(art["directories"] == audio["directories"], "art-audio-same-skeleton")
    if bridge:
        check(bridge["kind"] == "bridge-skill", "bridge-skill-kind-bridge")

    print(f"SELFTEST_SUMMARY total={total} failed={len(failures)}")
    if failures:
        raise SystemExit(1)


def _run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit runtime repository architecture facts.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root. Defaults to the parent repo of this script.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    parser.add_argument("--selftest", action="store_true", help="Run internal checks and exit.")
    args = parser.parse_args(argv)

    repo_root = resolve_repo_root(args.repo_root)
    snapshot = build_snapshot(repo_root)

    if args.selftest:
        run_selftest(snapshot)
        return 0

    if args.format == "json":
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
