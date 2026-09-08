import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.cli import install_skill


class LiveMcpTests(unittest.TestCase):
    def test_real_stdio_discovery_collection_query_and_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Config(root / "home", auto_start=False).initialize()
            messages = [
                {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}}},
                {"method": "notifications/initialized"},
                {"id": 2, "method": "tools/list"},
                {"id": 3, "method": "tools/call", "params": {"name": "wikiskill_collect", "arguments": {"project": directory, "source_id": "test", "observations": [{"problem": "failure", "action": "fix", "outcome": "passed", "lesson": "lesson"}]}}},
                {"id": 4, "method": "tools/call", "params": {"name": "wikiskill_query", "arguments": {"project": directory, "layer": "raw"}}},
                {"id": 5, "method": "tools/call", "params": {"name": "wikiskill_collect", "arguments": {"project": directory, "source_id": "test2", "observations": [], "unknown": True}}},
            ]
            process = subprocess.run([sys.executable, "-m", "wikiskill.live.cli", "--root", str(root / "home"), "mcp"],
                input="\n".join(json.dumps({"jsonrpc": "2.0", **m}) for m in messages) + "\n",
                capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(responses), 5)
            self.assertTrue({"wikiskill_collect", "wikiskill_context", "wikiskill_rollback"} <=
                            {tool["name"] for tool in responses[1]["result"]["tools"]})
            self.assertFalse(responses[2]["result"]["isError"])
            body = json.loads(responses[3]["result"]["content"][0]["text"])
            self.assertEqual(body["items"][0]["payload"]["observations"][0]["outcome"], "passed")
            self.assertTrue(responses[4]["result"]["isError"])

    def test_skill_installer_preserves_unrelated_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(install_skill(Path(directory)))
            self.assertIn("name: wikiskill", path.read_text())
            self.assertEqual(install_skill(Path(directory)), str(path))
            path.write_text("Existing unrelated skill")
            with self.assertRaises(ValueError):
                install_skill(Path(directory))
            self.assertEqual(path.read_text(), "Existing unrelated skill")


if __name__ == "__main__":
    unittest.main()
