import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wikiskill.live.cli import main
from wikiskill.live.config import Config
from wikiskill.live.doctor import diagnose, format_report


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "data"
        self.root.mkdir()
        self.codex = self.base / "codex"
        self.codex.mkdir()
        for name in ("sessions", "archived_sessions", "skills"):
            (self.codex / name).mkdir()
        for name in ("skills", "reports", "locks"):
            (self.root / name).mkdir()
        (self.root / "state.sqlite3").write_bytes(b"Doctor only inspects metadata.")
        (self.root / "worker.log").write_text("private log")
        self.binary = self.root / "codex-stub"
        self.binary.write_text("#!/bin/sh\nexit 99\n")
        self.binary.chmod(0o700)
        self.secret = "sk-doctor-private-token"
        self.values = {"codex_home": str(self.codex), "install_directory": str(self.codex / "skills"),
                       "codex_command": [str(self.binary), "app-server", "--private=" + self.secret],
                       "api_key": self.secret, "auto_start": True}
        self.config_file = self.root / "config.json"
        self.write_config()

    def write_config(self, **changes):
        self.values.update(changes)
        self.config_file.write_text(json.dumps(self.values), encoding="utf-8")
        self.config_file.chmod(0o600)

    def invoke(self, root=None, as_json=True):
        out, err = io.StringIO(), io.StringIO()
        args = ["--root", str(self.root if root is None else root), "doctor"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args + (["--json"] if as_json else []))
        self.assertEqual(err.getvalue(), "")
        self.assertNotIn(self.secret, out.getvalue())
        return code, out.getvalue()

    def checks(self, report=None):
        return {item["id"]: item for item in (report or diagnose(self.root))["checks"]}

    def snapshot(self):
        return {str(p.relative_to(self.base)): (p.stat().st_mode, p.stat().st_mtime_ns,
                p.read_bytes() if p.is_file() else None) for p in self.base.rglob("*")}

    def test_success_text_and_json_share_checks_and_status(self):
        code, output = self.invoke()
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(set(report), {"ok", "executor", "checks"})
        self.assertTrue(report["ok"])
        self.assertEqual(report["executor"], "codex")
        self.assertTrue(all(item["status"] == "ok" for item in report["checks"]))
        code, output = self.invoke(as_json=False)
        self.assertEqual(code, 0)
        self.assertEqual(output, format_report(report) + "\n")
        self.assertIn("环境诊断：通过", output)
        self.assertNotIn(str(self.root), output)

    def test_missing_root_is_reported_without_creation(self):
        missing = self.base / "absent" / "data"
        before = self.snapshot()
        code, output = self.invoke(missing)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["ok"])
        self.assertIn("init", output)
        self.assertFalse(missing.parent.exists())
        self.assertEqual(self.snapshot(), before)

    def test_root_file_and_invalid_root_are_safe(self):
        for root in (self.binary, str(self.base) + "/\0" + self.secret, "~unknown-user-doctor/" + self.secret):
            with self.subTest(root_type=type(root).__name__):
                code, output = self.invoke(root)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output)["checks"][0]["id"], "root")

    def test_missing_config_is_reported_without_initializing(self):
        self.config_file.unlink()
        before = self.snapshot()
        code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.checks(json.loads(output))["config_file"]["status"], "error")
        self.assertEqual(self.snapshot(), before)

    def test_config_directory_and_fifo_are_rejected_without_reading(self):
        self.config_file.unlink()
        self.config_file.mkdir()
        self.assertEqual(self.invoke()[0], 1)
        self.config_file.rmdir()
        os.mkfifo(self.config_file)
        self.assertEqual(self.invoke()[0], 1)

    def test_bad_json_unknown_fields_and_wrong_types_are_diagnostic(self):
        inputs = [b'{"api_key":"' + self.secret.encode() + b'",', b'\xff\xfe',
                  json.dumps([self.secret]).encode(), json.dumps({self.secret: 1}).encode()]
        for changes in ({"executor": []}, {"api_provider": {}}, {"api_url": 123},
                        {"api_url": []}, {"api_url": "http://[" + self.secret},
                        {"codex_home": 3}, {"api_model": {}}, {"api_key": []},
                        {"codex_command": []}, {"model": True}, {"raw_threshold": True},
                        {"poll_seconds": 0}, {"auto_start": "true"}):
            inputs.append(json.dumps({**self.values, **changes}).encode())
        for index, content in enumerate(inputs):
            with self.subTest(case=index):
                self.config_file.write_bytes(content)
                before = self.snapshot()
                code, output = self.invoke()
                self.assertEqual(code, 1)
                check = self.checks(json.loads(output))["config"]
                self.assertEqual(check["status"], "error")
                self.assertTrue(check["repair"])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.invoke(as_json=False)[0], 1)

    def test_sensitive_url_model_command_and_exceptions_stay_private(self):
        urls = ["https://user:" + self.secret + "@example.invalid/v1",
                "https://example.invalid/v1?key=" + self.secret]
        for url in urls:
            self.write_config(api_url=url)
            for as_json in (True, False):
                code, output = self.invoke(as_json=as_json)
                self.assertEqual(code, 1)
                self.assertNotIn(url, output)
                self.assertIn("api_url", output)
        self.write_config(executor="api", api_url="https://example.invalid/v1", api_model=self.secret)
        self.assertEqual(self.invoke()[0], 0)
        with patch.object(Config, "load", side_effect=PermissionError(self.secret)):
            self.assertEqual(self.invoke()[0], 1)

    def test_api_mode_missing_model_blocks_but_codex_is_optional(self):
        self.write_config(executor="api", codex_command=[str(self.base / "missing")])
        self.assertEqual(self.invoke()[0], 1)
        checks = self.checks()
        self.assertEqual(checks["api_model"]["status"], "error")
        self.assertEqual(checks["codex_command"]["status"], "warning")
        for provider in ("chat_completions", "gemini"):
            self.write_config(api_model="a-model", api_provider=provider)
            self.assertEqual(self.invoke()[0], 0)

    def test_empty_api_key_only_reports_missing_and_supports_unauthenticated_services(self):
        self.write_config(executor="api", api_model="local-model", api_key="")
        code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(self.checks(json.loads(output))["api_key"]["message"], "未配置。")
        self.assertEqual(self.checks()["api_key"]["status"], "warning")
        self.write_config(executor="codex")
        self.assertEqual(self.checks()["api_key"]["status"], "ok")

    def test_api_invalid_port_blocks_without_echo(self):
        self.write_config(executor="api", api_model="model", api_url="http://example.invalid:" + self.secret)
        code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.checks(json.loads(output))["api_endpoint"]["status"], "error")

    def test_codex_requires_executable_with_execute_permission(self):
        for command in ([str(self.base / "missing")], [str(self.root)], [str(self.binary)]):
            self.write_config(codex_command=command)
            self.binary.chmod(0o600)
            self.assertEqual(self.checks()["codex_command"]["status"], "error")
            self.assertEqual(self.invoke()[0], 1)

    def test_relative_executable_and_path_entries_use_data_root(self):
        self.write_config(codex_command=["./codex-stub"])
        self.assertEqual(self.checks()["codex_command"]["status"], "ok")
        self.write_config(codex_command=["codex-stub"])
        for value in (".", "", str(self.root)):
            with patch.dict(os.environ, {"PATH": value}):
                self.assertEqual(self.checks()["codex_command"]["status"], "ok")

    def test_missing_generated_paths_are_warnings_with_writable_parent(self):
        for name in ("skills", "reports", "locks"):
            (self.root / name).rmdir()
        (self.root / "state.sqlite3").unlink()
        (self.root / "worker.log").unlink()
        self.write_config(codex_home=str(self.base / "future-codex"),
                          install_directory=str(self.base / "future-install" / "skills"))
        before = self.snapshot()
        code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("warning", output)
        self.assertEqual(self.snapshot(), before)

    def test_existing_wrong_path_types_block(self):
        paths = [("data.skills", self.root / "skills"), ("codex.sessions", self.codex / "sessions"),
                 ("install_directory", self.codex / "skills")]
        for check_id, path in paths:
            with self.subTest(check_id=check_id):
                path.rmdir()
                path.write_text("wrong type")
                self.assertEqual(self.checks()[check_id]["status"], "error")
                path.unlink()
                path.mkdir()
        (self.root / "state.sqlite3").unlink()
        (self.root / "state.sqlite3").mkdir()
        self.assertEqual(self.checks()["database"]["status"], "error")

    def test_permission_failures_block_without_probing_by_write(self):
        original = os.access
        for path, check_id in ((self.root, "root"), (self.config_file, "config_file"),
                               (self.root / "reports", "data.reports"), (self.codex / "skills", "install_directory")):
            with self.subTest(check_id=check_id), patch("os.access", side_effect=lambda p, m:
                    False if Path(p) == path else original(p, m)):
                self.assertEqual(self.checks()[check_id]["status"], "error")
        self.write_config(install_directory=str(self.base / "future-install" / "skills"))
        with patch("os.access", side_effect=lambda p, m: False if Path(p) == self.base else original(p, m)):
            self.assertEqual(self.checks()["install_directory"]["status"], "error")

    def test_broken_symlinks_report_errors(self):
        (self.root / "reports").rmdir()
        (self.root / "reports").symlink_to(self.base / "missing")
        self.assertEqual(self.checks()["data.reports"]["status"], "error")

    def test_loose_config_permissions_warn_without_chmod(self):
        self.config_file.chmod(0o644)
        self.assertEqual(self.checks()["config_permissions"]["status"], "warning")
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.config_file.stat().st_mode & 0o777, 0o644)

    def test_diagnosis_has_no_runtime_database_file_process_or_network_effects(self):
        import socket
        import urllib.request

        before = self.snapshot()
        original_open = io.open

        def read_only_open(file, mode="r", *args, **kwargs):
            self.assertFalse(any(flag in mode for flag in "wax+"), "Doctor tried to write")
            return original_open(file, mode, *args, **kwargs)

        with contextlib.ExitStack() as stack:
            for target in ("wikiskill.live.config.Config.initialize", "wikiskill.storage.atomic_text",
                           "pathlib.Path.mkdir", "sqlite3.connect", "subprocess.Popen",
                           "socket.socket", "urllib.request.urlopen"):
                stack.enter_context(patch(target, side_effect=AssertionError("Unexpected side effect")))
            stack.enter_context(patch("io.open", side_effect=read_only_open))
            stack.enter_context(patch.dict(sys.modules, {"wikiskill.live.runtime": None, "wikiskill.live.mcp": None,
                                                        "wikiskill.live.models": None, "wikiskill.live.codex": None}))
            for executor in ("codex", "api"):
                # The fixture already uses Codex; API mode is tested with an in-memory config.
                config = Config(root=self.root, **{**self.values, "executor": executor, "api_model": "model"})
                with patch.object(Config, "load", return_value=config):
                    self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(self.invoke(self.base / "missing")[0], 1)
        self.assertEqual(self.snapshot(), before)

    def test_help_is_available_without_loading_configuration(self):
        for args in (["--help"], ["doctor", "--help"]):
            output = io.StringIO()
            with patch.object(Config, "load", side_effect=AssertionError("Help loaded config")), \
                    contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 0)
            self.assertIn("doctor" if args == ["--help"] else "--json", output.getvalue())


if __name__ == "__main__":
    unittest.main()
