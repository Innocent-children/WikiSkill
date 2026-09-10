import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from wikiskill.model import GeminiModel, ModelConfig, ModelError, create_model


class GeminiTests(unittest.TestCase):
    def test_native_http_tool_response_preserves_signature_call_id_and_usage(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, body, self.headers.get("x-goog-api-key")))
                if len(captured) == 1:
                    parts = [{"text": "Read the task file.", "thought": True},
                             {"functionCall": {"name": "read_file", "args": {"path": "fact.txt"}, "id": "provider-call"},
                              "thoughtSignature": "opaque-signature"}]
                else:
                    parts = [{"text": "<answer>42</answer>"}]
                response = {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                            "usageMetadata": {"totalTokenCount": 17}}
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = ModelConfig(f"http://127.0.0.1:{server.server_port}/v1beta", "gemini-fixture",
                                 api_key_env="GEMINI_FIXTURE_KEY", provider="gemini", thinking_budget=256, seed=7)
            with patch.dict(os.environ, {"GEMINI_FIXTURE_KEY": "test-only"}):
                model = create_model(config)
                tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a fixture", "parameters": {"type": "object"}}}]
                messages = [{"role": "user", "content": "Read the fact"}]
                first = model.complete(messages, tools)
                result = model.complete([*messages, first.message, {"role": "tool", "tool_call_id": first.message["tool_calls"][0]["id"], "content": '{"content":"42"}'}], tools)
            self.assertIsInstance(model, GeminiModel)
            self.assertEqual(result.message["content"], "<answer>42</answer>")
            self.assertEqual(len(captured), 2)
            self.assertEqual(captured[0][0], "/v1beta/models/gemini-fixture:generateContent")
            self.assertEqual(captured[0][2], "test-only")
            self.assertIn("functionDeclarations", captured[0][1]["tools"][0])
            self.assertEqual(captured[0][1]["generationConfig"]["seed"], 7)
            self.assertNotIn("maxOutputTokens", captured[0][1]["generationConfig"])
            contents = captured[1][1]["contents"]
            self.assertEqual(contents[1]["parts"][1]["thoughtSignature"], "opaque-signature")
            self.assertEqual(contents[2]["parts"][0]["functionResponse"]["id"], "provider-call")
            self.assertEqual(contents[2]["parts"][0]["functionResponse"]["response"]["result"]["content"], "42")
            self.assertEqual(result.usage, {"totalTokenCount": 17})
            self.assertNotIn("test-only", json.dumps(model.identity))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_gemini_refuses_missing_tool_history_and_malformed_response(self):
        with self.assertRaises(ModelError):
            GeminiModel._contents([{"role": "tool", "tool_call_id": "unknown", "content": "{}"}])
        config = ModelConfig("http://localhost/v1beta", "fixture", provider="gemini", api_key_env="")
        with patch("wikiskill.model.request_json", return_value={"candidates": []}):
            with self.assertRaisesRegex(ModelError, "invalid generateContent"):
                GeminiModel(config).complete([{"role": "user", "content": "hello"}], [])

    def test_token_limit_is_preserved(self):
        config = ModelConfig("http://localhost/v1beta", "fixture", provider="gemini", api_key_env="")
        response = {"candidates": [{"content": {"parts": [{"text": "partial", "thought": True}]}, "finishReason": "MAX_TOKENS"}]}
        with patch("wikiskill.model.request_json", return_value=response):
            self.assertEqual(GeminiModel(config).complete([], []).finish_reason, "length")


if __name__ == "__main__":
    unittest.main()
