import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from wikiskill.live.codex import CodexSession, SKILL_SCHEMA
from wikiskill.live.config import Config


class LiveCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = CodexSession(Config(Path(self.tmp.name), timeout_seconds=1))
        self.session.send = Mock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_disables_inherited_mcp_and_uses_persistent_thread(self):
        self.session.request = Mock(side_effect=[{"config": {"mcp_servers": {"wikiskill": {}, "other": {}}}},
                                                {"thread": {"id": "thr-1"}}])
        self.assertEqual(self.session.start("title"), "thr-1")
        params = self.session.request.call_args.args[1]
        self.assertFalse(params["ephemeral"])
        self.assertEqual(params["sandbox"], "read-only")
        self.assertFalse(params["config"]["mcp_servers.wikiskill.enabled"])
        self.assertNotIn("model", params)

    def test_events_before_turn_start_response_and_other_threads(self):
        self.session.thread_id = "thr-1"
        for message in [
            {"method": "item/completed", "params": {"threadId": "unrelated", "turnId": "turn-1", "item": {"type": "agentMessage", "id": "x", "text": "wrong"}}},
            {"method": "item/completed", "params": {"threadId": "thr-1", "turnId": "turn-1", "item": {"type": "agentMessage", "id": "a", "text": '{"summary":"ok","skill_md":null}'}}},
            {"id": 1, "result": {"turn": {"id": "turn-1"}}},
            {"method": "turn/completed", "params": {"threadId": "thr-1", "turn": {"id": "turn-1", "status": "completed"}}}
        ]:
            self.session.messages.put(message)
        result = self.session.turn("test", SKILL_SCHEMA)
        self.assertEqual(json.loads(result)["summary"], "ok")
        self.assertEqual(self.session.send.call_args.args[0]["params"]["outputSchema"], SKILL_SCHEMA)

    def test_errors_and_interactive_requests_are_explicit(self):
        self.session.messages.put({"id": 1, "error": {"message": "unavailable"}})
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.session.request("thread/start", {})
        self.session.messages.put({"id": 17, "method": "item/commandExecution/requestApproval", "params": {}})
        with self.assertRaisesRegex(RuntimeError, "interactive"):
            self.session.request("turn/start", {})
        self.assertEqual(self.session.send.call_args.args[0]["error"]["code"], -32601)

    def test_stdio_subprocess_handshake_and_exit(self):
        import sys
        program = self.tmp.name + "/fake.py"
        Path(program).write_text('''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if "id" in m:
  print(json.dumps({"id":m["id"],"result":{}}),flush=True)
''')
        config = Config(Path(self.tmp.name), codex_command=[sys.executable, program], timeout_seconds=2)
        with CodexSession(config) as session:
            self.assertEqual(session.request("ping", {}), {})
        self.assertIsNotNone(session.process.poll())


if __name__ == "__main__":
    unittest.main()
