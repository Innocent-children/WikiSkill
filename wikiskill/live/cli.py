from __future__ import annotations

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

from wikiskill.storage import atomic_text

from .config import Config


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
    doctor = commands.add_parser("doctor", help="Diagnose the local environment without changing data",
        description="Read-only local checks; no processes, model calls or network requests. "
                    "Exit 0 with no blocking problems, 1 otherwise. Configuration values stay private.")
    doctor.add_argument("--json", action="store_true", help="Print structured diagnostic results")
    init = commands.add_parser("init", help="Write the current default configuration")
    init.add_argument("--reset-settings", action="store_true", help="Replace settings with current defaults; preserve all data")
    install = commands.add_parser("install-skill", help="Install the business collection skill")
    install.add_argument("--directory", type=Path, default=Path("~/.codex/skills"))
    commands.add_parser("mcp", help="Serve MCP over stdio")
    worker = commands.add_parser("worker", help="Run the persistent threshold worker")
    worker.add_argument("--once", action="store_true", help="Drain eligible jobs and exit")
    collector = commands.add_parser("collector", help="Incrementally preserve Codex transcripts")
    collector.add_argument("--once", action="store_true")
    commands.add_parser("start", help="Start a detached worker")
    web = commands.add_parser("web", help="Serve the local knowledge management WebUI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    status = commands.add_parser("status", help="Query counts and job status")
    status.add_argument("--project")
    invoke = commands.add_parser("call", help="Call any MCP operation using a JSON object from stdin")
    invoke.add_argument("tool")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        from .doctor import diagnose, format_report
        report = diagnose(args.root)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
        return 0 if report["ok"] else 1
    try:
        if args.command == "web":
            from .web import serve_web
            serve_web(args.root, args.host, args.port)
            return 0
        if args.command == "init" and args.reset_settings:
            from dataclasses import asdict
            defaults = Config(Path(args.root))
            values = asdict(defaults)
            values.pop("root")
            atomic_text(defaults.root / "config.json", json.dumps(values, ensure_ascii=False, indent=2) + "\n")
            (defaults.root / "config.json").chmod(0o600)
        config = Config.load(args.root)
        from .mcp import call, serve
        from .runtime import Runtime
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
        elif args.command == "collector":
            from .capture import Collector
            Collector(config).run(args.once)
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
