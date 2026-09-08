import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from wikiskill.live.capture import Collector
from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.web import create_app
from test_live_capture import transcript
from test_live_runtime import FakeSession


class WorkflowTests(unittest.TestCase):
    def test_capture_http_conversion_authoring_install_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            config = Config(root / "data", codex_home=str(root / "codex"), install_directory=str(root / "installed"), auto_start=False)
            runtime = Runtime(config, FakeSession)
            FakeSession.starts, FakeSession.generations, FakeSession.reports = [], [], []
            FakeSession.fail_generate = FakeSession.fail_report = FakeSession.no_change = False
            FakeSession.during_generate = None
            collector = Collector(config)
            collector.scan()
            path = root / "codex/sessions/one.jsonl"
            path.parent.mkdir(parents=True)
            original = transcript(project)
            path.write_bytes(original)
            collector.scan()
            key = runtime.store.project(str(project))
            with TestClient(create_app(config.root), base_url="http://127.0.0.1") as client:
                records = client.get(f"/api/projects/{key}/traces").json()["items"]
                self.assertEqual(b"".join(base64.b64decode(r["bytes_base64"]) for r in records), original)
                response = client.post(f"/api/projects/{key}/convert", json={"stage": "raw"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(client.post(f"/api/projects/{key}/convert", json={"stage": "raw"}).status_code, 409)
                runtime.drain()
                wiki = client.get(f"/api/projects/{key}/wiki/build").json()
                update = {"pages": [{"name": "build", "body": "Updated by the user"}], "expected": {"build": wiki["digest"]}}
                self.assertEqual(client.put(f"/api/projects/{key}/wiki", json=update).status_code, 200)
                self.assertEqual(client.put(f"/api/projects/{key}/wiki", json=update).status_code, 409)
                skill = runtime.store.skills(key)[0]["id"]
                pending = client.get(f"/api/projects/{key}/wiki-inputs", params={"skill": skill}).json()["items"]
                self.assertEqual(len(pending), 2)
                self.assertEqual(client.post(f"/api/projects/{key}/convert", json={"stage": "skill", "skill": skill}).status_code, 200)
                runtime.drain()
                preview = client.get(f"/api/skills/{skill}/install").json()
                result = client.post(f"/api/skills/{skill}/install", json={"source_digest": preview["source_digest"], "target_digest": preview["target_digest"], "overwrite": False})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertTrue((Path(preview["target"]) / "SKILL.md").is_file())
                self.assertEqual(client.put("/api/settings", json={"api_key": "test-secret"}).status_code, 200)
                self.assertNotIn("test-secret", client.get("/api/snapshot").text)
                self.assertNotIn("test-secret", client.get("/api/settings").text)
                self.assertEqual(client.put("/api/settings", json={"raw_auto": True}, headers={"Origin": "https://attacker.example"}).status_code, 403)
                self.assertEqual(client.post("/api/history-import").status_code, 200)

    def test_capture_can_progress_while_generation_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root / "data", codex_home=str(root / "codex"), auto_start=False)
            collector = Collector(config)
            collector.scan()
            path = root / "codex/sessions/one.jsonl"
            path.parent.mkdir(parents=True)
            path.write_bytes(transcript(root))
            collector.scan()
            runtime = Runtime(config, FakeSession)
            key = runtime.store.project(str(root))
            job = runtime.enqueue(key, "raw")["job_id"]
            def during():
                with path.open("ab") as stream:
                    stream.write(b'{"type":"unknown","payload":"new while generating"}\n')
                self.assertEqual(collector.scan(), 1)
            with patch.object(FakeSession, "during_generate", during):
                runtime.run_job(job)
            self.assertEqual(runtime.store.status(key)["projects"][0]["raw_pending"], 1)
