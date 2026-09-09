"""Own the local panel and its collector/worker processes for one data directory."""

from __future__ import annotations

import errno
import fcntl
import hmac
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from wikiskill.storage import atomic_text
from .config import Config, digest
from .skills import file_lock


def _held(path: Path) -> bool:
    try:
        with path.open("r") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def _descriptor(root: Path) -> dict | None:
    try:
        value = json.loads((root / "service.json").read_text())
        if (isinstance(value, dict) and type(value.get("port")) is int
                and 1 <= value["port"] <= 65535 and isinstance(value.get("token"), str)
                and len(value["token"]) == 64):
            return value
    except (OSError, ValueError):
        pass
    return None


def _request(root: Path, descriptor: dict, operation: str = "health") -> dict:
    root = root.expanduser().resolve()
    request = urllib.request.Request(
        f'http://127.0.0.1:{descriptor["port"]}/api/service/{operation}',
        headers={"X-WikiSkill-Token": descriptor["token"]},
        method="GET" if operation == "health" else "POST",
    )
    # Local control stays local even when the user's shell configures an HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
        value = json.load(response)
    if value.get("identity") != digest(str(root)):
        raise RuntimeError("The port belongs to another WikiSkill data directory")
    return value


def service_status(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    descriptor = _descriptor(root)
    if descriptor:
        try:
            value = _request(root, descriptor)
            return {k: v for k, v in value.items() if k != "identity"}
        except (OSError, ValueError, RuntimeError):
            pass
    return {"state": "unavailable" if _held(root / "locks/service-process.lock") else "stopped",
            "url": None, "worker": "stopped", "collector": "stopped"}


def _spawn(root: Path, command: list[str], log_name: str) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[2]), *sys.path])
    with (root / log_name).open("ab") as log:
        return subprocess.Popen([sys.executable, "-m", "wikiskill.live.cli", "--root", str(root), *command],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=root,
                                env=environment, start_new_session=True, close_fds=True)


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            process.wait()


def ensure_services(root: str | Path, port: int = 8765) -> dict:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Use a port between 1 and 65535")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(root / "locks/service-launch.lock"):
        if not Path(__file__).with_name("web_assets").joinpath("index.html").is_file():
            raise RuntimeError("WebUI assets are missing. Reinstall WikiSkill or run npm run build in web/.")
        config = Config.load(root)
        config.initialize()
        current = service_status(root)
        if current["state"] == "running":
            return {**current, "reused": True}
        if current["state"] == "degraded":
            _request(root, _descriptor(root), "ensure")
        elif current["state"] == "unavailable":
            raise RuntimeError(f"WikiSkill is still starting or stopping. Retry shortly; see {root / 'service.log'}")
        else:
            process = _spawn(root, ["_service", "--port", str(port)], "service.log")
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    current = service_status(root)
                    if current["state"] == "running":
                        # The waiting thread owns the handle until the detached service exits.
                        threading.Thread(target=process.wait, name="wikiskill-service-reaper", daemon=True).start()
                        return {**current, "reused": False}
                    time.sleep(0.1)
                raise RuntimeError(f"WikiSkill could not start. Check {root / 'service.log'} and {root / 'worker.log'}")
            except BaseException:
                _terminate(process)
                raise
        current = service_status(root)
        if current["state"] != "running":
            raise RuntimeError(f"Background processes are not ready; see {root / 'worker.log'}")
        return {**current, "reused": True}


def stop_services(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return {"state": "stopped"}
    with file_lock(root / "locks/service-launch.lock"):
        descriptor = _descriptor(root)
        if not _held(root / "locks/service-process.lock"):
            return {"state": "stopped"}
        if not descriptor:
            raise RuntimeError("Service is starting; retry stop shortly")
        _request(root, descriptor, "stop")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not _held(root / "locks/service-process.lock"):
                return {"state": "stopped"}
            time.sleep(0.1)
        raise RuntimeError(f"Service is still stopping; check {root / 'service.log'}")


class ServiceProcess:
    def __init__(self, root: Path, port: int):
        self.root, self.port = root, port
        self.token = secrets.token_hex(32)
        self.children: dict[str, subprocess.Popen] = {}
        self.mutex = threading.Lock()
        self.server = None

    def status(self) -> dict:
        states = {name: "running" if child.poll() is None and
                  _held(self.root / f"locks/{name}-process.lock") else "stopped"
                  for name, child in self.children.items()}
        return {"identity": digest(str(self.root)), "state": "running" if
                len(states) == 2 and all(s == "running" for s in states.values()) else "degraded",
                "url": f"http://127.0.0.1:{self.port}", "pid": os.getpid(), **states}

    def ensure(self):
        with self.mutex:
            for name in ("collector", "worker"):
                child = self.children.get(name)
                if child is None or child.poll() is not None:
                    if _held(self.root / f"locks/{name}-process.lock"):
                        raise RuntimeError(f"A separately started {name} is running. Stop that process before starting WikiSkill.")
                    self.children[name] = _spawn(self.root, [name], "worker.log")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.status()["state"] == "running":
                    return
                if any(child.poll() is not None for child in self.children.values()):
                    break
                time.sleep(0.05)
            raise RuntimeError("Collector or worker failed to start; see worker.log")

    def run(self):
        import uvicorn
        from fastapi import Request
        from fastapi.responses import JSONResponse
        from .web import create_app

        def interrupted(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, interrupted)
        with file_lock(self.root / "locks/service-process.lock", blocking=False), socket.socket() as listener:
            try:
                try:
                    listener.bind(("127.0.0.1", self.port))
                except OSError as exc:
                    if exc.errno != errno.EADDRINUSE:
                        raise
                    listener.bind(("127.0.0.1", 0))
                listener.listen(128)
                self.port = listener.getsockname()[1]
                self.ensure()
                app = create_app(self.root)
                app.state.services = self
                self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2))

                # Register concrete annotations because FastAPI resolves them in module globals.
                async def control(request):
                    if not hmac.compare_digest(request.headers.get("x-wikiskill-token", ""), self.token):
                        return JSONResponse({"detail": "Invalid service token"}, status_code=403)
                    operation = request.path_params["operation"]
                    if operation == "health" and request.method == "GET":
                        return self.status()
                    if operation == "stop" and request.method == "POST":
                        self.server.should_exit = True
                        return {"identity": digest(str(self.root)), "state": "stopping"}
                    if operation == "ensure" and request.method == "POST":
                        from starlette.concurrency import run_in_threadpool
                        await run_in_threadpool(self.ensure)
                        return self.status()
                    return JSONResponse({"detail": "Unknown service operation"}, status_code=404)

                control.__annotations__["request"] = Request
                app.add_api_route("/api/service/{operation}", control, methods=["GET", "POST"])
                atomic_text(self.root / "service.json", json.dumps({"port": self.port, "token": self.token}))
                self.server.run(sockets=[listener])
            finally:
                for child in self.children.values():
                    _terminate(child)
                (self.root / "service.json").unlink(missing_ok=True)


def run_service(root: str | Path, port: int):
    ServiceProcess(Path(root).expanduser().resolve(), port).run()
