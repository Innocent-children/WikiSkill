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
    parser = argparse.ArgumentParser(description="Start WikiSkill and open its local control panel")
    parser.add_argument("--root", default="~/.wikiskill", help="User configuration and data directory")
    parser.add_argument("--no-open", action="store_true", help="Start without opening a browser")
    parser.add_argument("--port", type=int, default=8765, help="Preferred panel port; an occupied port uses a free port")
    commands = parser.add_subparsers(dest="command")
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
    commands.add_parser("start", help="Start or reuse the panel, collector and worker without a browser")
    commands.add_parser("stop", help="Stop the managed background services and preserve data")
    service = commands.add_parser("_service", help=argparse.SUPPRESS)
    service.add_argument("--port", type=int, default=8765)
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
        if args.command in {None, "start"}:
            import shlex
            import webbrowser
            from .services import ensure_services
            root = Path(args.root).expanduser().resolve()
            first = not (root / "config.json").exists()
            result = ensure_services(root, args.port)
            if args.command == "start":
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            url = result["url"] + ("/#/system" if first else "/#/manage")
            print(f"WikiSkill {'已在运行' if result['reused'] else '已启动'}\n\n控制面板  {url}")
            print("原文采集  运行中\n知识整理  运行中（自动转换按设置执行）")
            opened = False
            if not args.no_open:
                try:
                    opened = webbrowser.open(url)
                except (OSError, webbrowser.Error):
                    pass
            print("\n已打开浏览器。" if opened else "\n请在浏览器打开上面的地址。")
            print("关闭终端后后台继续运行。")
            print(f"停止服务：wikiskill --root {shlex.quote(str(root))} stop")
            return 0
        if args.command == "stop":
            from .services import stop_services
            print(json.dumps(stop_services(args.root), ensure_ascii=False))
            return 0
        if args.command == "_service":
            from .services import run_service
            run_service(args.root, args.port)
            return 0
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
        elif args.command == "status":
            from .services import service_status
            result = call(runtime, "wikiskill_status", {"project": args.project} if args.project else {})
            result["services"] = service_status(config.root)
        else:
            result = call(runtime, args.tool, json.load(sys.stdin))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
