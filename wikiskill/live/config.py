from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
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
    raw_threshold: int = 10
    wiki_threshold: int = 5
    manage_external: bool = False
    external_skills: list[str] = field(default_factory=list)
    codex_command: list[str] = field(default_factory=lambda: ["codex", "app-server"])
    model: str | None = None
    timeout_seconds: int = 600
    poll_seconds: int = 10
    auto_start: bool = True

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @classmethod
    def load(cls, root: str | Path | None = None) -> Config:
        home = Path(root or "~/.wikiskill").expanduser().resolve()
        file = home / "config.json"
        values = json.loads(file.read_text()) if file.exists() else {}
        allowed = set(cls.__dataclass_fields__) - {"root"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("Unknown config fields; use the current config format")
        config = cls(root=home, **values)
        for key in ("raw_threshold", "wiki_threshold", "timeout_seconds", "poll_seconds"):
            value = getattr(config, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("manage_external", "auto_start"):
            if type(getattr(config, key)) is not bool:
                raise ValueError(f"{key} must be boolean")
        for key in ("external_skills", "codex_command"):
            value = getattr(config, key)
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError(f"{key} must be a list of nonempty strings")
        if not config.codex_command:
            raise ValueError("codex_command must contain an executable")
        if config.model is not None and (not isinstance(config.model, str) or not config.model.strip()):
            raise ValueError("model must be null or a nonempty Codex model name")
        for path in config.external_skills:
            if not Path(path).expanduser().is_absolute():
                raise ValueError("external_skills must contain absolute directory paths")
        return config

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / "config.json"
        if not path.exists():
            values = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "root"}
            atomic_text(path, json.dumps(values, ensure_ascii=False, indent=2) + "\n")

    def permits(self, path: Path, owned: bool) -> bool:
        if owned:
            return path.parent == self.root / "skills"
        return self.manage_external and str(path) in {
            str(Path(p).expanduser().resolve()) for p in self.external_skills}
