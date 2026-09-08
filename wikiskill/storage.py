"""Persist raw records, wiki pages, candidate skills and atomic accepted checkpoints."""

from __future__ import annotations

import difflib
import fcntl
import json
import os
import re
import tempfile
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .data import digest, json_text, valid_relative_path
from .patches import Skills, apply_edits, validate_skill


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def initialize(self, manifest: dict) -> None:
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError(f"Workspace must be new or empty: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in ("raw/traces", "raw/evaluations", "raw/optimizer", "raw/events",
                          "wiki/patterns", "skills", "candidates"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write("wiki/index.md", "# Pattern index\n")
        self.write("wiki/logs.md", "# Evolution findings\n")
        self.write("wiki/skill-impact.md", "# Skill validation history\n")
        self.save_state({"manifest": manifest, "iteration": 0, "baseline_score": None,
                         "best_score": None, "skills": {}, "history": []})

    def path(self, relative: str) -> Path:
        valid_relative_path(relative)
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Workspace path escapes its root")
        cursor = path
        while cursor != self.root:
            if cursor.is_symlink():
                raise ValueError("Symlink resources are not supported")
            cursor = cursor.parent
        return path

    @contextmanager
    def lock(self) -> Iterator[None]:
        if not (self.root / "state.json").is_file():
            raise ValueError("Initialize the workspace before running it")
        with self.path(".lock").open("a", encoding="utf-8") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another process is using this workspace") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def write(self, relative: str, content: str) -> None:
        if relative.startswith("raw/"):
            raise ValueError("Use immutable_record to append raw records")
        atomic_text(self.path(relative), content)

    def immutable_record(self, relative: str, value: dict) -> None:
        if not relative.startswith("raw/"):
            raise ValueError("Immutable records belong in raw/")
        content = json_text(value) + "\n"
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o444)

    def archive_artifacts(self, artifacts: dict[str, str], directory: Path, prefix: str) -> dict:
        recorded = {}
        for name, source in artifacts.items():
            valid_relative_path(name)
            path = Path(source)
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Execution artifact must be a regular file inside its task directory")
            if not path.is_file():
                continue
            content = path.read_bytes()
            destination = self.path(f"{prefix}/{name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(content)
            destination.chmod(0o444)
            recorded[name] = {"path": str(destination.relative_to(self.root)), "bytes": len(content),
                              "sha256": hashlib.sha256(content).hexdigest()}
        return recorded

    def read_state(self) -> dict:
        return json.loads(self.path("state.json").read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        self.write("state.json", json_text(state) + "\n")

    def materialize_skills(self, skills: Skills, prefix: str = "skills") -> None:
        for name, bundle in skills.items():
            validate_skill(name, bundle["SKILL.md"], bundle["PURPOSE.md"])
            for filename, content in bundle.items():
                if filename not in ("SKILL.md", "PURPOSE.md"):
                    raise ValueError("Unexpected skill bundle member")
                self.write(f"{prefix}/{name}/{filename}", content)

    def wiki_files(self) -> dict[str, str]:
        names = ["wiki/index.md", "wiki/logs.md", "wiki/skill-impact.md"]
        names.extend(str(p.relative_to(self.root)) for p in sorted(
            self.path("wiki/patterns").glob("*.md")))
        return {name: self.path(name).read_text(encoding="utf-8") for name in names}

    def apply_wiki_update(self, update: dict, iteration: int, attempt: str) -> None:
        expected = {"create_patterns", "update_patterns", "update_index", "append_log"}
        required = {"update_index", "append_log"}
        if not isinstance(update, dict) or not required <= set(update) or set(update) - expected:
            raise ValueError("Wiki update requires update_index and append_log; pattern lists are optional")
        update = {"create_patterns": [], "update_patterns": [], **update}
        if not isinstance(update["create_patterns"], list) or not isinstance(update["update_patterns"], list):
            raise ValueError("Wiki pattern changes must be lists")
        if not isinstance(update["update_index"], str) or not isinstance(update["append_log"], str):
            raise ValueError("Wiki index and log must be text")
        files = self.wiki_files()
        changes = {}
        touched = set()
        for kind in ("create_patterns", "update_patterns"):
            for change in update[kind]:
                if not isinstance(change, dict):
                    raise ValueError("Pattern change must be an object")
                name = change.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.md", name):
                    raise ValueError("Invalid pattern filename")
                path = f"wiki/patterns/{name}"
                if path in touched:
                    raise ValueError("A pattern must appear in only one update entry")
                touched.add(path)
                if kind == "create_patterns":
                    if set(change) != {"name", "content"} or path in files:
                        raise ValueError("A create operation must target a new pattern")
                    if not isinstance(change["content"], str) or not change["content"].strip():
                        raise ValueError("Pattern content must be non-empty text")
                    changes[path] = change["content"]
                else:
                    if set(change) != {"name", "edits"} or path not in files:
                        raise ValueError("A patch must target an existing pattern")
                    changes[path] = apply_edits(files[path], change["edits"])
        pages = {p for p in files | changes if p.startswith("wiki/patterns/")}
        links = set(re.findall(r"\]\((wiki/patterns/[^)]+)\)", update["update_index"]))
        if pages != links:
            raise ValueError("Wiki index must link every pattern, using wiki/patterns/<name>.md")
        changes["wiki/index.md"] = update["update_index"]
        changes["wiki/logs.md"] = files["wiki/logs.md"] + (
            f"\n## Iteration {iteration} · {attempt}\n\n{update['append_log']}\n")
        # Validate the complete edit set first; each write replaces one whole file.
        for path, content in changes.items():
            self.write(path, content)

    def append_impact(self, record: dict) -> None:
        marker = f"<!-- attempt:{record['attempt']} -->"
        path = self.path("wiki/skill-impact.md")
        previous = path.read_text(encoding="utf-8")
        if marker in previous:
            return
        name = record["proposal"].get("name", "none")
        body = (f"\n{marker}\n## Iteration {record['iteration']}: {record['outcome']} · {name}\n\n"
                f"Validation: {record.get('validation_score')}; previous best: {record['previous_best']}\n\n"
                f"```json\n{json_text(record['proposal'])}\n```\n\n"
                f"```diff\n{record.get('diff', '')}\n```\n")
        if record.get("candidate_skill"):
            body += f"\nCandidate SKILL.md (including rejected content):\n\n````markdown\n{record['candidate_skill']}\n````\n"
        if record.get("error"):
            body += f"\nEvaluation did not complete: {record['error']}\n"
        self.write("wiki/skill-impact.md", previous + body)


def skill_diff(before: Skills, after: Skills) -> str:
    result = []
    for name in sorted(before.keys() | after.keys()):
        for filename in ("SKILL.md", "PURPOSE.md"):
            old = before.get(name, {}).get(filename, "")
            new = after.get(name, {}).get(filename, "")
            if old != new:
                result.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                 fromfile=f"before/{name}/{filename}",
                                                 tofile=f"after/{name}/{filename}"))
    return "".join(result)


def read_skill_directory(root: Path | str) -> Skills:
    result = {}
    for path in sorted(Path(root).glob("*/SKILL.md")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Symlink skills are not supported")
        purpose = path.with_name("PURPOSE.md")
        if purpose.is_symlink():
            raise ValueError("Symlink purpose files are not supported")
        bundle = {"SKILL.md": path.read_text(encoding="utf-8"),
                  "PURPOSE.md": purpose.read_text(encoding="utf-8")}
        validate_skill(path.parent.name, bundle["SKILL.md"], bundle["PURPOSE.md"])
        result[path.parent.name] = bundle
    return result
