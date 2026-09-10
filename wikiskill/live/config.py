from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wikiskill.storage import atomic_text


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def normalized(text: str) -> str:
    """Ignore line-ending and trailing-space edits while preserving Markdown indentation."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines()).strip()


def project_identity(directory: str) -> tuple[str, str]:
    path = Path(directory).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Project must be a directory")
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True, timeout=10)
    identity = str(Path(result.stdout.strip()).resolve()) if result.returncode == 0 else str(path)
    return digest(identity)[:24], str(path)


@dataclass(frozen=True)
class Config:
    root: Path
    capture_mode: str = "manual"
    automatic_scan_since: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    analysis_interval_minutes: int = 60
    session_wait_minutes: int = 60
    ollama_model: str = ""
    codex_home: str = field(default_factory=lambda: os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    install_directory: str = field(default_factory=lambda: str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"))
    executor: str = "codex"
    api_provider: str = "chat_completions"
    api_url: str = "https://api.openai.com/v1"
    api_model: str = ""
    api_key: str = field(default="", repr=False)
    codex_command: list[str] = field(default_factory=lambda: ["codex", "app-server"])
    model: str | None = None
    timeout_seconds: int = 600
    poll_seconds: int = 10
    auto_start: bool = True

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())
        try:
            since = datetime.fromisoformat(self.automatic_scan_since)
            if since.tzinfo is None:
                raise ValueError("timezone required")
            object.__setattr__(self, "automatic_scan_since", since.astimezone(timezone.utc).isoformat())
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("自动扫描起始时间必须是包含时区的有效日期时间") from exc
        for key in ("analysis_interval_minutes", "session_wait_minutes", "timeout_seconds", "poll_seconds"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("auto_start",):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be boolean")
        for key in ("codex_home", "install_directory"):
            if not isinstance(getattr(self, key), str) or not Path(getattr(self, key)).expanduser().is_absolute():
                raise ValueError(f"{key} must be an absolute directory")
            object.__setattr__(self, key, str(Path(getattr(self, key)).expanduser().resolve()))
        if not isinstance(self.capture_mode, str) or self.capture_mode not in {"manual", "automatic"}:
            raise ValueError("capture_mode must be manual or automatic")
        if not isinstance(self.ollama_model, str):
            raise ValueError("ollama_model must be text")
        if self.executor not in {"codex", "api", "ollama"} or self.api_provider not in {"chat_completions", "gemini"}:
            raise ValueError("Invalid executor or API provider")
        from urllib.parse import urlsplit
        url = urlsplit(self.api_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("api_url must be an HTTP(S) URL without credentials, query or fragment")
        if not all(isinstance(x, str) for x in (self.api_model, self.api_key)):
            raise ValueError("API model and key must be text")
        if not isinstance(self.codex_command, list) or not self.codex_command or any(not isinstance(x, str) or not x.strip() for x in self.codex_command):
            raise ValueError("codex_command must contain an executable and string arguments")
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise ValueError("model must be null or a nonempty Codex model name")

    @classmethod
    def load(cls, root: str | Path | None = None) -> Config:
        home = Path(root or "~/.wikiskill").expanduser().resolve()
        file = home / "config.json"
        values = json.loads(file.read_text()) if file.exists() else {}
        allowed = set(cls.__dataclass_fields__) - {"root"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("Unknown config fields; use the current config format")
        return cls(root=home, **values)

    def initialize(self) -> None:
        from .skills import file_lock
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / "config.json"
        with file_lock(self.root / "locks" / "settings.lock"):
            values = json.loads(path.read_text()) if path.exists() else {}
            defaults = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "root"}
            if defaults.keys() - values.keys():
                atomic_text(path, json.dumps({**defaults, **values}, ensure_ascii=False, indent=2) + "\n")
                path.chmod(0o600)

    def permits(self, path: Path, owned: bool) -> bool:
        return owned and path.parent == self.root / "skills"
