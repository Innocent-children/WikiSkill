from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .config import digest
from .skills import SkillManager, bundle_diff, file_lock, materialize, skill_text, snapshot
from .store import Store, dumps


class Installer:
    def __init__(self, store: Store):
        self.store = store
        self.skills = SkillManager(store)

    def target(self, skill):
        parent = Path(self.store.config.install_directory).expanduser().absolute()
        target = parent / Path(skill["path"]).name
        if parent.resolve() != parent or target.is_symlink() or target == Path(skill["path"]):
            raise ValueError("Installation requires a separate, non-symlink Codex directory")
        return target

    def preview(self, skill_id):
        with self.skills.lock(skill_id):
            skill = self.skills.get(skill_id)
            target = self.target(skill)
            before, after = snapshot(target), snapshot(Path(skill["path"]))
            if not skill_text(after):
                raise ValueError("Generate a Skill before installing")
            return {"target": str(target), "exists": target.exists(), "source_digest": digest(after),
                    "target_digest": digest(before), "diff": bundle_diff(before, after), "files": list(after)}

    def install(self, skill_id, source_digest, target_digest, overwrite=False):
        with self.skills.lock(skill_id):
            skill = self.skills.get(skill_id)
            target = self.target(skill)
            with file_lock(self.store.config.root / "locks" / f"install-{digest(str(target))}.lock"):
                pending = self.store.rows("SELECT id FROM installations WHERE target=? AND state='prepared'", (str(target),))
                recovered = None
                for item in pending:
                    recovered = self.recover(item["id"])
                before, after = snapshot(target), snapshot(Path(skill["path"]))
                if recovered and before == after and digest(after) == source_digest:
                    return recovered
                if digest(after) != source_digest or digest(before) != target_digest:
                    raise ValueError("Skill or installation target changed; preview the current diff again")
                if target.exists() and overwrite is not True:
                    raise ValueError("Confirm overwrite after reviewing the target diff")
                if not skill_text(after):
                    raise ValueError("Generate a Skill before installing")
                key = uuid.uuid4().hex
                with self.store.transaction() as db:
                    db.execute("INSERT INTO installations VALUES(?,?,?,?,?,'prepared',?)",
                               (key, skill_id, str(target), dumps(before), dumps(after), time.time()))
                return self.recover(key)

    def recover(self, key):
        record = self.store.rows("SELECT * FROM installations WHERE id=?", (key,))[0]
        target = Path(record["target"])
        if target.resolve() != target:
            raise ValueError("Installation target changed to a symlink")
        before, after = json.loads(record["before_bundle"]), json.loads(record["after_bundle"])
        backup = target.parent / f".wikiskill-install-{key}-previous"
        temporary = target.parent / f".wikiskill-install-{key}-next"
        current = snapshot(target)
        if record["state"] != "applied" and current != after:
            if current != before and not (not current and snapshot(backup) == before):
                raise ValueError("Interrupted installation conflicts with target; retained its backup")
            target.parent.mkdir(parents=True, exist_ok=True)
            if temporary.exists() and snapshot(temporary) != after:
                raise ValueError("Installation temporary directory changed")
            if not temporary.exists():
                materialize(temporary, after)
            if target.exists():
                if backup.exists():
                    raise ValueError("Installation backup already exists")
                os.replace(target, backup)
            os.replace(temporary, target)
        with self.store.transaction() as db:
            db.execute("UPDATE installations SET state='applied' WHERE id=?", (key,))
            Store.event(db, "skill.installed", skill=record["skill"], installation=key)
        if backup.exists() and snapshot(backup) == before:
            shutil.rmtree(backup)
        return {"id": key, "target": str(target), "state": "applied", "digest": digest(after)}
