import contextlib
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from wikiskill.live.cli import main
from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.services import ensure_services, service_status, stop_services, _held, _descriptor, _request, _spawn
from wikiskill.live.skills import file_lock


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = self.directory / "data with spaces"
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.directory / "empty-codex")})
        self.env.start()

    def tearDown(self):
        stop_services(self.root)
        self.env.stop()
        self.temp.cleanup()

    def test_first_launch_browser_fallback_repeat_and_stop(self):
        output = io.StringIO()
        with patch("webbrowser.open", return_value=False) as browser, contextlib.redirect_stdout(output):
            self.assertEqual(main(["--root", str(self.root)]), 0)
        browser.assert_called_once()
        self.assertTrue(browser.call_args.args[0].endswith("/#/system"))
        self.assertIn("请在浏览器打开", output.getvalue())
        status = service_status(self.root)
        self.assertEqual(status["state"], "running")
        with urllib.request.urlopen(status["url"]) as response:
            self.assertIn(b"<html", response.read().lower())
        config = Config.load(self.root)
        self.assertFalse(config.raw_auto)
        self.assertFalse(config.wiki_auto)
        self.assertEqual((self.root / "service.json").stat().st_mode & 0o777, 0o600)
        with patch("webbrowser.open", side_effect=OSError("no desktop")) as browser, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root)]), 0)
        self.assertTrue(browser.call_args.args[0].endswith("/#/manage"))
        self.assertEqual(service_status(self.root)["pid"], status["pid"])
        self.assertEqual(stop_services(self.root)["state"], "stopped")
        self.assertEqual(service_status(self.root)["state"], "stopped")
        for name in ("worker", "collector", "service"):
            self.assertFalse(_held(self.root / f"locks/{name}-process.lock"))
        self.assertTrue((self.root / "state.sqlite3").exists())
        self.assertTrue((self.root / "config.json").exists())

    def test_concurrent_launch_and_occupied_port(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: ensure_services(self.root, port), range(3)))
            self.assertEqual(len({r["pid"] for r in results}), 1)
            self.assertNotEqual(results[0]["url"], f"http://127.0.0.1:{port}")
            self.assertEqual(sum(not r["reused"] for r in results), 1)

    def test_manual_start_ignores_auto_start_and_shell_exit_keeps_background(self):
        Runtime(Config(self.root, auto_start=False, codex_home=str(self.directory / "empty-codex")))
        run = subprocess.run([sys.executable, "-m", "wikiskill.live.cli", "--root", str(self.root), "start"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["state"], "running")
        self.assertEqual(service_status(self.root)["state"], "running")
        with patch("wikiskill.live.services.ensure_services") as ensure:
            self.assertEqual(Runtime(Config.load(self.root)).wake(), {"worker": "manual"})
            ensure.assert_not_called()
        with patch("webbrowser.open") as browser, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "--no-open"]), 0)
            self.assertEqual(main(["--root", str(self.root), "start"]), 0)
            browser.assert_not_called()

    def test_automatic_wake_reuses_panel_and_never_opens_browser(self):
        runtime = Runtime(Config(self.root, codex_home=str(self.directory / "empty-codex")))
        with patch("webbrowser.open") as browser:
            first = runtime.wake()
            second = runtime.wake()
            browser.assert_not_called()
        self.assertEqual(first["state"], "running")
        self.assertEqual(first["pid"], second["pid"])

    def test_service_control_requires_token_and_same_origin(self):
        status = ensure_services(self.root)
        url = status["url"]
        descriptor = _descriptor(self.root)
        for headers in ({}, {"X-WikiSkill-Token": "wrong"},
                        {"X-WikiSkill-Token": descriptor["token"], "Origin": "https://example.org"}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + "/api/service/stop", method="POST", headers=headers))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        with urllib.request.urlopen(url + "/api/services") as response:
            self.assertNotIn(descriptor["token"], response.read().decode())
        self.assertEqual(service_status(self.root)["state"], "running")
        bad = {**descriptor, "token": "x" * 64}
        (self.root / "service.json").write_text(json.dumps(bad))
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                stop_services(self.root)
            error.exception.close()
            self.assertEqual(_request(self.root, descriptor)["state"], "running")
        finally:
            (self.root / "service.json").write_text(json.dumps(descriptor))

    def test_separately_started_worker_is_preserved_and_failed_start_cleans_up(self):
        self.root.mkdir()
        with file_lock(self.root / "locks/worker-process.lock"):
            with self.assertRaisesRegex(RuntimeError, "could not start"):
                ensure_services(self.root)
            self.assertTrue(_held(self.root / "locks/worker-process.lock"))
            self.assertFalse(_held(self.root / "locks/collector-process.lock"))
            self.assertFalse(_held(self.root / "locks/service-process.lock"))

    def test_read_status_and_stop_on_absent_directory(self):
        self.assertEqual(service_status(self.root)["state"], "stopped")
        self.assertEqual(stop_services(self.root)["state"], "stopped")
        self.assertFalse(self.root.exists())

    def test_directory_alias_uses_same_health_identity_and_service(self):
        status = ensure_services(self.root)
        alias = self.directory / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(_request(alias, _descriptor(alias))["state"], "running")
        self.assertEqual(ensure_services(alias)["pid"], status["pid"])
        self.assertEqual(stop_services(alias)["state"], "stopped")

    def test_launching_process_reaps_service_after_stop(self):
        children = []
        def spawn(*args):
            child = _spawn(*args)
            children.append(child)
            return child
        with patch("wikiskill.live.services._spawn", side_effect=spawn):
            status = ensure_services(self.root)
        self.assertEqual(children[0].pid, status["pid"])
        stop_services(self.root)
        deadline = time.monotonic() + 5
        while children[0].returncode is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(children[0].returncode)

    def test_degraded_service_restarts_owned_child(self):
        first = ensure_services(self.root)
        with urllib.request.urlopen(first["url"] + "/api/snapshot") as response:
            worker_pid = json.load(response)["worker"]["pid"]
        os.kill(worker_pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service_status(self.root)["worker"] == "running":
            time.sleep(0.05)
        self.assertEqual(service_status(self.root)["state"], "degraded")
        repaired = ensure_services(self.root)
        self.assertEqual(repaired["state"], "running")
        self.assertEqual(repaired["pid"], first["pid"])

    def test_two_data_directories_never_reuse_each_other(self):
        first = ensure_services(self.root)
        other = self.directory / "other"
        try:
            second = ensure_services(other)
            self.assertNotEqual(first["pid"], second["pid"])
            self.assertNotEqual(first["url"], second["url"])
            self.assertNotEqual(_descriptor(self.root)["token"], _descriptor(other)["token"])
            stop_services(other)
            self.assertEqual(service_status(self.root)["state"], "running")
        finally:
            stop_services(other)


if __name__ == "__main__":
    unittest.main()
