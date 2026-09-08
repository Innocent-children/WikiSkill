import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from wikiskill.live.config import Config, project_identity
from wikiskill.live.runtime import Runtime


class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = Config(self.root / "home", auto_start=False, raw_threshold=2, wiki_threshold=2)
        self.runtime = Runtime(self.config)
        self.key = self.runtime.store.project(str(self.project))
        self.observation = {"problem": "Build fails", "action": "Set JAVA_HOME", "outcome": "Build passes", "lesson": "Use project JDK"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_raw_preserves_original_but_counts_normalized_content_once(self):
        store = self.runtime.store
        first = store.collect(self.key, "thread:event", [self.observation], {"time": 1})
        self.assertEqual(first["new_observations"], 1)
        second = store.collect(self.key, "thread:event", [self.observation], {"time": 2})
        self.assertTrue(second["duplicate"])
        item = {k: v + "  \r\n" for k, v in self.observation.items()}
        self.assertEqual(store.collect(self.key, "another", [item], {})["new_observations"], 0)
        self.assertEqual(store.query(self.key, "raw", first["raw_id"])["items"][0]["payload"]["metadata"], {"time": 1})
        self.assertEqual(self.runtime.schedule(), [])
        store.collect(self.key, "logs-only", [], {"timestamp": 3, "index": "new"})
        self.assertEqual(self.runtime.schedule(), [])
        with self.assertRaises(ValueError):
            store.collect(self.key, "thread:event", [{**self.observation, "lesson": "different"}], {})
        store.collect(self.key, "second", [{**self.observation, "problem": "Test fails"}], {})
        self.assertEqual(len(self.runtime.schedule()), 1)
        self.assertEqual(self.runtime.schedule(), [])

    def test_wiki_metadata_and_seen_versions_do_not_count(self):
        put = lambda body, metadata: self.runtime.put_wiki(str(self.project), [{"name": "build", "body": body}], metadata)
        self.assertEqual(put("Use JDK 21", {"time": 1})["new_changes"], 1)
        self.assertEqual(put("Use JDK 21  \r\n", {"time": 2})["new_changes"], 0)
        self.assertEqual(self.runtime.schedule(), [])
        self.assertEqual(put("Use JDK 21 and settings.xml", {})["new_changes"], 1)
        self.assertEqual(len(self.runtime.schedule()), 1)
        self.assertEqual(put("Use JDK 21", {})["new_changes"], 0)
        self.assertEqual(self.runtime.store.query(self.key, "wiki")["items"][0]["body"], "Use JDK 21")

    def test_git_worktrees_share_project_identity(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        worktree = self.root / "worktree"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "--detach", "-q", str(worktree)], check=True)
        self.assertEqual(project_identity(str(self.project))[0], project_identity(str(worktree))[0])

    def test_strict_config_and_pagination(self):
        file = self.config.root / "config.json"
        original = json.loads(file.read_text())
        for change in ({"raw_threshold": True}, {"wiki_threshold": 0}, {"auto_start": "yes"}, {"old_config": {}}):
            file.write_text(json.dumps({**original, **change}))
            with self.assertRaises(ValueError):
                Config.load(self.config.root)
        file.write_text(json.dumps(original))
        for i in range(3):
            self.runtime.store.collect(self.key, str(i), [], {})
        page = self.runtime.store.query(self.key, "raw", limit=2)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["next_offset"], 2)
        self.assertIsNone(self.runtime.store.query(self.key, "raw", offset=2, limit=2)["next_offset"])

    def test_external_requires_switch_list_and_project_enrollment(self):
        external = self.root / "external"
        external.mkdir()
        (external / "SKILL.md").write_text("skill")
        with self.assertRaises(ValueError):
            self.runtime.store.enroll(self.key, str(external))
        configured = replace(self.config, manage_external=True, external_skills=[str(external)])
        runtime = Runtime(configured)
        enrolled = runtime.store.enroll(self.key, str(external))
        self.assertEqual(len(runtime.store.skills(self.key)), 2)
        self.assertEqual(enrolled["path"], str(external.resolve()))


if __name__ == "__main__":
    unittest.main()
