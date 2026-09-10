import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.skill_generation import SkillGeneration
from wikiskill.live.skills import skill_text, snapshot
from wikiskill.live.web import create_app
from test_live_runtime import FakeSession


class SkillGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / "home", auto_start=False), FakeSession)
        self.key = self.runtime.store.project(str(self.project))
        self.flow = SkillGeneration(self.runtime)
        FakeSession.fail_generate = FakeSession.fail_report = FakeSession.no_change = False
        FakeSession.during_generate = None
        self.runtime.put_wiki(str(self.project), [
            {"name": "build", "body": "# Maven 构建\n使用 JDK 21 和 Maven 构建，保存编译日志。"},
            {"name": "tests", "body": "# Maven 测试\n运行 Maven 单元测试，检查测试报告。"},
            {"name": "billing", "body": "UNSELECTED_BILLING_SECRET"},
        ])

    def create(self, pages=None, name="build-guide"):
        preview = self.flow.preview(self.key, pages or ["build"])
        result = self.flow.enqueue(self.key, [p["name"] for p in preview["pages"]],
                                   {p["name"]: p["digest"] for p in preview["pages"]}, name=name)
        return result

    def test_selected_current_bodies_are_fixed_and_reusable(self):
        result = self.create(["build", "tests"])
        job = self.runtime.store.job(result["job_id"])
        self.assertEqual(len(job["inputs"]), 2)
        self.assertEqual({p["name"] for p in job["context"]["wiki"]}, {"build", "tests"})
        self.assertNotIn("UNSELECTED", str(job["context"]))
        self.runtime.put_wiki(str(self.project), [{"name": "build", "body": "LATER_EDIT"}])
        self.runtime.run_job(result["job_id"])
        self.assertEqual(self.runtime.store.job(result["job_id"])["state"], "done")
        self.assertNotIn("LATER_EDIT", str(self.runtime.store.job(result["job_id"])["context"]))
        repeated = self.create(["tests"], "test-guide")
        self.runtime.run_job(repeated["job_id"])
        self.assertEqual(self.runtime.store.job(repeated["job_id"])["state"], "done")
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM consumed_wiki WHERE change_id=?", (job["inputs"][1],))), 2)

    def test_cross_project_candidates_merge_and_preserve_resources(self):
        first = self.create()
        self.runtime.run_job(first["job_id"])
        target = self.runtime.skills.get(first["skill_id"])
        path = Path(target["path"])
        (path / "resource.bin").write_bytes(b"\x00\xff")
        second = self.root / "second"
        second.mkdir()
        other = self.runtime.store.project(str(second))
        self.runtime.put_wiki(str(second), [{"name": "jdk", "body": "Check JDK configuration before Maven build"}])
        preview = self.flow.preview(other, ["jdk"])
        candidate = next(c for c in preview["candidates"] if c["id"] == first["skill_id"])
        self.assertIn("jdk", candidate["matched_terms"])
        self.assertEqual(candidate["projects"][0]["id"], self.key)
        merged = self.flow.enqueue(other, ["jdk"], {p["name"]: p["digest"] for p in preview["pages"]},
                                   skill=candidate["id"], skill_digest=candidate["digest"])
        with self.assertRaisesRegex(ValueError, "未完成"):
            self.flow.enqueue(other, ["jdk"], {p["name"]: p["digest"] for p in preview["pages"]},
                              skill=candidate["id"], skill_digest=candidate["digest"])
        before = self.runtime.store.job(merged["job_id"])["context"]
        self.assertIn("JDK", before["skill_md"])
        self.assertIn("resource.bin", before["before_bundle"])
        with patch.object(FakeSession, "generate", lambda session, stage, context: {
                "summary": "Merged JDK workflow", "skill_md": context["skill_md"] + "\nMerged JDK workflow.\n"}):
            self.runtime.run_job(merged["job_id"])
        self.assertEqual(self.runtime.store.job(merged["job_id"])["state"], "done")
        self.assertEqual((path / "resource.bin").read_bytes(), b"\x00\xff")
        self.assertIn("Merged JDK workflow", skill_text(snapshot(path)))
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM versions WHERE skill=?", (candidate["id"],))), 2)
        self.assertEqual(merged["skill_id"], first["skill_id"])
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM project_skills WHERE skill=?", (candidate["id"],))), 2)

    def test_stale_wiki_and_skill_rejected_before_creating_job(self):
        preview = self.flow.preview(self.key, ["build"])
        expected = {p["name"]: p["digest"] for p in preview["pages"]}
        self.runtime.put_wiki(str(self.project), [{"name": "build", "body": "updated"}])
        with self.assertRaisesRegex(ValueError, "Wiki 正文已变化"):
            self.flow.enqueue(self.key, ["build"], expected, name="stale")
        self.assertFalse(self.runtime.store.rows("SELECT * FROM jobs"))
        result = self.create()
        self.runtime.run_job(result["job_id"])
        preview = self.flow.preview(self.key, ["build"])
        candidate = preview["candidates"][0]
        path = Path(self.runtime.skills.get(candidate["id"])["path"]) / "SKILL.md"
        path.write_text(path.read_text() + "\nUser edit\n")
        with self.assertRaisesRegex(ValueError, "Skill 内容已变化"):
            self.flow.enqueue(self.key, ["build"], {p["name"]: p["digest"] for p in preview["pages"]},
                              skill=candidate["id"], skill_digest=candidate["digest"])

    def test_invalid_names_and_selection_are_atomic(self):
        preview = self.flow.preview(self.key, ["build", "tests"])
        expected = {p["name"]: p["digest"] for p in preview["pages"]}
        for pages in ([], ["build", "build"], ["missing"], [{}]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.flow.preview(self.key, pages)
        with self.assertRaises(ValueError):
            self.flow.enqueue(self.key, ["build", "tests"], expected, name="../escape")
        result = self.create()
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(len(self.runtime.store.job(result["job_id"])["inputs"]), 1)

    def test_selected_pages_are_not_rejected_by_local_token_limits(self):
        small = Runtime(self.runtime.config, FakeSession)
        flow = SkillGeneration(small)
        preview = flow.preview(self.key, ["build", "tests"])
        expected = {p["name"]: p["digest"] for p in preview["pages"]}
        result = flow.enqueue(self.key, ["build", "tests"], expected, name="large-input")
        small.run_job(result["job_id"])
        self.assertEqual(small.store.job(result["job_id"])["state"], "done")
        self.assertEqual(len(small.store.job(result["job_id"])["inputs"]), 2)

    def test_failure_retry_keeps_selection_and_auto_targets_unlinked_pages(self):
        result = self.create()
        FakeSession.fail_generate = True
        self.runtime.run_job(result["job_id"])
        self.assertEqual(self.runtime.store.job(result["job_id"])["state"], "failed")
        FakeSession.fail_generate = False
        self.runtime.retry(result["job_id"], regenerate=True)
        self.runtime.run_job(result["job_id"])
        job = self.runtime.store.job(result["job_id"])
        self.assertEqual(job["state"], "done")
        self.assertNotIn("UNSELECTED", str(job["context"]))
        auto = Runtime(replace(self.runtime.config, capture_mode="automatic"), FakeSession)
        auto.schedule()
        jobs = self.runtime.store.rows("SELECT * FROM jobs WHERE state='queued'")
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(j["skill"] != result["skill_id"] for j in jobs))

    def test_http_preview_and_submit_new_or_merge(self):
        with TestClient(create_app(self.runtime.config.root), base_url="http://127.0.0.1") as client, patch.object(Runtime, "wake"):
            base = f"/api/projects/{self.key}/skill-generation"
            preview = client.post(base + "/preview", json={"pages": ["build"]})
            self.assertEqual(preview.status_code, 200)
            value = preview.json()
            self.assertEqual(value["suggested_name"], "build")
            response = client.post(base, json={"pages": ["build"], "expected": {p["name"]: p["digest"] for p in value["pages"]}, "name": "http-guide"})
            self.assertEqual(response.status_code, 200)
            self.runtime.run_job(response.json()["job_id"])
            self.assertEqual(self.runtime.store.job(response.json()["job_id"])["state"], "done")
            self.assertEqual(client.post(base + "/preview", json={"pages": []}).status_code, 409)
            self.assertEqual(client.post(base, json={"pages": ["build"]}).status_code, 409)
