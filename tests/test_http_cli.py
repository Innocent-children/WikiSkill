from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from wikiskill.config import build_engine
from wikiskill.demo import ScriptedModel, write_demo_dataset
from wikiskill.model import ChatCompletionsModel, ModelConfig, ModelError


@contextmanager
def model_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "body": request,
                             "authorization": self.headers.get("Authorization")})
            role = request["model"]
            if role == "unauthorized":
                self.send_response(401)
                self.end_headers()
                return
            if role == "retry" and len(received) == 1:
                self.send_response(429)
                self.end_headers()
                return
            if role == "malformed":
                body = b"not json"
            elif role in ("simple", "retry"):
                body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]}).encode()
            else:
                result = ScriptedModel(role).complete(request["messages"], request.get("tools", []))
                body = json.dumps({"choices": [{"message": result.message}], "usage": {"total_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class HttpTests(unittest.TestCase):
    def test_full_evolution_through_http_adapter(self):
        with tempfile.TemporaryDirectory() as temporary, model_server() as (url, received):
            directory = Path(temporary)
            write_demo_dataset(directory / "data")
            config = {"models": {role: {"base_url": url, "model": role, "api_key_env": "", "retries": 0}
                                  for role in ("inference", "maintainer", "proposer")}}
            engine = build_engine(directory / "run", directory / "data", config)
            engine.initialize()
            result = engine.evolve()
            self.assertEqual(result["best_score"], 1)
            self.assertEqual(result["iteration"], 5)
            self.assertTrue(all(request["path"] == "/v1/chat/completions" for request in received))
            proposals = [r for r in received if r["body"]["model"] == "proposer"]
            self.assertTrue(all(r["body"]["tool_choice"] == "auto" for r in proposals))
            self.assertTrue(any(message["role"] == "tool" for r in proposals for message in r["body"]["messages"]))
            self.assertEqual(engine.evaluate()["score"], 1)

    def test_auth_environment_and_nonretryable_http_error(self):
        with model_server() as (url, received), patch.dict(os.environ, {"WIKISKILL_TEST_KEY": "test-only-token"}):
            model = ChatCompletionsModel(ModelConfig(url, "simple", api_key_env="WIKISKILL_TEST_KEY"))
            self.assertEqual(model.complete([{"role": "user", "content": "hello"}], []).message["content"], "ok")
            self.assertEqual(received[0]["authorization"], "Bearer test-only-token")
            self.assertNotIn("test-only-token", json.dumps(model.identity))
            model = ChatCompletionsModel(ModelConfig(url, "unauthorized", api_key_env="", retries=2))
            with self.assertRaisesRegex(ModelError, "HTTP 401"):
                model.complete([], [])
            self.assertEqual(len(received), 2)

    def test_retry_then_success(self):
        with model_server() as (url, received), patch("wikiskill.model.time.sleep"):
            model = ChatCompletionsModel(ModelConfig(url, "retry", api_key_env="", retries=1))
            self.assertEqual(model.complete([], []).message["content"], "ok")
            self.assertEqual(len(received), 2)

    def test_invalid_response_and_missing_key_fail_clearly(self):
        with model_server() as (url, received):
            model = ChatCompletionsModel(ModelConfig(url, "malformed", api_key_env="", retries=0))
            with self.assertRaises(ModelError):
                model.complete([], [])
            model = ChatCompletionsModel(ModelConfig(url, "simple", api_key_env="WIKISKILL_ABSENT_TEST_KEY"))
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ModelError, "environment variable"):
                model.complete([], [])
            self.assertEqual(len(received), 1)


class CliTests(unittest.TestCase):
    def test_http_cli_init_run_evaluate_compare_and_independent_experiments(self):
        with tempfile.TemporaryDirectory() as temporary, model_server() as (url, received):
            root = Path(temporary)
            data, config_path = root / "data", root / "config.json"
            write_demo_dataset(data)
            config = {"models": {role: {"base_url": url, "model": role, "api_key_env": "", "retries": 0}
                                  for role in ("inference", "maintainer", "proposer")}}
            config_path.write_text(json.dumps(config))

            def run(*args):
                result = subprocess.run([sys.executable, "-m", "wikiskill", *map(str, args)],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            workspace = root / "experiment"
            self.assertEqual(run("init", workspace, "--data", data, "--config", config_path)["model_calls"], 0)
            self.assertFalse(received)
            self.assertEqual(run("run", workspace, "--quiet")["best_score"], 1)
            baseline, candidate = root / "baseline.json", root / "candidate.json"
            run("evaluate", workspace, "--no-skills", "--output", baseline, "--quiet")
            run("evaluate", workspace, "--output", candidate, "--quiet")
            self.assertEqual(run("compare", "--baseline", baseline, "--candidate", candidate)["mean_delta"], 0.75)
            report = run("experiment", root / "repeats", "--data", data, "--config", config_path,
                         "--seeds", 51, 52, "--quiet")
            self.assertEqual(len(report["runs"]), 2)
            self.assertEqual(report["mean_final_test"], 1)
            self.assertEqual(report["mean_baseline_test"], 0.25)

    def test_demo_status_export_and_nonempty_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            def run(*args):
                return subprocess.run([sys.executable, "-m", "wikiskill", *map(str, args)],
                                      capture_output=True, text=True, check=False)
            result = run("demo", root, "--quiet")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["research_result"])
            status = run("status", root)
            self.assertEqual(json.loads(status.stdout)["best_score"], 1)
            exported = run("export", root, Path(temporary) / "exported")
            self.assertEqual(exported.returncode, 0, exported.stderr)
            self.assertTrue((Path(temporary) / "exported/arithmetic_rules/SKILL.md").is_file())
            repeat = run("demo", root, "--quiet")
            self.assertEqual(repeat.returncode, 1)
            self.assertIn("new or empty", repeat.stderr)


if __name__ == "__main__":
    unittest.main()
