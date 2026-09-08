from __future__ import annotations

import base64
import difflib
import fcntl
import json
import os
import re
import shutil
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import yaml

from .config import Config, digest
from .store import Store, dumps


@contextmanager
def file_lock(path: Path, blocking: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def snapshot(path: Path) -> dict:
    """Capture every regular file, empty directory and permission bit, including binary assets."""
    if path.is_symlink():
        raise ValueError("Skill symlinks are not supported")
    if not path.exists():
        return {}
    if not path.is_dir():
        raise ValueError("Skill path must be a directory")
    result = {}
    for member in [path, *sorted(path.rglob("*"))]:
        info = member.lstat()
        name = member.relative_to(path).as_posix()
        if stat.S_ISDIR(info.st_mode):
            result[name] = {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            result[name] = {"type": "file", "mode": stat.S_IMODE(info.st_mode),
                            "data": base64.b64encode(member.read_bytes()).decode()}
        else:
            raise ValueError(f"Skill contains a symlink or special file: {name}")
    return result


def skill_text(bundle: dict) -> str:
    entry = bundle.get("SKILL.md")
    if not entry or entry["type"] != "file":
        return ""
    return base64.b64decode(entry["data"]).decode("utf-8")


def with_skill(bundle: dict, content: str) -> dict:
    if not isinstance(content, str) or not content.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Unterminated Skill frontmatter")
    front = yaml.safe_load(parts[1])
    if not isinstance(front, dict) or not isinstance(front.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", front["name"]):
        raise ValueError("Skill frontmatter requires a valid name")
    if not isinstance(front.get("description"), str) or not front["description"].strip() or not parts[2].strip():
        raise ValueError("Skill requires a description and instructions")
    result = {**bundle}
    result.setdefault(".", {"type": "directory", "mode": 0o755})
    result["SKILL.md"] = {"type": "file", "mode": bundle.get("SKILL.md", {}).get("mode", 0o644),
                           "data": base64.b64encode(content.encode()).decode()}
    return result


def bundle_diff(before: dict, after: dict) -> str:
    lines = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if (old and old["type"] == "directory") or (new and new["type"] == "directory"):
            lines.append(f"Directory {name}: {old} -> {new}\n")
            continue
        try:
            left = base64.b64decode(old["data"]).decode() if old else ""
            right = base64.b64decode(new["data"]).decode() if new else ""
            lines.extend(difflib.unified_diff(left.splitlines(keepends=True), right.splitlines(keepends=True),
                                            fromfile=f"before/{name}", tofile=f"after/{name}"))
        except UnicodeDecodeError:
            lines.append(f"Binary {name}: {digest(old)} -> {digest(new)}\n")
        if old and new and old["mode"] != new["mode"]:
            lines.append(f"Mode {name}: {oct(old['mode'])} -> {oct(new['mode'])}\n")
    return "".join(lines)


def materialize(path: Path, bundle: dict) -> None:
    path.mkdir()
    directories = []
    for name, entry in sorted(bundle.items()):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Version contains an invalid path")
        target = path / name
        if entry["type"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
            directories.append((target, entry["mode"]))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(base64.b64decode(entry["data"], validate=True))
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(entry["mode"])
    for target, mode in reversed(directories):
        target.chmod(mode)


class SkillManager:
    def __init__(self, store: Store):
        self.store = store

    def get(self, skill_id: str) -> dict:
        rows = self.store.rows("SELECT * FROM skills WHERE id=?", (skill_id,))
        if not rows:
            raise ValueError("Unknown skill")
        skill = rows[0]
        path = Path(skill["path"])
        if not Config.load(self.store.config.root).permits(path, bool(skill["owned"])):
            raise ValueError("Skill is outside the currently enabled management scope")
        if path.resolve() != path:
            raise ValueError("Managed skill path changed to a symlink")
        return skill

    def lock(self, skill_id: str):
        return file_lock(self.store.config.root / "locks" / f"skill-{skill_id}.lock")

    def publish(self, skill_id: str, job_id: str, before: dict, after: dict) -> dict:
        """Caller holds the Skill lock through generation, journal preparation and replacement."""
        skill = self.get(skill_id)
        previous = self.store.rows("SELECT * FROM versions WHERE job_id=?", (job_id,))
        if previous:
            return self.recover(previous[0]["id"])
        if snapshot(Path(skill["path"])) != before:
            raise ValueError("Skill changed after generation started; kept the user's current content")
        if before == after:
            return {"changed": False, "version_id": None, "diff": ""}
        version_id = uuid.uuid4().hex
        difference = bundle_diff(before, after)
        with self.store.transaction() as db:
            db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)",
                       (version_id, skill_id, job_id, dumps(before), dumps(after), difference, "prepared", time.time()))
            self.store.event(db, "publication.prepared", skill=skill_id,
                             job_id=None if job_id.startswith("rollback-") else job_id, version_id=version_id)
        return self.recover(version_id)

    def recover(self, version_id: str) -> dict:
        version = self.store.rows("SELECT * FROM versions WHERE id=?", (version_id,))[0]
        skill = self.get(version["skill"])
        path = Path(skill["path"])
        before, after = json.loads(version["before_bundle"]), json.loads(version["after_bundle"])
        result = {"changed": True, "version_id": version_id, "diff": version["diff"]}
        if version["state"] == "applied":
            return result
        current = snapshot(path)
        temporary = path.parent / f".wikiskill-{version_id}-next"
        previous = path.parent / f".wikiskill-{version_id}-previous"
        if current != after:
            if current != before and not (not current and snapshot(previous) == before):
                raise ValueError("Interrupted publication conflicts with current Skill content")
            path.parent.mkdir(parents=True, exist_ok=True)
            if temporary.exists():
                if snapshot(temporary) != after:
                    shutil.rmtree(temporary)
            if not temporary.exists() and after:
                materialize(temporary, after)
            if path.exists():
                if previous.exists():
                    raise ValueError("Publication backup already exists; inspect the retained version")
                os.replace(path, previous)
            if after:
                os.replace(temporary, path)
        with self.store.transaction() as db:
            db.execute("UPDATE versions SET state='applied' WHERE id=?", (version_id,))
            self.store.event(db, "publication.applied", skill=version["skill"],
                             job_id=None if version["job_id"].startswith("rollback-") else version["job_id"],
                             version_id=version_id)
        if previous.exists() and snapshot(previous) == before:
            shutil.rmtree(previous)
        return result

    def history(self, skill_id: str, version_id: str | None = None) -> list[dict]:
        self.get(skill_id)
        columns = "*" if version_id else "id,skill,job_id,diff,state,created"
        rows = self.store.rows(f"SELECT {columns} FROM versions WHERE skill=?" +
                              (" AND id=?" if version_id else "") + " ORDER BY created",
                              (skill_id, version_id) if version_id else (skill_id,))
        for row in rows:
            for key in ("before_bundle", "after_bundle"):
                if key in row:
                    row[key] = json.loads(row[key])
        return rows

    def rollback(self, skill_id: str, version_id: str, side: str = "before") -> dict:
        if side not in {"before", "after"}:
            raise ValueError("side must be before or after")
        with self.lock(skill_id):
            skill = self.get(skill_id)
            pending = self.store.rows("SELECT id FROM versions WHERE skill=? AND state='prepared'", (skill_id,))
            for row in pending:
                self.recover(row["id"])
            versions = self.history(skill_id, version_id)
            if not versions or versions[0]["state"] != "applied":
                raise ValueError("Choose an applied version of this Skill")
            target = versions[0][f"{side}_bundle"]
            return self.publish(skill_id, "rollback-" + uuid.uuid4().hex, snapshot(Path(skill["path"])), target)
