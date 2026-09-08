"""Run task shell commands in a disposable container with one data mount."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path


class DockerSandbox:
    def __init__(self, directory: Path, options: dict):
        self.directory = directory.resolve()
        self.image = options.get("image", "wikiskill-spreadsheet:local")
        self.timeout = options.get("command_timeout", 60)
        self.memory = options.get("memory", "1g")
        self.cpus = options.get("cpus", 1)
        self.output_characters = options.get("output_characters", 30000)
        if not self.image or type(self.timeout) is not int or self.timeout <= 0:
            raise ValueError("Supply a sandbox image and positive command_timeout")
        if type(self.output_characters) is not int or self.output_characters < 1:
            raise ValueError("output_characters must be positive")
        if ":" in str(self.directory):
            raise ValueError("Docker task directories cannot contain a colon")
        self.directory.mkdir(parents=True, exist_ok=True)

    def check(self) -> None:
        try:
            result = subprocess.run(["docker", "image", "inspect", self.image], capture_output=True,
                                    text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Docker is unavailable; start Docker and run wikiskill sandbox-build") from exc
        if result.returncode:
            raise RuntimeError("Sandbox image is unavailable; start Docker and run wikiskill sandbox-build")
        status = self.run("command -v libreoffice >/dev/null && python -c 'import openpyxl'")
        if status["exit_code"] != 0:
            raise RuntimeError("Sandbox image requires LibreOffice and openpyxl; run wikiskill sandbox-build")

    def run(self, command: str) -> dict:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("bash command must be non-empty text")
        name = "wikiskill-" + uuid.uuid4().hex
        args = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
                "--memory", str(self.memory), "--cpus", str(self.cpus),
                "--tmpfs", "/tmp:rw,nosuid,size=256m", "--user", f"{os.getuid()}:{os.getgid()}",
                "--env", "HOME=/tmp", "--volume", f"{self.directory}:/workspace/task:rw",
                "--workdir", "/workspace/task", self.image, "bash", "-lc", command]
        timed_out = False
        try:
            try:
                result = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout)
                stdout, stderr, code = result.stdout, result.stderr, result.returncode
                if code in (125, 126, 127) and "docker:" in stderr.lower():
                    raise RuntimeError("Docker could not execute the sandbox container")
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout, stderr, code = exc.stdout or "", exc.stderr or "", 124
                if isinstance(stdout, bytes):
                    stdout = stdout.decode(errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
                stderr += "\nCommand exceeded the task timeout."
            return {"exit_code": code, "stdout": stdout[:self.output_characters],
                    "stderr": stderr[:self.output_characters], "timed_out": timed_out,
                    "truncated": len(stdout) > self.output_characters or len(stderr) > self.output_characters}
        finally:
            # The command can outlive its CLI client; remove this call's named container.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False)
