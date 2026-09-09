import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from test_live_runtime import seed_record


class ApiModelTests(unittest.TestCase):
    def test_chat_api_two_stages_keep_key_out_of_job_and_report(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, self.headers.get("Authorization"), body))
                prompt = body["messages"][-1]["content"]
                result = ({"summary": "Wiki created", "pages": [{"name": "build", "body": "Use the configured JDK"}]}
                          if "original Codex transcript" in prompt else
                          {"summary": "Skill created", "skill_md": "---\nname: test\ndescription: Build project\n---\nUse the configured JDK"})
                encoded = json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(result)}, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runtime = Runtime(Config(root / "data", executor="api", api_url=f"http://127.0.0.1:{server.server_port}/v1",
                    api_model="fixture", api_key="test-secret", max_tokens=500000, context_window=1, raw_auto=True, wiki_auto=True, raw_threshold=1, wiki_threshold=1, auto_start=False))
                key = runtime.store.project(str(root))
                seed_record(runtime, key)
                self.assertEqual(runtime.drain(), 2)
                jobs = runtime.store.rows("SELECT * FROM jobs")
                self.assertTrue(all(j["state"] == "done" for j in jobs), jobs)
                self.assertTrue(all(j["thread_id"] is None for j in jobs))
                self.assertNotIn("test-secret", json.dumps(jobs))
                self.assertNotIn("test-secret", json.dumps(runtime.store.rows("SELECT * FROM job_execution")))
                self.assertTrue(all(x[2]["max_tokens"] == 500000 for x in captured))
                self.assertTrue(all("context_window" not in x[2] for x in captured))
                self.assertEqual([x[1] for x in captured], ["Bearer test-secret"] * 2)
        finally:
            server.shutdown(); server.server_close(); thread.join()
