from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mcp import discover_mcp_servers
from .mcp_oauth import complete_oauth_authorization, start_oauth_authorization
from .runtime import RuntimeResult
from .selftest import SelfTester
from .universal_cli import _config_from_args, _runtime_from_config


def main(argv: list[str]) -> int:
    if argv and argv[0].startswith("/"):
        argv = ["run", *argv]

    parser = argparse.ArgumentParser(
        prog="codex-skill-runtime-core",
        description="Run Claude Code skills and agents on top of Codex CLI.",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Skill repository root containing .claude/ or compatible skill layout.",
    )
    parser.add_argument("--runtime-env", default=None, help="Load runtime settings from a .env-style file and isolate Codex config.")
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
    parser.add_argument("--qa", choices=["auto", "off", "required"], default="auto", help="Required QA gate mode.")
    parser.add_argument("--godot", default=None, help="Godot executable or directory.")
    parser.add_argument("--strict-tools", action="store_true", help="Run slash commands through runtime-owned structured tool actions.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum strict action-loop steps.")
    parser.add_argument("--add-dir", action="append", default=[], help="Additional directory to expose for skill/config discovery and Codex execution.")
    parser.add_argument("--output-style", default=None, help="Claude Code compatible output style hint for prompt construction.")
    parser.add_argument("--system-prompt", default=None, help="Override runtime system prompt text or @file path.")
    parser.add_argument("--append-system-prompt", default=None, help="Append runtime system prompt text or @file path.")

    sub = parser.add_subparsers(dest="command_name", required=True)

    sub.add_parser("inspect", help="List discovered skills, agents, and runtime context.")

    run = sub.add_parser("run", help="Run a slash command from .claude/skills.")
    run.add_argument("invocation", nargs=argparse.REMAINDER, help="Slash command and arguments, e.g. /prototype idea --path engine.")

    agent = sub.add_parser("agent", help="Run a single .claude/agents agent directly.")
    agent.add_argument("agent_name")
    agent.add_argument("prompt", nargs=argparse.REMAINDER)

    resume = sub.add_parser("resume", help="Resume from a recorded runtime transcript/session.")
    resume.add_argument("session")
    resume.add_argument("prompt", nargs=argparse.REMAINDER)

    answer = sub.add_parser("answer", help="Answer a pending runtime question and resume.")
    answer.add_argument("session")
    answer.add_argument("answer", nargs=argparse.REMAINDER)

    mcp_auth = sub.add_parser("mcp-auth", help="Start or complete OAuth for a configured HTTP/SSE MCP server.")
    mcp_auth.add_argument("server")
    mcp_auth.add_argument("--callback-url", default=None, help="Full OAuth callback URL containing code and state.")
    mcp_auth.add_argument("--code", default=None, help="Authorization code if callback URL is unavailable.")

    strict_smoke = sub.add_parser("strict-smoke", help="Run a minimal live strict action-loop smoke test.")
    strict_smoke.add_argument("read_path", nargs="?", default="README.md")

    selftest = sub.add_parser("selftest", help="Run runtime self-tests.")
    selftest.add_argument("--godot-project", default=None, help="Optional Godot project to include in self-test.")
    selftest.add_argument("--live-qa-target", default=None, help="Optional project path for a real Codex qa-tester self-test.")
    selftest.add_argument("--live-strict-target", default=None, help="Optional file path for a real strict action-loop self-test.")

    args = parser.parse_args(argv)

    config = _config_from_args(args, argv=argv)
    runtime = _runtime_from_config(config)

    if args.command_name == "inspect":
        print(json.dumps(runtime.inspect(), ensure_ascii=False, indent=2))
        return 0

    if args.command_name == "run":
        if not args.invocation:
            parser.error("run requires a slash command, e.g. run /prototype ...")
        command = args.invocation[0]
        if not command.startswith("/"):
            parser.error("first run argument must be a slash command such as /prototype")
        if args.strict_tools:
            result = runtime.run_strict_skill(command, " ".join(args.invocation[1:]), max_steps=args.max_steps)
        else:
            result = runtime.run_skill(command, " ".join(args.invocation[1:]))
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
            parser.error("answer requires text, e.g. answer <session> \"choose option A\"")
        result = runtime.answer_question(args.session, " ".join(args.answer))
        _print_result(result)
        return result.exit_code

    if args.command_name == "mcp-auth":
        server = _find_mcp_server(config.root, args.server)
        if args.callback_url or args.code:
            record = complete_oauth_authorization(
                project_root=config.root,
                server_name=server.name,
                config=server.config,
                code=args.code,
                callback_url=args.callback_url,
            )
            print(json.dumps({"status": "authenticated", "server": server.name, "key": record.key, "expires_at": record.expires_at}, ensure_ascii=False, indent=2))
            return 0
        result = start_oauth_authorization(
            project_root=config.root,
            server_name=server.name,
            config=server.config,
            plugin_root=server.plugin_root,
            server_url=str(server.config.get("url") or server.config.get("uri") or ""),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"auth_url", "authenticated"} else 2

    if args.command_name == "strict-smoke":
        result = runtime.run_strict_smoke(args.read_path, max_steps=config.max_steps)
        _print_result(result)
        return result.exit_code

    if args.command_name == "selftest":
        tester = SelfTester(
            project_root=config.root,
            codex_executable=config.codex,
            model=config.model,
            godot=config.godot,
            godot_project=Path(args.godot_project) if args.godot_project else None,
            live_qa_target=Path(args.live_qa_target) if args.live_qa_target else None,
            live_strict_target=args.live_strict_target,
        )
        return tester.run_all()

    parser.error(f"Unknown command: {args.command_name}")
    return 2


def _print_result(result: RuntimeResult) -> None:
    print(f"session: {result.session.dir}")
    if result.primary is not None:
        print(f"primary: {result.primary.label} exit={result.primary.returncode}")
        print(f"primary_last_message: {result.primary.last_message_path}")
    for task in result.tasks:
        print(f"task: {task.label} exit={task.returncode} last_message={task.last_message_path}")
    for gate in result.gates:
        print(f"gate: {gate.name} {gate.status} - {gate.reason}")


def _find_mcp_server(project_root: Path, name: str):
    servers = discover_mcp_servers(project_root)
    for server in servers:
        if name == server.name or name in server.aliases:
            return server
    available = ", ".join(sorted(server.name for server in servers))
    raise SystemExit(f"MCP server not found: {name}. Available: {available}")
