import json
import tempfile
import unittest
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.settings import public_settings, save_settings
from test_live_runtime import seed_record, FakeSession


class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / "home", auto_start=False), FakeSession)
        self.key = self.runtime.store.project(str(self.project))

    def tearDown(self):
        self.tmp.cleanup()

    def test_wiki_return_to_previous_body_creates_history_and_checks_concurrency(self):
        put = lambda body, expected=None: self.runtime.put_wiki(str(self.project), [{"name": "build", "body": body}], expected=expected)
        put("A", {"build": None})
        old = self.runtime.store.query(self.key, "wiki")["items"][0]["digest"]
        put("B", {"build": old})
        with self.assertRaisesRegex(ValueError, "changed"):
            put("lost", {"build": old})
        put("A")
        self.assertEqual([r["body"] for r in self.runtime.store.rows("SELECT body FROM wiki_changes ORDER BY id")], ["A", "B", "A"])
        self.assertEqual(put("A  \n")["new_changes"], 0)

    def test_manual_bypasses_threshold_and_auto_is_opt_in(self):
        seed_record(self.runtime, self.key)
        self.assertEqual(self.runtime.schedule(), [])
        job = self.runtime.enqueue(self.key, "raw")
        with self.assertRaisesRegex(ValueError, "already"):
            self.runtime.enqueue(self.key, "raw")
        self.assertEqual(self.runtime.store.job(job["job_id"])["inputs"], [1])

    def test_settings_do_not_return_key_and_reject_old_format(self):
        config = save_settings(self.runtime.config.root, {"api_key": "test-secret", "capture_mode": "automatic"})
        self.assertTrue(config["api_key_configured"])
        self.assertNotIn("test-secret", json.dumps(config))
        save_settings(self.runtime.config.root, {"api_key": ""})
        self.assertEqual(Config.load(self.runtime.config.root).api_key, "test-secret")
        save_settings(self.runtime.config.root, {"clear_api_key": True})
        self.assertFalse(public_settings(Config.load(self.runtime.config.root))["api_key_configured"])
        for change in ({"raw_threshold": True}, {"wiki_threshold": 0}, {"manage_external": True}):
            with self.assertRaises(ValueError):
                save_settings(self.runtime.config.root, change)

    def test_session_wait_settings_validate_and_roundtrip(self):
        root = self.runtime.config.root
        self.assertEqual(Config.load(root).session_wait_minutes, 60)
        for minutes in (1, 15, 120):
            saved = save_settings(root, {"session_wait_minutes": minutes})
            self.assertEqual(saved["session_wait_minutes"], minutes)
            self.assertEqual(Config.load(root).session_wait_minutes, minutes)
        before = (root / "config.json").read_bytes()
        for value in (0, -1, 1.5, True, "30", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "session_wait_minutes"):
                save_settings(root, {"session_wait_minutes": value})
            self.assertEqual((root / "config.json").read_bytes(), before)

    def test_old_summary_rows_survive_initialization(self):
        with self.runtime.store.transaction() as db:
            db.execute("INSERT INTO raw VALUES('old',?,'source','{\"observations\":[]}',1)", (self.key,))
        Runtime(Config.load(self.runtime.config.root))
        self.assertEqual(self.runtime.store.query(self.key, "raw")["items"][0]["id"], "old")
        self.assertEqual(self.runtime.store.rows("SELECT * FROM trace_records"), [])

    def test_old_summary_retry_cannot_consume_transcript_ids(self):
        seed_record(self.runtime, self.key)
        with self.runtime.store.transaction() as db:
            db.execute("INSERT INTO jobs(id,project,stage,state,inputs,created) VALUES('old',?,'raw','failed','[1]',1)", (self.key,))
        with self.assertRaisesRegex(ValueError, "Old summary"):
            self.runtime.retry("old", regenerate=True)
        self.assertIsNone(self.runtime.store.rows("SELECT consumed_by FROM trace_records")[0]["consumed_by"])
