import base64
import hashlib
import io
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wikiskill.live.config import Config, digest
from wikiskill.live.runtime import Runtime, write_pages
from wikiskill.live.web import create_app


def archive(entries):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w") as bundle:
            for name, body in entries:
                bundle.writestr(name, body)
    return base64.b64encode(output.getvalue()).decode("ascii")


class WikiTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.directory = self.root / "project"
        self.directory.mkdir()
        self.runtime = Runtime(Config(self.root / "data", auto_start=False,
                                      codex_home=str(self.root / "codex"),
                                      install_directory=str(self.root / "codex" / "skills"),
                                      api_key="fake-config-only-secret"))
        self.project = self.runtime.store.project(str(self.directory))
        self.client = TestClient(create_app(self.runtime.config.root), base_url="http://127.0.0.1",
                                 raise_server_exceptions=False)
        self.base = f"/api/projects/{self.project}"
        self.put("build", "# Current\n")
        self.put("same", "# Same\r\n")

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def put(self, name, body):
        return self.runtime.put_wiki(str(self.directory), [{"name": name, "body": body}], {"kept": name})

    def state(self):
        return {table: self.runtime.store.rows(f"SELECT * FROM {table} ORDER BY rowid")
                for table in ("wiki", "wiki_changes", "runtime_events", "projects", "skills", "jobs")}

    def preview(self, data):
        response = self.client.post(self.base + "/wiki-import/preview", json={"archive_base64": data})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, data, preview, overwrite=()):
        return self.client.post(self.base + "/wiki-import", json={
            "archive_base64": data, "preview_token": preview["preview_token"], "overwrite": list(overwrite)})

    def test_export_all_and_selected_current_bodies_only(self):
        self.put("build", "# Latest\n  exact spaces  \n")
        for number in range(25):
            self.put(f"page-{number:02}", f"# Page {number}\n")
        expected = {r["name"] + ".md": r["body"].encode() for r in self.state()["wiki"]}
        before = self.state()
        response = self.client.get(self.base + "/wiki-export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            self.assertEqual({name: bundle.read(name) for name in bundle.namelist()}, expected)
        response = self.client.post(self.base + "/wiki-export", json={"pages": ["page-24", "build"]})
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            self.assertEqual(bundle.namelist(), ["build.md", "page-24.md"])
            self.assertEqual(bundle.read("build.md"), expected["build.md"])
        self.assertEqual(self.state(), before)
        for names in ([], ["build", "build"], ["build", "missing"], ["../build"], [1], None):
            response = self.client.post(self.base + "/wiki-export", json={"pages": names})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("detail", response.json())
        self.assertEqual(self.state(), before)

    def test_preview_is_read_only_and_exposes_all_three_statuses(self):
        data = archive([("new.md", "# New\n"), ("build.md", "# Updated\n"), ("same.md", "# Same\r\n")])
        state = self.state()
        files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.runtime.config.root.rglob("*") if path.is_file()}
        preview = self.preview(data)
        self.assertEqual(preview["counts"], {"added": 1, "modified": 1, "unchanged": 1})
        build = next(page for page in preview["pages"] if page["name"] == "build")
        self.assertEqual(build["before"], "# Current\n")
        self.assertEqual(build["body"], "# Updated\n")
        self.assertEqual(build["expected_digest"], digest("# Current\n"))
        self.assertIn("-# Current", build["diff"])
        self.assertIn("+# Updated", build["diff"])
        self.assertEqual(self.state(), state)
        self.assertEqual(files, {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in self.runtime.config.root.rglob("*") if path.is_file()})

    def test_explicit_confirmation_atomic_write_history_and_no_change(self):
        data = archive([("new.md", "# New\n"), ("build.md", "# Updated\n"), ("same.md", "# Same\r\n")])
        preview = self.preview(data)
        before = self.state()
        for confirmation in ([], ["same"], ["build", "build"], ["build", "new"]):
            self.assertEqual(self.submit(data, preview, confirmation).status_code, 409)
            self.assertEqual(self.state(), before)
        result = self.submit(data, preview, ["build"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["new_changes"], 2)
        after = self.state()
        self.assertEqual([r["body"] for r in after["wiki_changes"]], ["# Current\n", "# Same\r\n", "# Updated\n", "# New\n"])
        self.assertEqual(next(r for r in before["wiki"] if r["name"] == "same"), next(r for r in after["wiki"] if r["name"] == "same"))
        self.assertEqual(len(after["runtime_events"]), len(before["runtime_events"]) + 1)
        self.assertEqual(self.submit(data, preview, ["build"]).status_code, 409)
        unchanged = self.preview(data)
        self.assertEqual(unchanged["counts"]["unchanged"], 3)
        self.assertEqual(self.submit(data, unchanged).json()["new_changes"], 0)
        self.assertEqual(self.state(), after)

    def test_any_project_edit_after_preview_rejects_whole_import(self):
        data = archive([("build.md", "# Imported\n"), ("new.md", "# New\n")])
        for name, body, restore in (("build", "# Edited", False), ("same", "# Other edit", False),
                                    ("build", "# Edited  \n", False), ("build", "# Temporary", True)):
            with self.subTest(name=name, body=body, restore=restore):
                preview = self.preview(data)
                original = next(r["body"] for r in self.state()["wiki"] if r["name"] == name)
                self.put(name, body)
                if restore:
                    self.put(name, original)
                before = self.state()
                response = self.submit(data, preview, ["build"])
                self.assertEqual(response.status_code, 409)
                self.assertIn("重新预览", response.json()["detail"])
                self.assertEqual(self.state(), before)
        refreshed = self.preview(data)
        self.assertEqual(self.submit(data, refreshed, ["build"]).status_code, 200)

    def test_preview_binds_archive_project_and_application(self):
        data = archive([("build.md", "# Imported\n")])
        preview = self.preview(data)
        before = self.state()
        changed = archive([("build.md", "# Different ZIP\n")])
        self.assertEqual(self.submit(changed, preview, ["build"]).status_code, 409)
        with TestClient(create_app(self.runtime.config.root), base_url="http://127.0.0.1") as restarted:
            response = restarted.post(self.base + "/wiki-import", json={"archive_base64": data,
                                      "preview_token": preview["preview_token"], "overwrite": ["build"]})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state(), before)
        other = self.root / "other"
        other.mkdir()
        key = self.runtime.store.project(str(other))
        response = self.client.post(f"/api/projects/{key}/wiki-import", json={"archive_base64": data,
                                    "preview_token": preview["preview_token"], "overwrite": ["build"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.runtime.store.rows("SELECT * FROM wiki WHERE project=?", (key,)), [])

    def test_whitespace_import_has_version_and_mid_write_failure_rolls_back(self):
        data = archive([("build.md", "# Current  \n")])
        preview = self.preview(data)
        self.assertEqual(self.submit(data, preview, ["build"]).json()["new_changes"], 1)
        self.assertEqual(self.state()["wiki_changes"][-1]["body"], "# Current  \n")
        data = archive([("aaa-new.md", "# First"), ("build.md", "# Last")])
        preview = self.preview(data)
        before = self.state()

        def fail_after_first(db, project, pages, metadata, job_id, **kwargs):
            write_pages(db, project, pages[:1], metadata, job_id, **kwargs)
            raise RuntimeError("injected write failure")

        with patch("wikiskill.live.wiki_transfer.write_pages", side_effect=fail_after_first):
            self.assertEqual(self.submit(data, preview, ["build"]).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_invalid_archives_fail_before_any_page_is_written(self):
        invalid = [("base64", "%%%"), ("ZIP", base64.b64encode(b"not a zip").decode()),
                   ("没有", archive([])), ("非法页面名称", archive([("../outside.md", "# Escape")])),
                   ("非法页面名称", archive([("folder/a.md", "# Nested")])),
                   ("非法页面名称", archive([("Upper.md", "# Name")])),
                   ("非法页面名称", archive([("a.txt", "# Extension")])),
                   ("重复", archive([("a.md", "# First"), ("a.md", "# Second")])),
                   ("UTF-8", archive([("a.md", b"\xff")])),
                   ("正文为空", archive([("a.md", " \r\n\t")])),
                   ("正文为空", archive([("a.md", "\ufeff")])),
                   ("单页", archive([("a.md", "x" * (2 * 1024 * 1024 + 1))]))]
        symlink = zipfile.ZipInfo("link.md")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        invalid.append(("普通", archive([(symlink, "target")])) )
        corrupted = bytearray(base64.b64decode(archive([("a.md", b"body-to-corrupt")])))
        corrupted[corrupted.index(b"body-to-corrupt")] ^= 1
        invalid.append(("无效 ZIP", base64.b64encode(corrupted).decode()))
        before = self.state()
        for message, data in invalid:
            with self.subTest(message=message):
                for endpoint, extra in (("/wiki-import/preview", {}), ("/wiki-import", {"preview_token": "0" * 64, "overwrite": []})):
                    response = self.client.post(self.base + endpoint, json={"archive_base64": data, **extra})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn(message, response.json()["detail"])
                self.assertEqual(self.state(), before)
        mixed = archive([("aaa-new.md", "# Valid"), ("zzz.md", b"\xff")])
        self.assertEqual(self.client.post(self.base + "/wiki-import/preview", json={"archive_base64": mixed}).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_missing_project_and_cross_origin_requests_are_rejected(self):
        data = archive([("a.md", "# A")])
        absent = self.root / "absent"
        with TestClient(create_app(absent), base_url="http://127.0.0.1") as client:
            self.assertEqual(client.get("/api/projects/missing/wiki-export").status_code, 404)
            self.assertEqual(client.post("/api/projects/missing/wiki-import/preview", json={"archive_base64": data}).status_code, 404)
        self.assertFalse(absent.exists())
        self.assertEqual(self.client.get(self.base + "/wiki-export", headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.post(self.base + "/wiki-import/preview", json={"archive_base64": data},
                                         headers={"Origin": "https://other.example"}).status_code, 403)
