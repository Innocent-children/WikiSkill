import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.telemetry import WorkerHeartbeat
from wikiskill.live.views import ChangeCursor, ReadView
from wikiskill.live.web import create_app
from test_live_runtime import FakeSession, OBSERVATION, seed_record


class LiveWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / "home", auto_start=False, capture_mode="automatic"), FakeSession)
        self.key = self.runtime.store.project(str(self.project))
        with self.runtime.store.transaction() as db:
            db.execute('INSERT INTO skill_wiki VALUES(?,?,?,0)', (self.runtime.store.skills(self.key)[0]['id'], self.key, 'build'))
        self.view = ReadView(self.runtime.config.root)
        self.client = TestClient(create_app(self.runtime.config.root), base_url="http://127.0.0.1")
        FakeSession.starts, FakeSession.generations, FakeSession.reports = [], [], []
        FakeSession.fail_generate = FakeSession.fail_report = FakeSession.no_change = False
        FakeSession.during_generate = None

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def collect(self, source="first", lesson="Check settings"):
        seed_record(self.runtime, self.key, source + "-one")
        return seed_record(self.runtime, self.key, source + "-two")

    def test_uninitialized_app_and_cli_queries_are_read_only(self):
        empty = self.root / "absent"
        with TestClient(create_app(empty), base_url="http://127.0.0.1") as client:
            self.assertFalse(client.get("/api/snapshot").json()["initialized"])
            self.assertEqual(client.get("/api/jobs").json()["items"], [])
            self.assertEqual(client.get("/api/projects/missing/raw").status_code, 404)
            self.assertFalse(empty.exists())
        self.collect()
        before = hashlib.sha256(self.view.path.read_bytes()).hexdigest()
        files = set(self.view.root.rglob("*"))
        self.assertEqual(self.client.get("/api/snapshot").status_code, 200)
        self.assertEqual(self.client.get("/api/jobs").status_code, 200)
        self.assertEqual(self.client.get(f"/api/projects/{self.key}/raw").status_code, 200)
        self.assertEqual(self.client.get("/api/projects/unknown/raw").status_code, 404)
        self.assertEqual(before, hashlib.sha256(self.view.path.read_bytes()).hexdigest())
        self.assertEqual(files, set(self.view.root.rglob("*")))
        self.assertEqual(len(self.runtime.store.rows("SELECT id FROM projects")), 1)
        self.assertEqual(self.runtime.store.rows("SELECT id FROM jobs"), [])

    def test_real_runtime_events_reports_wiki_and_complete_version_files(self):
        skill = self.runtime.store.skills(self.key)[0]
        directory = Path(skill["path"])
        directory.mkdir(parents=True)
        (directory / "resource.bin").write_bytes(b"\x00\xff\x7f")
        self.collect()
        FakeSession.fail_report = True
        self.runtime.drain()
        jobs = self.view.jobs()["items"]
        target = next(j for j in jobs if j["stage"] == "skill")
        self.assertEqual((target["state"], target["content_status"], target["report_status"]), ("done", "published", "failed"))
        detail = self.view.job(target["id"])
        self.assertIsNotNone(detail["started_at"])
        self.assertIsNotNone(detail["finished_at"])
        kinds = [event["kind"] for event in self.view.events(target["id"])["items"]]
        self.assertLess(kinds.index("publication.prepared"), kinds.index("publication.applied"))
        self.assertLess(kinds.index("publication.applied"), kinds.index("job.applied"))
        self.assertIn("report.failed", kinds)
        wiki = self.view.wiki_item(self.key, "build")
        self.assertIsNotNone(wiki["changes"]["items"][0]["source_job"])
        self.assertEqual(wiki["changes"]["items"][0]["consumers"][0]["consumed_by"], target["id"])
        version = self.view.version(skill["id"], target["version_id"])
        self.assertIn("Read the project", version["after"]["skill_md"])
        self.assertEqual(self.view.version_file(skill["id"], target["version_id"], "before", "resource.bin"), b"\x00\xff\x7f")
        base = f"/api/skills/{skill['id']}/versions/{target['version_id']}"
        self.assertEqual(self.client.get(base + "/file", params={"side": "after", "name": "resource.bin"}).content, b"\x00\xff\x7f")
        self.assertEqual(self.client.get(base + "/file", params={"side": "after", "name": "../../config.json"}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/jobs/{target['id']}/report").json()["outcome"], "changed")
        self.assertEqual(self.client.get(f"/api/jobs/{target['id']}/inputs").json()["items"][0]["name"], "build")

    def test_no_change_and_failed_attempt_report_are_separate(self):
        self.collect()
        FakeSession.no_change = True
        self.runtime.drain()
        self.assertEqual(self.view.jobs()["items"][0]["content_status"], "no_change")
        self.assertEqual(self.view.snapshot()["projects"][0]["raw"]["pending"], 0)
        FakeSession.no_change = False
        FakeSession.fail_generate = True
        self.collect("failure")
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE automatic_analysis SET last_run=0")
        self.runtime.drain()
        job = self.view.jobs(state="failed")["items"][0]
        self.assertEqual(job["report_status"], "sent")
        self.assertEqual(self.view.snapshot()["projects"][0]["raw"]["reason"], "failed")
        self.runtime.retry(job["id"])
        pending = self.view.job(job["id"])
        self.assertEqual(pending["report_status"], "pending")
        self.assertIsNone(pending["report"])
        self.assertEqual(pending["previous_report"]["outcome"], "failed")
        self.assertEqual(pending["retry_count"], 1)
        self.assertEqual(self.view.snapshot()["projects"][0]["raw"]["pending"], 2)

    def test_publication_status_remains_visible_when_job_is_failed(self):
        self.collect()
        self.runtime.drain()
        job = self.view.jobs(stage="skill")["items"][0]
        self.runtime.store.update_job(job["id"], state="failed", error="interrupted after publication",
                                      report={"outcome": "failed"}, report_sent=0)
        self.assertEqual(self.view.job(job["id"])["content_status"], "published")
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE versions SET state='prepared' WHERE id=?", (job["version_id"],))
        self.assertEqual(self.view.job(job["id"])["content_status"], "publication_pending")

    def test_report_retry_exposes_the_new_attempt_without_reusing_the_old_error(self):
        self.collect()
        FakeSession.fail_report = True
        self.runtime.drain()
        job = self.view.jobs(stage="skill")["items"][0]

        def report(session, payload):
            sending = self.view.job(job["id"])
            self.assertEqual(sending["report_status"], "unconfirmed")
            self.assertIsNone(sending["report_error"])
            self.assertEqual(sending["content_status"], "published")
            return "Report delivered"

        with patch.object(FakeSession, "report", autospec=True, side_effect=report):
            self.runtime.retry(job["id"])
        self.assertEqual(self.view.job(job["id"])["report_status"], "sent")

    def test_shared_skill_queues_and_disabled_history(self):
        second = self.root / "second"
        second.mkdir()
        key2 = self.runtime.store.project(str(second))
        shared = self.runtime.store.skills(self.key)[0]["id"]
        with self.runtime.store.transaction() as db:
            db.execute("INSERT INTO project_skills VALUES(?,?)", (key2, shared))
        for project in (self.project, second):
            self.runtime.put_wiki(str(project), [{"name": "build", "body": "Project JDK"}])
        self.runtime.schedule()
        active = next(j for j in self.view.jobs()["items"] if j["skill"] == shared)
        others = next(p for p in self.view.snapshot()["projects"] if p["id"] != active["project"])
        queue = next(s["wiki"] for s in others["skills"] if s["id"] == shared)
        self.assertEqual((queue["reason"], queue["pending"], queue["batched"]), ("shared_busy", 1, 0))
        self.runtime.drain()
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE skills SET owned=0 WHERE id=?", (shared,))
        self.assertFalse(self.view.skill(shared)["enabled"])
        self.assertTrue(self.view.skill(shared)["versions"]["items"])

    def test_token_and_call_limits_are_absent_from_current_settings(self):
        saved = self.client.get('/api/settings').json()
        for name in ('max_tokens', 'context_window', 'analysis_input_tokens', 'analysis_max_calls'):
            self.assertNotIn(name, saved)
            self.assertEqual(self.client.put('/api/settings', json={name: 1}).status_code, 409)

    def test_config_errors_limits_and_local_access(self):
        self.assertEqual(self.client.get("/api/jobs?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/jobs?offset=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/jobs?state=wrong").status_code, 422)
        self.assertEqual(self.client.post("/api/snapshot").status_code, 405)
        self.assertEqual(self.client.get("/api/snapshot", headers={"Host": "attacker.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/snapshot", headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/snapshot", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        (self.view.root / "config.json").write_text('{"analysis_interval_minutes": 0}')
        data = self.client.get("/api/snapshot").json()
        self.assertIsNone(data["config"])
        self.assertIn("analysis_interval_minutes", data["config_error"])
        self.assertEqual(data["projects"][0]["raw"]["reason"], "config_error")

    def test_paginated_queries_and_old_unrecorded_timestamps(self):
        self.collect()
        self.runtime.drain()
        first = self.view.jobs(limit=1)
        second = self.view.jobs(offset=first["next_offset"], limit=1)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertIsNone(second["next_offset"])
        with self.runtime.store.transaction() as db:
            db.execute("DROP TABLE runtime_events")
            db.execute("DROP TABLE worker_runtime")
        self.assertEqual(self.view.snapshot()["worker"]["status"], "unknown")
        job = self.view.job(first["items"][0]["id"])
        self.assertIsNone(job["started_at"])
        self.assertIsNone(job["finished_at"])
        self.assertEqual(self.view.events(job["id"])["items"], [])

    def test_wiki_search_matches_names_and_full_current_body_as_literal_text(self):
        self.runtime.put_wiki(str(self.project), [
            {"name": "build-guide", "body": "Compile the project"},
            {"name": "details", "body": "x" * 200 + " 中文 MiXeD Straße 100% foo_bar a+b &? #tag 'quote'"},
            {"name": "plain", "body": "100 percent fooXbar"},
        ])
        path = f"/api/projects/{self.key}/wiki"
        before = self.view.path.read_bytes()
        for term, expected in [
            ("BUILD-GUIDE", ["build-guide"]), ("COMPILE", ["build-guide"]),
            ("中文", ["details"]), ("mixed", ["details"]),
            ("STRASSE", ["details"]), ("%", ["details"]),
            ("_", ["details"]), ("foo_bar", ["details"]),
            ("a+b &? #tag", ["details"]), ("'quote'", ["details"]),
            ("' OR 1=1 --", []), ("not present", []),
        ]:
            with self.subTest(q=term):
                response = self.client.get(path, params={"q": term})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([item["name"] for item in response.json()["items"]], expected)
                self.assertIsNone(response.json()["next_offset"])
        self.assertEqual(self.client.get(path).json(), self.client.get(path, params={"q": ""}).json())
        self.assertEqual(before, self.view.path.read_bytes())

    def test_wiki_search_filters_before_pagination_and_excludes_other_projects_and_history(self):
        self.runtime.put_wiki(str(self.project), [
            {"name": "a-unrelated", "body": "No match"},
            {"name": "b-result", "body": "Needle one"},
            {"name": "c-result", "body": "needle two"},
            {"name": "d-old", "body": "needle old-only"},
        ])
        self.runtime.put_wiki(str(self.project), [{"name": "d-old", "body": "Replacement text"}])
        other = self.root / "other"
        other.mkdir()
        self.runtime.put_wiki(str(other), [{"name": "foreign", "body": "needle foreign-only"}])
        path = f"/api/projects/{self.key}/wiki"
        before = self.runtime.store.rows("SELECT * FROM wiki ORDER BY project,name")
        history = self.runtime.store.rows("SELECT * FROM wiki_changes ORDER BY id")
        first = self.client.get(path, params={"q": "NEEDLE", "limit": 1}).json()
        self.assertEqual([item["name"] for item in first["items"]], ["b-result"])
        self.assertEqual(first["next_offset"], 1)
        second = self.client.get(path, params={"q": "NEEDLE", "limit": 1, "offset": first["next_offset"]}).json()
        self.assertEqual([item["name"] for item in second["items"]], ["c-result"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.view.wiki(self.key, offset=2, limit=1, q="needle")["items"], [])
        self.assertEqual([item["name"] for item in self.view.wiki(self.key)["items"]],
                         ["a-unrelated", "b-result", "c-result", "d-old"])
        for term in ("old-only", "foreign-only"):
            self.assertEqual(self.client.get(path, params={"q": term}).json()["items"], [])
        self.assertEqual(self.client.get("/api/projects/missing/wiki", params={"q": "needle"}).status_code, 404)
        self.assertEqual(self.client.get(path, params={"q": "needle", "offset": -1}).status_code, 422)
        self.assertEqual(self.client.get(path, params={"q": "needle", "limit": 0}).status_code, 422)
        self.assertEqual(before, self.runtime.store.rows("SELECT * FROM wiki ORDER BY project,name"))
        self.assertEqual(history, self.runtime.store.rows("SELECT * FROM wiki_changes ORDER BY id"))

    def test_heartbeat_continues_during_generation_and_stale_transition_notifies(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingSession(FakeSession):
            def generate(self, stage, context):
                entered.set()
                release.wait(3)
                return super().generate(stage, context)

        self.collect()
        job_id = self.runtime.schedule()[0]
        with patch.object(WorkerHeartbeat, "interval", 0.02), WorkerHeartbeat(self.runtime.store) as heartbeat:
            runtime = Runtime(self.runtime.config, BlockingSession, heartbeat)
            runner = threading.Thread(target=runtime.run_job, args=(job_id,))
            runner.start()
            try:
                self.assertTrue(entered.wait(2))
                first = self.view.snapshot()["worker"]
                time.sleep(0.08)
                second = self.view.snapshot()["worker"]
                self.assertGreater(second["heartbeat"], first["heartbeat"])
                self.assertEqual(second["job_id"], job_id)
                self.assertTrue(self.view.job(job_id)["running_confirmed"])
            finally:
                release.set()
                runner.join(3)
            self.assertFalse(runner.is_alive())
        self.assertEqual(self.view.snapshot()["worker"]["status"], "stopped")
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE worker_runtime SET state='online',heartbeat=? WHERE id=1", (time.time(),))
        cursor = ChangeCursor(self.view)
        try:
            current, _ = cursor.poll()
            with patch("wikiskill.live.views.time.time", return_value=time.time() + 20):
                later, _ = cursor.poll()
                self.assertNotEqual(current, later)
                self.assertEqual(self.view.snapshot()["worker"]["status"], "stale")
        finally:
            cursor.close()

    def test_cli_server_stream_observes_other_process_writes_and_reconnects(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-m", "wikiskill.live.cli", "--root", str(self.view.root),
                                    "web", "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stream = None
        base = f"http://127.0.0.1:{port}"

        def read_event(response):
            lines = []
            while True:
                content = response.readline()
                if not content:
                    self.fail("Event stream ended before a complete event")
                line = content.decode().strip()
                if not line:
                    if lines:
                        return lines
                else:
                    lines.append(line)

        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with urllib.request.urlopen(base + "/api/snapshot", timeout=1) as response:
                        self.assertEqual(response.status, 200)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        self.fail("Web server did not start")
                    time.sleep(0.02)
            stream = urllib.request.urlopen(base + "/api/events", timeout=5)
            self.assertIn("text/event-stream", stream.headers["Content-Type"])
            first = read_event(stream)
            self.collect()
            second = read_event(stream)
            first_id = next(line for line in first if line.startswith("id:"))
            second_id = next(line for line in second if line.startswith("id:"))
            self.assertNotEqual(first_id, second_id)
            stream.close()
            stream = urllib.request.urlopen(urllib.request.Request(base + "/api/events", headers={"Last-Event-ID": first_id[4:]}), timeout=5)
            self.assertIn(second_id, read_event(stream))
            with urllib.request.urlopen(base + "/api/snapshot", timeout=2) as response:
                self.assertEqual(json.load(response)["projects"][0]["raw"]["pending"], 2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                self.fail("Web server did not stop with an open SSE connection")
            finally:
                if stream:
                    stream.close()
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
