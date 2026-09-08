import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wikiskill.live.config import Config
from wikiskill.live.install import Installer
from wikiskill.live.runtime import Runtime
from wikiskill.live.skills import materialize, snapshot, with_skill


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = Runtime(Config(self.root / "data", install_directory=str(self.root / "codex/skills"), auto_start=False))
        key = self.runtime.store.project(str(self.root))
        self.skill = self.runtime.store.skills(key)[0]
        self.path = Path(self.skill["path"])
        self.path.parent.mkdir(parents=True)
        materialize(self.path, with_skill({}, "---\nname: test\ndescription: test skill\n---\nHello\n"))
        (self.path / "resource.bin").write_bytes(b"\x00\xff")
        self.installer = Installer(self.runtime.store)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, preview, overwrite=False):
        return self.installer.install(self.skill["id"], preview["source_digest"], preview["target_digest"], overwrite)

    def test_complete_copy_and_changed_target_requires_new_preview(self):
        preview = self.installer.preview(self.skill["id"])
        self.install(preview)
        target = Path(preview["target"])
        self.assertEqual(snapshot(target), snapshot(self.path))
        preview = self.installer.preview(self.skill["id"])
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.install(preview)
        (target / "resource.bin").write_bytes(b"user change")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.install(preview, True)
        self.assertEqual((target / "resource.bin").read_bytes(), b"user change")

    def test_interrupted_rename_retains_backup_and_recovers(self):
        preview = self.installer.preview(self.skill["id"])
        self.install(preview)
        (self.path / "resource.bin").write_bytes(b"new")
        preview = self.installer.preview(self.skill["id"])
        original_replace = os.replace
        def fail(source, destination):
            if str(source).endswith("-next"):
                raise OSError("simulated interruption")
            return original_replace(source, destination)
        with patch("wikiskill.live.install.os.replace", side_effect=fail), self.assertRaises(OSError):
            self.install(preview, True)
        pending = self.runtime.store.rows("SELECT id FROM installations WHERE state='prepared'")[0]
        self.installer.recover(pending["id"])
        self.assertEqual(snapshot(Path(preview["target"])), snapshot(self.path))
