import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wikiskill.live.config import Config
from wikiskill.live.skills import SkillManager, file_lock, snapshot, with_skill
from wikiskill.live.store import Store


SKILL = "---\nname: example\ndescription: Work on example tasks.\n---\n\nUse the configured runtime.\n"


class LiveSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(Config(self.root / "home", auto_start=False))
        project = self.store.project(str(self.root))
        self.skill = self.store.skills(project)[0]
        self.path = Path(self.skill["path"])
        self.manager = SkillManager(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_snapshot_diff_and_rollback_preserve_binary_and_modes(self):
        self.path.mkdir(parents=True)
        (self.path / "SKILL.md").write_text(SKILL)
        (self.path / "assets").mkdir()
        (self.path / "assets/a.bin").write_bytes(b"\x00\xff\x01")
        (self.path / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.path / "run.sh").chmod(0o755)
        (self.path / "empty").mkdir()
        before = snapshot(self.path)
        after = with_skill(before, SKILL + "Check output.\n")
        with self.manager.lock(self.skill["id"]):
            result = self.manager.publish(self.skill["id"], "job-a", before, after)
        self.assertIn("+Check output.", result["diff"])
        self.assertEqual(snapshot(self.path), after)
        self.manager.rollback(self.skill["id"], result["version_id"])
        self.assertEqual(snapshot(self.path), before)
        history = self.manager.history(self.skill["id"])
        self.assertEqual(len(history), 2)
        self.assertEqual(self.manager.history(self.skill["id"], result["version_id"])[0]["before_bundle"], before)

    def test_publication_recovers_gap_between_directory_renames_once(self):
        self.path.mkdir(parents=True)
        (self.path / "SKILL.md").write_text(SKILL)
        before = snapshot(self.path)
        after = with_skill(before, SKILL + "New instruction.\n")
        real_replace = os.replace

        def fail_second(source, target):
            if str(source).endswith("-next"):
                raise OSError("simulated power interruption")
            return real_replace(source, target)

        with patch("wikiskill.live.skills.os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.manager.publish(self.skill["id"], "job-b", before, after)
        self.assertFalse(self.path.exists())
        result = self.manager.publish(self.skill["id"], "job-b", before, after)
        self.assertEqual(snapshot(self.path), after)
        self.assertEqual(result, self.manager.publish(self.skill["id"], "job-b", before, after))
        self.assertEqual(len(self.manager.history(self.skill["id"])), 1)

    def test_conflicting_user_edit_and_symlinks_are_preserved(self):
        before = snapshot(self.path)
        self.path.mkdir(parents=True)
        (self.path / "SKILL.md").write_text("user edit")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.manager.publish(self.skill["id"], "job-c", before, with_skill({}, SKILL))
        self.assertEqual((self.path / "SKILL.md").read_text(), "user edit")
        (self.path / "linked").symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            snapshot(self.path)

    def test_missing_initial_skill_can_be_restored_as_absent(self):
        result = self.manager.publish(self.skill["id"], "first", {}, with_skill({}, SKILL))
        self.manager.rollback(self.skill["id"], result["version_id"])
        self.assertFalse(self.path.exists())

    def test_lock_refuses_another_writer_and_revoked_scope(self):
        lock = self.root / "write.lock"
        with file_lock(lock):
            with self.assertRaises(BlockingIOError):
                with file_lock(lock, blocking=False):
                    pass
        external = self.root / "external"
        external.mkdir()
        (external / "SKILL.md").write_text(SKILL)
        config_path = self.store.config.root / "config.json"
        values = json.loads(config_path.read_text())
        values.update(manage_external=True, external_skills=[str(external)])
        config_path.write_text(json.dumps(values))
        store = Store(Config.load(self.store.config.root))
        key = store.project(str(self.root))
        enrolled = store.enroll(key, str(external))
        manager = SkillManager(store)
        manager.get(enrolled["skill_id"])
        values["manage_external"] = False
        config_path.write_text(json.dumps(values))
        with self.assertRaises(ValueError):
            manager.get(enrolled["skill_id"])


if __name__ == "__main__":
    unittest.main()
