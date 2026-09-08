"""Load disjoint task splits and score predictions outside the inference agent."""

from __future__ import annotations

import hashlib
import copy
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def valid_relative_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("A resource path must be a non-empty relative POSIX path")
    if name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError(f"Invalid resource path: {name!r}")
    return name


@dataclass(frozen=True)
class TaskInput:
    """The task information available to an inference environment."""

    id: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    answer: Any
    files: dict[str, str] = field(default_factory=dict)
    metric: str = "exact"
    tolerance: float = 1e-6
    context: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)

    def public(self) -> TaskInput:
        return TaskInput(self.id, self.prompt, dict(self.files), copy.deepcopy(self.context))

    def record(self) -> dict:
        return dict(id=self.id, prompt=self.prompt, answer=self.answer, files=self.files,
                    metric=self.metric, tolerance=self.tolerance,
                    context=self.context, evaluation=self.evaluation)


@dataclass(frozen=True)
class Dataset:
    train: tuple[Task, ...]
    validation: tuple[Task, ...]
    test: tuple[Task, ...]

    def __post_init__(self) -> None:
        if len(self.train) < 4:
            raise ValueError("Training requires at least four tasks for proposer trace inspection")
        if not self.validation or not self.test:
            raise ValueError("Validation and test splits must each contain at least one task")
        ids: set[str] = set()
        seen_inputs: dict[str, str] = {}
        for split in ("train", "validation", "test"):
            for task in getattr(self, split):
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", task.id):
                    raise ValueError(f"Invalid task id: {task.id!r}")
                if task.id in ids:
                    raise ValueError(f"Duplicate task id: {task.id}")
                ids.add(task.id)
                if not isinstance(task.prompt, str) or not task.prompt.strip():
                    raise ValueError(f"Empty prompt for {task.id}")
                if task.metric not in ("exact", "normalized", "numeric"):
                    raise ValueError(f"Unknown metric: {task.metric}")
                if not math.isfinite(task.tolerance) or task.tolerance < 0:
                    raise ValueError("Numeric tolerance must be finite and non-negative")
                if not isinstance(task.files, dict):
                    raise ValueError("Task files must map relative paths to text")
                if not isinstance(task.context, dict) or not isinstance(task.evaluation, dict):
                    raise ValueError("context and evaluation must be objects")
                json_text(task.record())
                for path, content in task.files.items():
                    valid_relative_path(path)
                    if not isinstance(content, str):
                        raise ValueError("Task file contents must be strings")
                identity = digest({"prompt": task.prompt.strip(), "files": task.files, "context": task.context})
                if identity in seen_inputs and seen_inputs[identity] != split:
                    raise ValueError(f"The same task input occurs in different splits: {task.id}")
                seen_inputs[identity] = split

    @classmethod
    def load(cls, directory: Path | str) -> Dataset:
        root = Path(directory)
        splits = {}
        allowed = {"id", "prompt", "answer", "files", "metric", "tolerance", "context", "evaluation"}
        for split in ("train", "validation", "test"):
            tasks = []
            path = root / f"{split}.jsonl"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict) or set(item) - allowed:
                        raise ValueError("Task contains unknown fields")
                    tasks.append(Task(**item))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"{path.name}:{number}: {exc}") from exc
            splits[split] = tuple(tasks)
        return cls(**splits)

    def fingerprint(self) -> str:
        return digest({split: [task.record() for task in getattr(self, split)]
                       for split in ("train", "validation", "test")})


def extract_answer(text: str) -> str:
    matches = re.findall(r"<answer>\s*(.*?)\s*</answer>", text, re.DOTALL | re.IGNORECASE)
    return (matches[-1] if matches else text).strip()


def _normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def score_answer(prediction: str, task: Task) -> float:
    alternatives = task.answer if isinstance(task.answer, list) else [task.answer]
    for expected in alternatives:
        if task.metric == "numeric":
            try:
                left, right = float(prediction), float(expected)
                if math.isfinite(left) and math.isfinite(right) and math.isclose(
                    left, right, rel_tol=0, abs_tol=task.tolerance
                ):
                    return 1.0
            except (TypeError, ValueError):
                pass
        elif task.metric == "normalized":
            if _normalize(prediction) == _normalize(expected):
                return 1.0
        elif prediction.strip() == str(expected).strip():
            return 1.0
    return 0.0


Scorer = Callable[[str, Task], float]


def checked_score(scorer: Scorer, prediction: str, task: Task) -> float:
    score = float(scorer(prediction, task))
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"Scorer returned an invalid score for {task.id}")
    return score
