from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from wikiskill.storage import atomic_text

from .config import Config
from .mcp import call, serve
from .runtime import Runtime


def install_skill(directory: Path) -> str:
    destination = directory.expanduser().resolve() / "wikiskill" / "SKILL.md"
    content = files("wikiskill.live").joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    if destination.exists() and destination.read_text() != content:
        raise ValueError(f"Existing skill differs; inspect before replacing: {destination}")
    atomic_text(destination, content)
    return str(destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description="WikiSkill Codex business integration")
    parser.add_argument("--root", default="~/.wikiskill", help="User configuration and data directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Write the current default configuration")
    install = commands.add_parser("install-skill", help="Install the business collection skill")
    install.add_argument("--directory", type=Path, default=Path("~/.codex/skills"))
    commands.add_parser("mcp", help="Serve MCP over stdio")
    worker = commands.add_parser("worker", help="Run the persistent threshold worker")
    worker.add_argument("--once", action="store_true", help="Drain eligible jobs and exit")
    commands.add_parser("start", help="Start a detached worker")
    web = commands.add_parser("web", help="Serve the local read-only WebUI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    status = commands.add_parser("status", help="Query counts and job status")
    status.add_argument("--project")
    invoke = commands.add_parser("call", help="Call any MCP operation using a JSON object from stdin")
    invoke.add_argument("tool")
    args = parser.parse_args(argv)
    try:
        if args.command == "web":
            from .web import serve_web
            serve_web(args.root, args.host, args.port)
            return 0
        config = Config.load(args.root)
        runtime = Runtime(config)
        if args.command == "init":
            result = {"config": str(config.root / "config.json"), "mcp_command":
                      [sys.executable, "-m", "wikiskill.live.cli", "--root", str(config.root), "mcp"]}
        elif args.command == "install-skill":
            result = {"skill": install_skill(args.directory)}
        elif args.command == "mcp":
            runtime.wake()
            serve(config)
            return 0
        elif args.command == "worker":
            runtime.worker(args.once)
            return 0
        elif args.command == "start":
            result = runtime.wake()
        elif args.command == "status":
            result = call(runtime, "wikiskill_status", {"project": args.project} if args.project else {})
        else:
            result = call(runtime, args.tool, json.load(sys.stdin))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
