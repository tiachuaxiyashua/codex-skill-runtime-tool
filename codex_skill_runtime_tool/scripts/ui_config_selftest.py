#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import sys


TOOL_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = TOOL_ROOT.parent
BACKEND_ROOT = TOOL_ROOT / "codex-skill-runtime-ui" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from ui_config import (  # noqa: E402
    RuntimePaths,
    configured_services,
    load_env,
    model_config_from_env,
    model_config_updates_from_payload,
    portable_path,
    runtime_env_exports,
    runtime_env_paths,
    service_by_id,
    state_root_from_env,
    write_env_updates,
)


def main() -> int:
    paths = RuntimePaths(tool_root=TOOL_ROOT, workspace_root=WORKSPACE_ROOT)
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

    with tempfile.TemporaryDirectory(prefix="ui-config-selftest-") as raw_tmp:
        tmp = Path(raw_tmp)
        env_path = tmp / "skill-runtime.env"
        key_path = tmp / "api-key.json"
        key_path.write_text('{"OPENAI_API_KEY":"test"}\n', encoding="utf-8")
        env_path.write_text(
            "\n".join(
                [
                    f"SKILL_RUNTIME_ROOT={tmp / 'root'}",
                    "SKILL_RUNTIME_TARGET_WORKSPACE=${SKILL_RUNTIME_ROOT}/workspace",
                    f"SKILL_RUNTIME_STATE_ROOT={tmp / 'state'}",
                    f"CODEX_API_KEY_FILE={key_path}",
                    "SKILL_RUNTIME_MODEL=gpt-test",
                    "CODEX_PROVIDER=OpenAI",
                    "CODEX_BASE_URL=https://example.invalid",
                    "CODEX_WIRE_API=responses",
                    "CODEX_REQUIRES_OPENAI_AUTH=true",
                    'CODEX_CONFIG=["review_model=\\"gpt-review\\"","model_reasoning_effort=\\"low\\"","model_context_window=128000","model_auto_compact_token_limit=96000","disable_response_storage=true"]',
                    'SKILL_RUNTIME_SERVICES_JSON={"services":[{"id":"art","label":"Art","endpoint":"http://127.0.0.1:7860","start_cmd":"echo art"}]}',
                    "SKILL_RUNTIME_SERVICE_AUDIO_ENDPOINT=http://127.0.0.1:9000",
                    "SKILL_RUNTIME_ENV_SAMPLE=visible",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        values = load_env(env_path, paths=paths)
        check(values["SKILL_RUNTIME_TARGET_WORKSPACE"].endswith("/root/workspace"), "env-expands-prior-values")
        check(values["SKILL_RUNTIME_MODEL"] == "gpt-test", "env-loads-model")

        model = model_config_from_env(values, env_path, paths=paths)
        config = model["config"]
        check(config["model"] == "gpt-test", "model-config-model")
        check(config["provider"] == "OpenAI", "model-config-provider")
        check(config["api_key_file_exists"] is True, "model-config-api-key-file-exists")
        check(config["context_window"] == 128000, "model-config-context-window")

        services = configured_services(values, paths=paths)
        ids = {str(item["id"]) for item in services}
        check({"art", "audio"}.issubset(ids), "services-json-and-env")
        check(service_by_id(values, "audio", paths=paths) is not None, "service-by-id")

        exports = runtime_env_exports(values, paths=paths)
        check(exports["SAMPLE"] == "visible", "runtime-env-export-prefix")

        state_root = state_root_from_env(env_path, paths=paths)
        check(state_root == (tmp / "state").resolve(), "state-root-from-env")

        resolved = runtime_env_paths(env_path, paths=paths)
        check(resolved["target_workspace"] == (tmp / "root" / "workspace").resolve(), "runtime-env-target-workspace")

        fallback = tmp / "fallback"
        check(portable_path("E:\\foreign\\path", paths=paths, fallback=fallback) == fallback.resolve(), "foreign-windows-path-fallback")

        updates = model_config_updates_from_payload(
            {
                "model": "gpt-next",
                "provider": "OpenAI",
                "base_url": "https://proxy.example",
                "wire_api": "responses",
                "requires_openai_auth": True,
                "review_model": "gpt-review-next",
                "context_window": 256000,
                "auto_compact_token_limit": 200000,
            },
            values,
        )
        check(updates["SKILL_RUNTIME_MODEL"] == "gpt-next", "model-updates-model")
        check(json.loads(updates["CODEX_CONFIG"])[0] == 'review_model="gpt-review-next"', "model-updates-codex-config")

        write_env_updates(env_path, updates, delete_when_empty={"SKILL_RUNTIME_CODEX_LOCAL_PROVIDER"})
        rewritten = load_env(env_path, paths=paths)
        check(rewritten["SKILL_RUNTIME_MODEL"] == "gpt-next", "write-env-updates-existing")
        check(rewritten["CODEX_BASE_URL"] == "https://proxy.example", "write-env-updates-base-url")

    print(f"SELFTEST_SUMMARY total={total} failed={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
