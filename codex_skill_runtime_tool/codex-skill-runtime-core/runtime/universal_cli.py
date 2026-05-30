from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .codex_cli import CodexCLI
from .mcp import discover_mcp_servers
from .mcp_oauth import complete_oauth_authorization, start_oauth_authorization
from .plugins import set_plugin_enabled
from .runtime import CodexSkillRuntime, RuntimeResult
from .selftest import SelfTester


@dataclass(frozen=True)
class RuntimeConfig:
    root: Path
    target_workspace: Path | None = None
    skill_repos: tuple[Path, ...] = ()
    runtime_env_file: Path | None = None
    codex: str = "codex"
    model: str | None = None
    codex_profile: str | None = None
    codex_env: dict[str, str] | None = None
    codex_config: tuple[str, ...] = ()
    isolated_codex_home: Path | None = None
    codex_config_path: Path | None = None
    codex_auth_path: Path | None = None
    runtime_state_root: Path | None = None
    dry_run: bool = False
    assume_yes: bool = False
    qa: str = "auto"
    strict_tools: bool = True
    strict_schema: bool = True
    max_steps: int = 8
    add_dirs: tuple[Path, ...] = ()
    output_style: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None


def main(argv: list[str]) -> int:
    if argv and argv[0].startswith("/"):
        argv = ["run", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args, argv=argv)

    if args.command_name == "ui":
        return _interactive_loop(config)

    runtime = _runtime_from_config(config)
    return _dispatch_noninteractive(args, config, runtime, parser)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-runtime",
        description="Load and run Claude Code skills, commands, agents, hooks, and MCP tools on top of Codex CLI.",
    )
    parser.add_argument(
        "--root",
        default=str(Path.cwd()),
        help=(
            "Legacy root. If --target-workspace is omitted this is also the execution workspace; "
            "if --skill-repo/SKILL_RUNTIME_SKILL_REPOS is omitted it is also a skill repository."
        ),
    )
    parser.add_argument("--target-workspace", default=None, help="Workspace where Codex executes and where write tools are allowed.")
    parser.add_argument("--skill-repo", action="append", default=[], help="Skill/plugin repository to load. Repeatable.")
    parser.add_argument("--runtime-env", default=None, help="Load skill-runtime settings from a .env-style file and isolate Codex config.")
    parser.add_argument("--runtime-state-root", default=None, help="Directory for runtime sessions, memory, MCP tokens, bridge, voice, and IDE state.")
    parser.add_argument("--codex", default="codex", help="Path to codex executable.")
    parser.add_argument("--model", default=None, help="Codex model override.")
    parser.add_argument("--codex-profile", default=None, help="Codex config.toml profile to pass through to codex CLI.")
    parser.add_argument("--codex-config", action="append", default=[], help="Raw codex --config key=value override. Repeatable.")
    parser.add_argument("--codex-env", action="append", default=[], help="Environment variable for codex child process, KEY=VALUE. Repeatable.")
    parser.add_argument("--codex-env-file", action="append", default=[], help="Load codex child-process environment variables from a .env file.")
    parser.add_argument("--codex-api-key", default=None, help="API key value or @file. Injected as OPENAI_API_KEY for codex child process.")
    parser.add_argument("--codex-api-key-file", default=None, help="File containing API key. Injected as OPENAI_API_KEY for codex child process.")
    parser.add_argument("--codex-base-url", default=None, help="OpenAI-compatible Codex API proxy base URL.")
    parser.add_argument("--codex-provider", default=None, help="Provider name for --codex-base-url config injection.")
    parser.add_argument("--codex-wire-api", default="responses", help="Provider wire_api when --codex-base-url is set. Default: responses.")
    parser.add_argument("--codex-requires-openai-auth", choices=["true", "false"], default="true", help="Provider requires_openai_auth when --codex-base-url is set.")
    parser.add_argument("--codex-http-proxy", default=None, help="HTTP proxy URL for codex child process.")
    parser.add_argument("--codex-https-proxy", default=None, help="HTTPS proxy URL for codex child process.")
    parser.add_argument("--codex-home", default=None, help="CODEX_HOME for an isolated codex config/auth directory.")
    parser.add_argument("--dry-run", action="store_true", help="Prepare prompts without calling Codex.")
    parser.add_argument("--assume-yes", action="store_true", help="Allow runtime prompts to proceed without approval pauses.")
    parser.add_argument("--qa", choices=["auto", "off", "required"], default="auto", help="QA gate mode.")
    parser.add_argument("--strict-tools", dest="strict_tools", action="store_true", default=True, help="Run slash commands through runtime-owned structured tool actions. Default: on.")
    parser.add_argument("--no-strict-tools", dest="strict_tools", action="store_false", help="Run slash commands as a single Codex prompt without structured tool mediation.")
    parser.add_argument("--strict-schema", dest="strict_schema", action="store_true", default=True, help="Use Codex --output-schema for strict action-loop steps. Default: on.")
    parser.add_argument("--no-strict-schema", dest="strict_schema", action="store_false", help="Use prompt-only raw JSON for strict action-loop steps.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum strict action-loop steps.")
    parser.add_argument("--add-dir", action="append", default=[], help="Additional directory to expose for skill/config discovery and Codex execution.")
    parser.add_argument("--output-style", default=None, help="Claude Code compatible output style hint for prompt construction.")
    parser.add_argument("--system-prompt", default=None, help="Override runtime system prompt text or @file path.")
    parser.add_argument("--append-system-prompt", default=None, help="Append runtime system prompt text or @file path.")

    sub = parser.add_subparsers(dest="command_name", required=True)
    sub.add_parser("ui", help="Open an interactive terminal interface.")
    sub.add_parser("inspect", help="Inspect the loaded skill repository.")
    sub.add_parser("skills", help="List discovered skills and slash commands.")
    sub.add_parser("agents", help="List discovered agents.")
    sub.add_parser("plugins", help="List discovered local plugins and enablement state.")

    plugin = sub.add_parser("plugin", help="Enable or disable a local plugin by name.")
    plugin.add_argument("operation", choices=["enable", "disable"])
    plugin.add_argument("name")
    plugin.add_argument("--plugin-root", dest="plugin_root", default=None, help="Optional exact plugin root path.")

    run = sub.add_parser("run", help="Run a slash command from the loaded skill repository.")
    run.add_argument("invocation", nargs=argparse.REMAINDER, help="Slash command and arguments, e.g. /namespace:skill task details.")

    agent = sub.add_parser("agent", help="Run one loaded agent directly.")
    agent.add_argument("agent_name")
    agent.add_argument("prompt", nargs=argparse.REMAINDER)

    resume = sub.add_parser("resume", help="Resume from a recorded runtime transcript/session.")
    resume.add_argument("session")
    resume.add_argument("prompt", nargs=argparse.REMAINDER)

    answer = sub.add_parser("answer", help="Answer a pending runtime question and resume.")
    answer.add_argument("session")
    answer.add_argument("answer", nargs=argparse.REMAINDER)

    mcp_auth = sub.add_parser("mcp-auth", help="Start or complete OAuth for a configured HTTP/SSE/WebSocket MCP server.")
    mcp_auth.add_argument("server")
    mcp_auth.add_argument("--callback-url", default=None, help="Full OAuth callback URL containing code and state.")
    mcp_auth.add_argument("--code", default=None, help="Authorization code if callback URL is unavailable.")

    strict_smoke = sub.add_parser("strict-smoke", help="Run a minimal live strict action-loop smoke test.")
    strict_smoke.add_argument("read_path", nargs="?", default="README.md")

    selftest = sub.add_parser("selftest", help="Run runtime self-tests against the loaded repository.")
    selftest.add_argument("--live-qa-target", default=None, help="Optional project path for a real Codex qa-tester self-test.")
    selftest.add_argument("--live-strict-target", default=None, help="Optional file path for a real strict action-loop self-test.")
    return parser


def _config_from_args(args: argparse.Namespace, *, argv: list[str] | None = None) -> RuntimeConfig:
    explicit = _explicit_options(argv or [])
    runtime_env_file = Path(args.runtime_env).expanduser().resolve() if getattr(args, "runtime_env", None) else None
    runtime_env = _read_env_file(runtime_env_file) if runtime_env_file else {}
    _apply_runtime_process_env(runtime_env)

    root = Path(
        _effective_text(args, "root", runtime_env, ("SKILL_RUNTIME_ROOT",), explicit, ("--root",))
    ).expanduser().resolve()
    target_workspace_value = _effective_optional_text(
        args,
        "target_workspace",
        runtime_env,
        ("SKILL_RUNTIME_TARGET_WORKSPACE", "SKILL_RUNTIME_WORKSPACE"),
        explicit,
        ("--target-workspace",),
    )
    target_workspace = Path(target_workspace_value).expanduser().resolve() if target_workspace_value else root
    add_dirs = tuple(_effective_add_dirs(args, runtime_env))
    skill_repos = tuple(_effective_skill_repos(args, runtime_env, root=root, add_dirs=add_dirs))
    codex = _effective_text(
        args,
        "codex",
        runtime_env,
        ("SKILL_RUNTIME_CODEX_EXECUTABLE", "CODEX_EXECUTABLE"),
        explicit,
        ("--codex",),
    )
    model = _effective_optional_text(
        args,
        "model",
        runtime_env,
        ("SKILL_RUNTIME_MODEL", "CODEX_MODEL"),
        explicit,
        ("--model",),
    )
    codex_profile = _effective_optional_text(
        args,
        "codex_profile",
        runtime_env,
        ("SKILL_RUNTIME_CODEX_PROFILE", "CODEX_PROFILE"),
        explicit,
        ("--codex-profile",),
    )
    codex_env = _codex_env_from_args(args, runtime_env=runtime_env, explicit=explicit, root=target_workspace, runtime_env_file=runtime_env_file)
    codex_config = _codex_config_from_args(args, runtime_env=runtime_env, explicit=explicit)
    runtime_state_root = _effective_optional_text(
        args,
        "runtime_state_root",
        runtime_env,
        ("SKILL_RUNTIME_STATE_ROOT",),
        explicit,
        ("--runtime-state-root",),
    )
    if not runtime_state_root and runtime_env_file is not None:
        runtime_state_root = str(_runtime_app_root() / ".skill-runtime" / "state")

    config = RuntimeConfig(
        root=root,
        target_workspace=target_workspace,
        skill_repos=skill_repos,
        runtime_env_file=runtime_env_file,
        codex=codex,
        model=model,
        codex_profile=codex_profile,
        codex_env=codex_env,
        codex_config=tuple(codex_config),
        dry_run=_effective_bool(args, "dry_run", runtime_env, ("SKILL_RUNTIME_DRY_RUN",), explicit, ("--dry-run",)),
        assume_yes=_effective_bool(args, "assume_yes", runtime_env, ("SKILL_RUNTIME_ASSUME_YES",), explicit, ("--assume-yes",)),
        qa=_effective_choice(args, "qa", runtime_env, ("SKILL_RUNTIME_QA",), explicit, ("--qa",), {"auto", "off", "required"}),
        strict_tools=_effective_bool(
            args,
            "strict_tools",
            runtime_env,
            ("SKILL_RUNTIME_STRICT_TOOLS",),
            explicit,
            ("--strict-tools", "--no-strict-tools"),
        ),
        strict_schema=_effective_bool(
            args,
            "strict_schema",
            runtime_env,
            ("SKILL_RUNTIME_STRICT_SCHEMA",),
            explicit,
            ("--strict-schema", "--no-strict-schema"),
        ),
        max_steps=_effective_int(args, "max_steps", runtime_env, ("SKILL_RUNTIME_MAX_STEPS",), explicit, ("--max-steps",)),
        add_dirs=add_dirs,
        output_style=_effective_optional_text(args, "output_style", runtime_env, ("SKILL_RUNTIME_OUTPUT_STYLE",), explicit, ("--output-style",)),
        system_prompt=_effective_optional_text(args, "system_prompt", runtime_env, ("SKILL_RUNTIME_SYSTEM_PROMPT",), explicit, ("--system-prompt",)),
        append_system_prompt=_effective_optional_text(args, "append_system_prompt", runtime_env, ("SKILL_RUNTIME_APPEND_SYSTEM_PROMPT",), explicit, ("--append-system-prompt",)),
        runtime_state_root=Path(runtime_state_root).expanduser().resolve() if runtime_state_root else None,
    )
    return _prepare_runtime_state_root(_prepare_isolated_codex_home(config))


def _runtime_from_config(config: RuntimeConfig) -> CodexSkillRuntime:
    config = _prepare_runtime_state_root(_prepare_isolated_codex_home(config))
    target_workspace = (config.target_workspace or config.root).resolve()
    skill_repos = tuple(config.skill_repos or (config.root, *config.add_dirs))
    codex_add_dirs = _unique_paths([*config.add_dirs, *skill_repos])
    return CodexSkillRuntime(
        project_root=target_workspace,
        codex=CodexCLI(
            executable=config.codex,
            model=config.model,
            add_dirs=codex_add_dirs,
            env=config.codex_env or {},
            config_overrides=config.codex_config,
            profile=config.codex_profile,
        ),
        dry_run=config.dry_run,
        assume_yes=config.assume_yes,
        qa_mode=config.qa,
        additional_dirs=list(skill_repos),
        output_style=config.output_style,
        system_prompt=config.system_prompt,
        append_system_prompt=config.append_system_prompt,
        strict_schema=config.strict_schema,
    )


def _codex_env_from_args(
    args: argparse.Namespace,
    *,
    runtime_env: dict[str, str] | None = None,
    explicit: set[str] | None = None,
    root: Path | None = None,
    runtime_env_file: Path | None = None,
) -> dict[str, str]:
    runtime_env = dict(runtime_env or {})
    explicit = set(explicit or set())
    root = root or Path(getattr(args, "root", Path.cwd())).expanduser().resolve()
    env: dict[str, str] = {}
    for env_file in _split_list(_first_env(runtime_env, ("SKILL_RUNTIME_CODEX_ENV_FILE", "CODEX_ENV_FILE"))):
        env.update(_read_env_file(Path(env_file).expanduser()))
    for key, value in runtime_env.items():
        if key.startswith("CODEX_ENV_") and len(key) > len("CODEX_ENV_"):
            env[key.removeprefix("CODEX_ENV_")] = value

    api_key = _first_env(runtime_env, ("SKILL_RUNTIME_CODEX_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY"))
    api_key_file = _first_env(runtime_env, ("SKILL_RUNTIME_CODEX_API_KEY_FILE", "CODEX_API_KEY_FILE", "OPENAI_API_KEY_FILE"))
    if api_key_file:
        api_key = _read_secret_file(Path(api_key_file).expanduser())
    if api_key:
        env["OPENAI_API_KEY"] = api_key

    http_proxy = _first_env(runtime_env, ("SKILL_RUNTIME_CODEX_HTTP_PROXY", "CODEX_HTTP_PROXY", "HTTP_PROXY"))
    if http_proxy:
        env["HTTP_PROXY"] = http_proxy
        env["http_proxy"] = http_proxy
    https_proxy = _first_env(runtime_env, ("SKILL_RUNTIME_CODEX_HTTPS_PROXY", "CODEX_HTTPS_PROXY", "HTTPS_PROXY"))
    if https_proxy:
        env["HTTPS_PROXY"] = https_proxy
        env["https_proxy"] = https_proxy

    for env_file in args.codex_env_file:
        env.update(_read_env_file(Path(env_file).expanduser()))
    for item in args.codex_env:
        key, value = _parse_key_value(item, option="--codex-env")
        env[key] = value
    if args.codex_api_key_file:
        env["OPENAI_API_KEY"] = _read_secret_file(Path(args.codex_api_key_file).expanduser())
    if args.codex_api_key:
        env["OPENAI_API_KEY"] = _read_value_or_file(args.codex_api_key)
    if args.codex_http_proxy:
        env["HTTP_PROXY"] = args.codex_http_proxy
        env["http_proxy"] = args.codex_http_proxy
    if args.codex_https_proxy:
        env["HTTPS_PROXY"] = args.codex_https_proxy
        env["https_proxy"] = args.codex_https_proxy

    codex_home = _first_env(runtime_env, ("SKILL_RUNTIME_CODEX_HOME", "CODEX_HOME"))
    if args.codex_home:
        codex_home = args.codex_home
    if not codex_home and runtime_env_file is not None:
        codex_home = str(_runtime_app_root() / ".skill-runtime" / "codex-home")
    if codex_home:
        env["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
    return env


def _apply_runtime_process_env(runtime_env: dict[str, str]) -> None:
    for key, value in runtime_env.items():
        if key.startswith("SKILL_RUNTIME_ENV_") and len(key) > len("SKILL_RUNTIME_ENV_"):
            os.environ[key.removeprefix("SKILL_RUNTIME_ENV_")] = value
        elif key in {
            "SKILL_RUNTIME_NAMESPACES",
            "CODEX_SKILL_RUNTIME_NAMESPACES",
            "SKILL_RUNTIME_CAPABILITIES_JSON",
            "CODEX_SKILL_RUNTIME_CAPABILITIES_JSON",
            "SKILL_RUNTIME_MODEL_CONTEXT_WINDOW",
            "SKILL_RUNTIME_QA_AUTO_PATTERNS",
            "CODEX_SKILL_RUNTIME_QA_AUTO_PATTERNS",
            "CODEX_SKILL_RUNTIME_BARE",
        }:
            os.environ[key] = value
        elif key.startswith("SKILL_RUNTIME_CAPABILITY_") or key.startswith("CODEX_SKILL_RUNTIME_CAPABILITY_"):
            os.environ[key] = value


def _codex_config_from_args(
    args: argparse.Namespace,
    *,
    runtime_env: dict[str, str] | None = None,
    explicit: set[str] | None = None,
) -> list[str]:
    runtime_env = dict(runtime_env or {})
    explicit = set(explicit or set())
    values = _split_list(_first_env(runtime_env, ("SKILL_RUNTIME_CODEX_CONFIG", "CODEX_CONFIG")))

    base_url = _effective_optional_text(
        args,
        "codex_base_url",
        runtime_env,
        ("SKILL_RUNTIME_CODEX_BASE_URL", "CODEX_BASE_URL", "OPENAI_BASE_URL"),
        explicit,
        ("--codex-base-url",),
    )
    if base_url:
        provider = _effective_optional_text(
            args,
            "codex_provider",
            runtime_env,
            ("SKILL_RUNTIME_CODEX_PROVIDER", "CODEX_PROVIDER"),
            explicit,
            ("--codex-provider",),
        ) or "skill-runtime-proxy"
        wire_api = _effective_text(
            args,
            "codex_wire_api",
            runtime_env,
            ("SKILL_RUNTIME_CODEX_WIRE_API", "CODEX_WIRE_API"),
            explicit,
            ("--codex-wire-api",),
        )
        requires_auth = _effective_text(
            args,
            "codex_requires_openai_auth",
            runtime_env,
            ("SKILL_RUNTIME_CODEX_REQUIRES_OPENAI_AUTH", "CODEX_REQUIRES_OPENAI_AUTH"),
            explicit,
            ("--codex-requires-openai-auth",),
        )
        values.extend(
            [
                f"model_provider={_toml_string(provider)}",
                f"model_providers.{provider}.name={_toml_string(provider)}",
                f"model_providers.{provider}.base_url={_toml_string(base_url)}",
                f"model_providers.{provider}.wire_api={_toml_string(wire_api)}",
                f"model_providers.{provider}.requires_openai_auth={_toml_bool(requires_auth)}",
            ]
        )
    values.extend(str(item) for item in args.codex_config if str(item).strip())
    return values


def _read_env_file(path: Path) -> dict[str, str]:
    path = path.expanduser().resolve()
    builtins = _env_file_builtin_vars(path)
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    lookup = {**builtins, **os.environ, **env}
    return {key: _expand_env_value(value, lookup) for key, value in env.items()}


def _env_file_builtin_vars(path: Path) -> dict[str, str]:
    tool_root = _runtime_app_root()
    return {
        "SKILL_RUNTIME_ENV_DIR": str(path.parent),
        "SKILL_RUNTIME_TOOL_ROOT": str(tool_root),
        "SKILL_RUNTIME_WORKSPACE_ROOT": str(tool_root.parent),
    }


def _expand_env_value(value: str, lookup: dict[str, str]) -> str:
    result = value
    for _ in range(8):
        updated = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: lookup.get(match.group(1), match.group(0)), result)
        updated = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", lambda match: lookup.get(match.group(1), match.group(0)), updated)
        if updated == result:
            return updated
        result = updated
        lookup = {**lookup, **{"_": result}}
    return result


def _parse_key_value(value: str, *, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise SystemExit(f"{option} must be KEY=VALUE")
    key, raw = value.split("=", 1)
    key = key.strip()
    if not key:
        raise SystemExit(f"{option} must include a non-empty key")
    return key, _read_value_or_file(raw.strip())


def _read_value_or_file(value: str) -> str:
    if value.startswith("@"):
        return _read_secret_file(Path(value[1:]).expanduser())
    return value.lstrip("\ufeff")


def _read_secret_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff").strip()


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if _parse_bool_text(str(value), default=True) else "false"


def _toml_override_value(value: str) -> str:
    stripped = value.strip()
    lowered = stripped.lower()
    if not stripped:
        return '""'
    if stripped[0] in {'"', "'", "[", "{"}:
        return stripped
    if lowered in {"true", "false"}:
        return lowered
    if re.fullmatch(r"[+-]?\d+(?:_\d+)*", stripped):
        return stripped
    if re.fullmatch(r"[+-]?(?:\d+(?:_\d+)*)?\.\d+(?:[eE][+-]?\d+)?", stripped):
        return stripped
    return _toml_string(stripped)


def _explicit_options(argv: list[str]) -> set[str]:
    options: set[str] = set()
    for item in argv:
        if not item.startswith("-"):
            continue
        option = item.split("=", 1)[0]
        options.add(option)
    return options


def _first_env(env: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _effective_text(
    args: argparse.Namespace,
    attr: str,
    runtime_env: dict[str, str],
    env_names: tuple[str, ...],
    explicit: set[str],
    option_names: tuple[str, ...],
) -> str:
    if any(option in explicit for option in option_names):
        return str(getattr(args, attr))
    value = _first_env(runtime_env, env_names)
    if value is not None:
        return value
    return str(getattr(args, attr))


def _effective_optional_text(
    args: argparse.Namespace,
    attr: str,
    runtime_env: dict[str, str],
    env_names: tuple[str, ...],
    explicit: set[str],
    option_names: tuple[str, ...],
) -> str | None:
    if any(option in explicit for option in option_names):
        value = getattr(args, attr)
        return str(value) if value is not None and str(value).strip() else None
    value = _first_env(runtime_env, env_names)
    if value is not None:
        return value
    arg_value = getattr(args, attr)
    return str(arg_value) if arg_value is not None and str(arg_value).strip() else None


def _effective_bool(
    args: argparse.Namespace,
    attr: str,
    runtime_env: dict[str, str],
    env_names: tuple[str, ...],
    explicit: set[str],
    option_names: tuple[str, ...],
) -> bool:
    if any(option in explicit for option in option_names):
        return bool(getattr(args, attr))
    value = _first_env(runtime_env, env_names)
    if value is not None:
        return _parse_bool_text(value, default=bool(getattr(args, attr)))
    return bool(getattr(args, attr))


def _effective_int(
    args: argparse.Namespace,
    attr: str,
    runtime_env: dict[str, str],
    env_names: tuple[str, ...],
    explicit: set[str],
    option_names: tuple[str, ...],
) -> int:
    if any(option in explicit for option in option_names):
        return int(getattr(args, attr))
    value = _first_env(runtime_env, env_names)
    if value is None:
        return int(getattr(args, attr))
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{env_names[0]} must be an integer") from exc


def _effective_choice(
    args: argparse.Namespace,
    attr: str,
    runtime_env: dict[str, str],
    env_names: tuple[str, ...],
    explicit: set[str],
    option_names: tuple[str, ...],
    choices: set[str],
) -> str:
    value = _effective_text(args, attr, runtime_env, env_names, explicit, option_names)
    if value not in choices:
        raise SystemExit(f"{env_names[0]} must be one of: {', '.join(sorted(choices))}")
    return value


def _effective_add_dirs(args: argparse.Namespace, runtime_env: dict[str, str]) -> list[Path]:
    values = _split_list(_first_env(runtime_env, ("SKILL_RUNTIME_ADD_DIR", "SKILL_RUNTIME_ADD_DIRS")))
    values.extend(str(value) for value in getattr(args, "add_dir", []) if str(value).strip())
    return [Path(value).expanduser().resolve() for value in values]


def _effective_skill_repos(
    args: argparse.Namespace,
    runtime_env: dict[str, str],
    *,
    root: Path,
    add_dirs: tuple[Path, ...],
) -> list[Path]:
    values = _split_list(_first_env(runtime_env, ("SKILL_RUNTIME_SKILL_REPOS", "SKILL_RUNTIME_SKILL_REPOSITORIES")))
    values.extend(str(value) for value in getattr(args, "skill_repo", []) if str(value).strip())
    if values:
        return _unique_paths([*(Path(value).expanduser().resolve() for value in values), *add_dirs])
    return _unique_paths([root, *add_dirs])


def _split_list(value: str | None) -> list[str]:
    if value is None:
        return []
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON list: {stripped}") from exc
        if not isinstance(parsed, list):
            raise SystemExit(f"Expected JSON list: {stripped}")
        return [str(item).strip() for item in parsed if str(item).strip()]
    separator = "||" if "||" in stripped else ";"
    return [part.strip() for part in stripped.split(separator) if part.strip()]


def _unique_paths(paths) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _parse_bool_text(value: str, *, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _prepare_isolated_codex_home(config: RuntimeConfig) -> RuntimeConfig:
    env = dict(config.codex_env or {})
    home_value = env.get("CODEX_HOME")
    if not home_value:
        return config
    codex_home = Path(home_value).expanduser().resolve()
    env["CODEX_HOME"] = str(codex_home)
    codex_home.mkdir(parents=True, exist_ok=True)

    config_path = codex_home / "config.toml"
    config_path.write_text(_build_isolated_codex_config_toml(config), encoding="utf-8")

    auth_path = codex_home / "auth.json"
    if env.get("OPENAI_API_KEY"):
        auth_path.write_text(
            json.dumps({"OPENAI_API_KEY": env["OPENAI_API_KEY"]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return replace(
        config,
        codex_env=env,
        isolated_codex_home=codex_home,
        codex_config_path=config_path,
        codex_auth_path=auth_path if auth_path.exists() else None,
    )


def _prepare_runtime_state_root(config: RuntimeConfig) -> RuntimeConfig:
    if config.runtime_state_root is None:
        return config
    state_root = config.runtime_state_root.expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    os.environ["SKILL_RUNTIME_STATE_ROOT"] = str(state_root)
    return replace(config, runtime_state_root=state_root)


def _runtime_app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _build_isolated_codex_config_toml(config: RuntimeConfig) -> str:
    top_level: dict[str, str] = {
        "disable_response_storage": "true",
    }
    if config.model:
        top_level["model"] = _toml_string(config.model)
    sections: dict[str, dict[str, str]] = {}
    for override in config.codex_config:
        if "=" not in override:
            continue
        key, raw_value = override.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key or not raw_value:
            continue
        raw_value = _toml_override_value(raw_value)
        if "." not in key:
            top_level[key] = raw_value
            continue
        section_name, item_name = key.rsplit(".", 1)
        if section_name and item_name:
            sections.setdefault(section_name, {})[item_name] = raw_value

    lines = [
        "# Generated by Codex Skill Runtime.",
        "# This file belongs to the isolated CODEX_HOME for this skill runtime.",
        "",
    ]
    for key in sorted(top_level):
        lines.append(f"{key} = {top_level[key]}")
    for section_name in sorted(sections):
        lines.append("")
        lines.append(f"[{section_name}]")
        for key in sorted(sections[section_name]):
            lines.append(f"{key} = {sections[section_name][key]}")
    lines.append("")
    return "\n".join(lines)


def _dispatch_noninteractive(
    args: argparse.Namespace,
    config: RuntimeConfig,
    runtime: CodexSkillRuntime,
    parser: argparse.ArgumentParser,
) -> int:
    if args.command_name == "inspect":
        print(json.dumps(runtime.inspect(), ensure_ascii=False, indent=2))
        return 0

    if args.command_name == "skills":
        for skill in runtime.inspect()["skills"]:
            print(skill)
        return 0

    if args.command_name == "agents":
        for agent in runtime.inspect()["agents"]:
            print(agent)
        return 0

    if args.command_name == "plugins":
        print(json.dumps(runtime.inspect().get("plugins", []), ensure_ascii=False, indent=2))
        return 0

    if args.command_name == "plugin":
        state = set_plugin_enabled(
            config.target_workspace or config.root,
            name=args.name,
            root=args.plugin_root,
            enabled=args.operation == "enable",
        )
        print(json.dumps({"ok": True, "state": state}, ensure_ascii=False, indent=2))
        return 0

    if args.command_name == "run":
        if not args.invocation:
            parser.error("run requires a slash command, e.g. skill-runtime run /namespace:skill ...")
        result = _run_invocation(runtime, config, args.invocation)
        _print_result(result)
        return result.exit_code

    if args.command_name == "agent":
        result = runtime.run_agent(args.agent_name, " ".join(args.prompt))
        _print_result(result)
        return result.exit_code

    if args.command_name == "resume":
        result = runtime.resume_session(args.session, " ".join(args.prompt))
        _print_result(result)
        return result.exit_code

    if args.command_name == "answer":
        if not args.answer:
            parser.error("answer requires text, e.g. skill-runtime answer <session> \"choose option A\"")
        result = runtime.answer_question(args.session, " ".join(args.answer))
        _print_result(result)
        return result.exit_code

    if args.command_name == "mcp-auth":
        return _mcp_auth(config, args.server, callback_url=args.callback_url, code=args.code)

    if args.command_name == "strict-smoke":
        result = runtime.run_strict_smoke(args.read_path, max_steps=config.max_steps)
        _print_result(result)
        return result.exit_code

    if args.command_name == "selftest":
        skill_repos = list(config.skill_repos or (config.root, *config.add_dirs))
        loaded_root = skill_repos[0] if skill_repos else config.root
        tester = SelfTester(
            project_root=loaded_root,
            codex_executable=config.codex,
            model=config.model,
            live_qa_target=Path(args.live_qa_target) if args.live_qa_target else None,
            live_strict_target=args.live_strict_target,
            additional_dirs=_unique_paths([*skill_repos[1:], *config.add_dirs]),
        )
        return tester.run_all()

    parser.error(f"Unknown command: {args.command_name}")
    return 2


def _run_invocation(runtime: CodexSkillRuntime, config: RuntimeConfig, invocation: list[str]) -> RuntimeResult:
    command = invocation[0]
    if not command.startswith("/"):
        raise SystemExit("first run argument must be a slash command such as /namespace:skill")
    arguments = " ".join(invocation[1:])
    if config.strict_tools:
        return runtime.run_strict_skill(command, arguments, max_steps=config.max_steps)
    return runtime.run_skill(command, arguments)


def _mcp_auth(config: RuntimeConfig, server_name: str, *, callback_url: str | None, code: str | None) -> int:
    target_workspace = config.target_workspace or config.root
    server = _find_mcp_server(target_workspace, server_name, additional_dirs=list(config.skill_repos))
    if callback_url or code:
        record = complete_oauth_authorization(
            project_root=target_workspace,
            server_name=server.name,
            config=server.config,
            code=code,
            callback_url=callback_url,
        )
        print(json.dumps({"status": "authenticated", "server": server.name, "key": record.key, "expires_at": record.expires_at}, ensure_ascii=False, indent=2))
        return 0
    result = start_oauth_authorization(
        project_root=target_workspace,
        server_name=server.name,
        config=server.config,
        plugin_root=server.plugin_root,
        server_url=str(server.config.get("url") or server.config.get("uri") or ""),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"auth_url", "authenticated"} else 2


def _find_mcp_server(project_root: Path, name: str, *, additional_dirs: list[Path] | None = None):
    servers = discover_mcp_servers(project_root, additional_dirs=additional_dirs)
    for server in servers:
        if name == server.name or name in server.aliases:
            return server
    available = ", ".join(sorted(server.name for server in servers))
    raise SystemExit(f"MCP server not found: {name}. Available: {available}")


def _interactive_loop(initial: RuntimeConfig) -> int:
    config = initial
    print("Codex Skill Runtime")
    print("输入 help 查看命令；输入 exit 退出。")
    print(f"目标工作区: {config.target_workspace or config.root}")
    print(f"技能仓库: {', '.join(str(path) for path in (config.skill_repos or (config.root,)))}")
    while True:
        try:
            line = input("skill-runtime> ").lstrip("\ufeff").strip()
        except EOFError:
            print()
            return 0
        if not line:
            continue
        if line in {"exit", "quit", "q"}:
            return 0
        try:
            config = _handle_interactive_command(config, line)
        except SystemExit as exc:
            if exc.code not in {None, 0}:
                print(exc)
        except KeyboardInterrupt:
            print("已中断当前命令。")
        except Exception as exc:  # pragma: no cover - user-facing diagnostics.
            print(f"ERROR: {type(exc).__name__}: {exc}")


def _handle_interactive_command(config: RuntimeConfig, line: str) -> RuntimeConfig:
    command, rest = _split_first(line)
    handlers: dict[str, Callable[[RuntimeConfig, str], RuntimeConfig]] = {
        "help": _ui_help,
        "?": _ui_help,
        "status": _ui_status,
        "load": _ui_load,
        "root": _ui_root,
        "inspect": _ui_inspect,
        "skills": _ui_skills,
        "agents": _ui_agents,
        "run": _ui_run,
        "strict": _ui_strict_run,
        "agent": _ui_agent,
        "resume": _ui_resume,
        "answer": _ui_answer,
        "mcp-auth": _ui_mcp_auth,
        "strict-smoke": _ui_strict_smoke,
        "set": _ui_set,
    }
    if command.startswith("/"):
        return _ui_run(config, line)
    handler = handlers.get(command.lower())
    if handler is None:
        print(f"未知命令: {command}。输入 help 查看可用命令。")
        return config
    return handler(config, rest)


def _split_first(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped:
        return "", ""
    parts = stripped.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _split_args(text: str) -> list[str]:
    if not text.strip():
        return []
    return [part.strip("\"'") for part in shlex.split(text, posix=False)]


def _ui_help(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    print(
        "\n".join(
            [
                "可用命令:",
                "  load <path>                  追加加载一个 skill 仓库",
                "  status                       查看当前配置",
                "  inspect                      输出当前仓库的 skills / agents / context",
                "  skills [filter]              列出 skills 和 slash commands",
                "  agents [filter]              列出 agents",
                "  run /skill <args>            按当前 strict 设置运行 skill",
                "  /skill <args>                run 的快捷写法",
                "  strict /skill <args>         强制通过结构化工具循环运行",
                "  agent <name> <prompt>        直接运行某个 agent",
                "  resume <session> <prompt>    从 session 恢复",
                "  answer <session> <answer>    回答 pending question 并恢复",
                "  mcp-auth <server> [...]      配置远程 MCP OAuth",
                "  set strict on|off            是否默认使用 Claude-Code-like 工具循环",
                "  set strict-schema on|off     strict 模式是否使用 Codex --output-schema",
                "  set qa auto|off|required     设置 QA gate",
                "  set assume-yes on|off        设置自动回答 runtime pause",
                "  set dry-run on|off           只生成 prompt，不调用 Codex",
                "  set model <name|clear>       设置 Codex model override",
                "  set api-key <value|@file|clear>     Set OPENAI_API_KEY for codex child process",
                "  set base-url <url|clear>            Set a proxy/OpenAI-compatible base URL for codex",
                "  set provider <name|clear>           Set provider name for base-url config",
                "  set http-proxy <url|clear>          Set HTTP_PROXY/http_proxy for codex",
                "  set https-proxy <url|clear>         Set HTTPS_PROXY/https_proxy for codex",
                "  set env KEY=VALUE                   Set any codex child-process environment variable",
                "  set config KEY=VALUE                Add raw codex --config override",
                "  set profile <name|clear>            Set codex --profile",
                "  set workspace <path>         设置目标工作区",
                "  set max-steps <n>            设置 strict action-loop 最大轮数",
                "  exit                         退出",
            ]
        )
    )
    return config


def _ui_status(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    print(json.dumps(_config_to_json(_prepare_runtime_state_root(_prepare_isolated_codex_home(config))), ensure_ascii=False, indent=2))
    return config


def _ui_load(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    if not rest.strip():
        print("用法: load <skill-repo-path>")
        return config
    root = Path(rest.strip().strip("\"'")).expanduser().resolve()
    repos = _unique_paths([*(config.skill_repos or (config.root,)), root])
    next_config = replace(config, root=config.root, skill_repos=tuple(repos))
    runtime = _runtime_from_config(next_config)
    data = runtime.inspect()
    print(f"已加载: {root}")
    print(f"skills={len(data['skills'])} agents={len(data['agents'])}")
    return next_config


def _ui_root(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    print(config.target_workspace or config.root)
    return config


def _ui_inspect(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    print(json.dumps(_runtime_from_config(config).inspect(), ensure_ascii=False, indent=2))
    return config


def _ui_skills(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    names = _runtime_from_config(config).inspect()["skills"]
    _print_filtered(names, rest)
    return config


def _ui_agents(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    names = _runtime_from_config(config).inspect()["agents"]
    _print_filtered(names, rest)
    return config


def _ui_run(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    invocation = _split_args(rest)
    if not invocation:
        print("用法: run /skill <args>")
        return config
    result = _run_invocation(_runtime_from_config(config), config, invocation)
    _print_result(result)
    return config


def _ui_strict_run(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    invocation = _split_args(rest)
    if not invocation:
        print("用法: strict /skill <args>")
        return config
    strict_config = replace(config, strict_tools=True)
    result = _run_invocation(_runtime_from_config(strict_config), strict_config, invocation)
    _print_result(result)
    return config


def _ui_agent(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    agent_name, prompt = _split_first(rest)
    if not agent_name or not prompt:
        print("用法: agent <agent-name> <prompt>")
        return config
    result = _runtime_from_config(config).run_agent(agent_name, prompt)
    _print_result(result)
    return config


def _ui_resume(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    session, prompt = _split_first(rest)
    if not session:
        print("用法: resume <session-id-or-path> <prompt>")
        return config
    result = _runtime_from_config(config).resume_session(session, prompt)
    _print_result(result)
    return config


def _ui_answer(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    session, answer = _split_first(rest)
    if not session or not answer:
        print("用法: answer <session-id-or-path> <answer>")
        return config
    result = _runtime_from_config(config).answer_question(session, answer)
    _print_result(result)
    return config


def _ui_mcp_auth(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    args = _split_args(rest)
    if not args:
        print("用法: mcp-auth <server> [--callback-url <url>] [--code <code>]")
        return config
    server = args[0]
    callback_url = _option_value(args, "--callback-url")
    code = _option_value(args, "--code")
    _mcp_auth(config, server, callback_url=callback_url, code=code)
    return config


def _ui_strict_smoke(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    result = _runtime_from_config(config).run_strict_smoke(rest.strip() or "README.md", max_steps=config.max_steps)
    _print_result(result)
    return config


def _ui_set(config: RuntimeConfig, rest: str) -> RuntimeConfig:
    key, value = _split_first(rest)
    key = key.lower()
    value = value.strip()
    env = dict(config.codex_env or {})
    provider = _provider_from_config(config.codex_config) or "skill-runtime-proxy"
    if key == "strict":
        return replace(config, strict_tools=_parse_on_off(value, config.strict_tools))
    if key == "strict-schema":
        return replace(config, strict_schema=_parse_on_off(value, config.strict_schema))
    if key == "qa":
        if value not in {"auto", "off", "required"}:
            print("qa 只能是 auto、off 或 required")
            return config
        return replace(config, qa=value)
    if key == "assume-yes":
        return replace(config, assume_yes=_parse_on_off(value, config.assume_yes))
    if key == "dry-run":
        return replace(config, dry_run=_parse_on_off(value, config.dry_run))
    if key == "model":
        return replace(config, model=None if value.lower() == "clear" else value)
    if key == "profile":
        return replace(config, codex_profile=None if value.lower() == "clear" else value)
    if key == "workspace":
        if not value:
            print("用法: set workspace <path>")
            return config
        return replace(config, target_workspace=Path(value.strip("\"'")).expanduser().resolve())
    if key == "api-key":
        if value.lower() == "clear":
            env.pop("OPENAI_API_KEY", None)
        else:
            env["OPENAI_API_KEY"] = _read_value_or_file(value)
        return replace(config, codex_env=env)
    if key == "http-proxy":
        if value.lower() == "clear":
            env.pop("HTTP_PROXY", None)
            env.pop("http_proxy", None)
        else:
            env["HTTP_PROXY"] = value
            env["http_proxy"] = value
        return replace(config, codex_env=env)
    if key == "https-proxy":
        if value.lower() == "clear":
            env.pop("HTTPS_PROXY", None)
            env.pop("https_proxy", None)
        else:
            env["HTTPS_PROXY"] = value
            env["https_proxy"] = value
        return replace(config, codex_env=env)
    if key == "env":
        env_key, env_value = _parse_key_value(value, option="set env")
        env[env_key] = env_value
        return replace(config, codex_env=env)
    if key == "provider":
        if value.lower() == "clear":
            return replace(config, codex_config=_remove_config_prefix(config.codex_config, ["model_provider="]))
        updated = _replace_config_key(config.codex_config, "model_provider", _toml_string(value))
        return replace(config, codex_config=updated)
    if key == "base-url":
        if value.lower() == "clear":
            return replace(config, codex_config=_remove_config_prefix(config.codex_config, ["model_provider=", f"model_providers.{provider}."]))
        updated = _replace_codex_base_url_config(config.codex_config, provider=provider, base_url=value)
        return replace(config, codex_config=updated)
    if key == "config":
        if "=" not in value:
            print("set config requires KEY=VALUE")
            return config
        return replace(config, codex_config=(*config.codex_config, value))
    if key == "output-style":
        return replace(config, output_style=None if value.lower() == "clear" else value)
    if key == "max-steps":
        try:
            steps = int(value)
        except ValueError:
            print("max-steps 必须是整数")
            return config
        return replace(config, max_steps=max(1, steps))
    print("支持的 set 项: strict, strict-schema, qa, assume-yes, dry-run, model, api-key, base-url, provider, http-proxy, https-proxy, env, config, profile, output-style, max-steps")
    return config


def _parse_on_off(value: str, current: bool) -> bool:
    lowered = value.lower()
    if lowered in {"on", "true", "yes", "1"}:
        return True
    if lowered in {"off", "false", "no", "0"}:
        return False
    print("值必须是 on 或 off，保持原值。")
    return current


def _option_value(args: list[str], name: str) -> str | None:
    try:
        index = args.index(name)
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def _provider_from_config(values: tuple[str, ...]) -> str | None:
    prefix = "model_provider="
    for value in reversed(values):
        if value.startswith(prefix):
            raw = value.removeprefix(prefix).strip()
            return raw.strip('"').strip("'")
    return None


def _replace_codex_base_url_config(values: tuple[str, ...], *, provider: str, base_url: str) -> tuple[str, ...]:
    cleaned = _remove_config_prefix(values, ["model_provider=", f"model_providers.{provider}."])
    return (
        *cleaned,
        f"model_provider={_toml_string(provider)}",
        f"model_providers.{provider}.name={_toml_string(provider)}",
        f"model_providers.{provider}.base_url={_toml_string(base_url)}",
        'model_providers.%s.wire_api="responses"' % provider,
        f"model_providers.{provider}.requires_openai_auth=true",
    )


def _replace_config_key(values: tuple[str, ...], key: str, raw_value: str) -> tuple[str, ...]:
    prefix = f"{key}="
    return (*[value for value in values if not value.startswith(prefix)], f"{key}={raw_value}")


def _remove_config_prefix(values: tuple[str, ...], prefixes: list[str]) -> tuple[str, ...]:
    return tuple(value for value in values if not any(value.startswith(prefix) for prefix in prefixes))


def _print_filtered(values: object, filter_text: str) -> None:
    names = [str(value) for value in values] if isinstance(values, list) else []
    needle = filter_text.strip().lower()
    for name in names:
        if not needle or needle in name.lower():
            print(name)


def _config_to_json(config: RuntimeConfig) -> dict[str, object]:
    return {
        "root": str(config.root),
        "target_workspace": str(config.target_workspace) if config.target_workspace else None,
        "skill_repos": [str(path) for path in config.skill_repos],
        "runtime_env_file": str(config.runtime_env_file) if config.runtime_env_file else None,
        "codex": config.codex,
        "model": config.model,
        "codex_profile": config.codex_profile,
        "codex_env": _redacted_env(config.codex_env or {}),
        "codex_config": list(config.codex_config),
        "isolated_codex_home": str(config.isolated_codex_home) if config.isolated_codex_home else None,
        "codex_config_path": str(config.codex_config_path) if config.codex_config_path else None,
        "codex_auth_path": str(config.codex_auth_path) if config.codex_auth_path else None,
        "runtime_state_root": str(config.runtime_state_root) if config.runtime_state_root else None,
        "dry_run": config.dry_run,
        "assume_yes": config.assume_yes,
        "qa": config.qa,
        "strict_tools": config.strict_tools,
        "strict_schema": config.strict_schema,
        "max_steps": config.max_steps,
        "add_dirs": [str(path) for path in config.add_dirs],
        "output_style": config.output_style,
    }


def _redacted_env(values: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in values.items():
        redacted[key] = "[REDACTED]" if _looks_secret(key) else value
    return redacted


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ["key", "token", "secret", "password", "credential", "auth"])


def _print_result(result: RuntimeResult) -> None:
    print(f"session: {result.session.dir}")
    if result.primary is not None:
        print(f"primary: {result.primary.label} exit={result.primary.returncode}")
        print(f"primary_last_message: {result.primary.last_message_path}")
    for task in result.tasks:
        print(f"task: {task.label} exit={task.returncode} last_message={task.last_message_path}")
    for gate in result.gates:
        print(f"gate: {gate.name} {gate.status} - {gate.reason}")
