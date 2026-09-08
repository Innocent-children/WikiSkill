"""Common dataset asset resolution and scoring contracts for benchmark adapters."""

from __future__ import annotations

import hashlib
import copy
from pathlib import Path

from ..data import Task, TaskInput, score_answer, valid_relative_path


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def comparison_identity(identity: dict | None) -> dict | None:
    if identity is None:
        return None
    result = copy.deepcopy(identity)
    result.get("options", {}).pop("seed", None)
    return result


def asset_path(root: Path, relative: str) -> Path:
    valid_relative_path(relative)
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Dataset asset escapes its root: {relative}")
    if not candidate.is_file():
        raise ValueError(f"Dataset asset does not exist: {relative}")
    return candidate.resolve()


class Benchmark:
    name = ""
    description = "the configured task family"
    allowed_options: set[str] = set()

    def __init__(self, data_root: Path, options: dict):
        if not isinstance(options, dict) or set(options) - self.allowed_options:
            raise ValueError(f"Unknown options for {self.name}: {set(options) - self.allowed_options}")
        self.data_root = data_root.resolve()
        self.options = dict(options)
        self.asset_hashes: dict[str, str] = {}

    @property
    def identity(self) -> dict:
        return {"name": self.name, "options": self.options, "asset_hashes": self.asset_hashes}

    def check_asset(self, path: str) -> Path:
        resolved = asset_path(self.data_root, path)
        self.asset_hashes[path] = file_digest(resolved)
        return resolved

    def validate(self, dataset) -> None:
        for split in (dataset.train, dataset.validation, dataset.test):
            for task in split:
                self.validate_task(task)

    def validate_task(self, task: Task) -> None:
        pass

    def run(self, agent, task: TaskInput, skills, wiki, directory: Path):
        raise NotImplementedError

    def score(self, prediction: str, task: Task, conversation) -> float:
        return score_answer(prediction, task) if conversation.termination == "answer" else 0.0
