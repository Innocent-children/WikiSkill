import json
import os
import tempfile
import unittest
from pathlib import Path

from wikiskill.live.capture import Collector
from wikiskill.live.config import Config


def transcript(project, session="session", turn="turn"):
    return b"".join((json.dumps(record, ensure_ascii=False) + "\n").encode() for record in [
        {"type": "session_meta", "payload": {"id": session, "cwd": str(project)}},
        {"type": "turn_context", "payload": {"turn_id": turn}},
        {"type": "response_item", "payload": {"type": "message", "content": "失败 → 重试，原文  "}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn}},
    ])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = Config(self.root / "data", codex_home=str(self.root / "codex"), auto_start=False)
        self.collector = Collector(self.config)
        self.path = self.root / "codex/sessions/log.jsonl"
        self.path.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def originals(self):
        return b"".join(r["original"] for r in self.collector.store.rows("SELECT original FROM trace_records ORDER BY source,offset"))

    def test_start_point_history_partial_line_and_archive(self):
        before = transcript(self.project)
        self.path.write_bytes(before)
        os.utime(self.path, (1, 1))
        self.assertEqual(self.collector.scan(), 0)
        suffix = b'{"type":"unknown","payload":{"encrypted_content":"opaque"}}\n'
        with self.path.open("ab") as f:
            f.write(suffix[:-1])
        self.assertEqual(self.collector.scan(), 0)
        with self.path.open("ab") as f:
            f.write(b"\n")
        self.assertEqual(self.collector.scan(), 1)
        self.assertEqual(self.originals(), suffix)
        archive = self.root / "codex/archived_sessions/log.jsonl"
        archive.parent.mkdir()
        self.path.rename(archive)
        self.assertEqual(self.collector.scan(), 0)
        self.collector.request_history()
        self.assertEqual(self.collector.scan(), 4)
        self.assertEqual(self.originals(), before + suffix)
        self.collector.request_history()
        self.assertEqual(self.collector.scan(), 0)

    def test_new_file_unknown_bytes_and_truncation_keep_original(self):
        self.collector.scan()
        original = transcript(self.project) + b'not-json \xff\n'
        self.path.write_bytes(original)
        self.collector.scan()
        self.assertEqual(self.originals(), original)
        self.assertEqual(self.collector.scan(), 0)
        self.assertTrue(self.collector.store.rows("SELECT parse_error FROM trace_records WHERE parse_error IS NOT NULL"))
        replacement = transcript(self.project, turn="other")
        self.path.write_bytes(replacement)
        self.collector.scan()
        self.assertEqual(self.originals(), original + replacement)

    def test_own_cwd_and_registered_session_are_excluded(self):
        self.collector.scan()
        self.path.write_bytes(transcript(self.config.root))
        self.assertEqual(self.collector.scan(), 0)
        with self.collector.store.transaction() as db:
            db.execute("INSERT INTO generated_sessions VALUES('generated')")
        self.path.write_bytes(transcript(self.project, "generated"))
        self.assertEqual(self.collector.scan(), 0)


if __name__ == "__main__":
    unittest.main()
