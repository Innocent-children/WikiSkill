from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque

from .config import Config
from .generation import INSTRUCTIONS, WIKI_SCHEMA, TOOLS, Proposer, maintain, maintainer_prompt, maintainer_schema, parse_json


class CodexSession:
    """One local app-server connection; every optimization starts a persistent fresh thread."""

    def __init__(self, config: Config):
        self.config = config
        self.process = None
        self.messages = queue.Queue()
        self.events = deque()
        self.sequence = 0
        self.thread_id = None
        self.stderr = deque(maxlen=20)
        self.on_activity = None
        self.metrics = {}
        self.on_response = None
        self.proposer = None

    def __enter__(self):
        self.process = subprocess.Popen(self.config.codex_command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", bufsize=1, cwd=self.config.root)

        def read_stdout():
            try:
                for line in self.process.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except ValueError:
                        self.messages.put(RuntimeError("Codex returned malformed JSONL"))
            finally:
                self.messages.put(EOFError("Codex app-server closed its output"))

        def read_stderr():
            for line in self.process.stderr:
                self.stderr.append(line.rstrip())

        self.readers = [threading.Thread(target=read_stdout, daemon=True),
                        threading.Thread(target=read_stderr, daemon=True)]
        for thread in self.readers:
            thread.start()
        try:
            self.request("initialize", {"clientInfo": {"name": "wikiskill", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for thread in self.readers:
            thread.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def send(self, message: dict):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def receive(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex response timed out")
        try:
            message = self.messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Codex response timed out") from exc
        if isinstance(message, BaseException):
            raise message
        if not isinstance(message, dict):
            raise RuntimeError("Codex message must be an object")
        if self.on_activity is not None:
            self.on_activity()
        if "method" in message and "id" in message:
            data = message.get('params', {})
            if message['method'] == 'item/tool/call' and self.proposer is not None and data.get('threadId') == self.thread_id:
                if self.on_response:
                    self.on_response({'stage': 'skill', 'provider': 'codex', 'call': self.metrics['calls'], 'response': data})
                try:
                    result = self.proposer.call(data.get('tool'), data.get('arguments'))
                except Exception as exc:
                    self.send({'id': message['id'], 'result': {'success': False, 'contentItems': [{'type': 'inputText', 'text': str(exc)}]}})
                    raise
                self.send({'id': message['id'], 'result': {'success': True, 'contentItems': [{'type': 'inputText', 'text': result}]}})
            else:
                self.send({"id": message["id"], "error": {"code": -32601,
                          "message": "WikiSkill only accepts its registered read_file and finish tools"}})
                raise RuntimeError("Background Codex session requested an interactive action")
        return message

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            message = self.receive(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Codex {method}: {message['error']}")
                if not isinstance(message.get("result"), dict):
                    raise RuntimeError(f"Codex {method} returned an invalid result")
                return message["result"]
            self.events.append(message)

    def start(self, title: str) -> str:
        # Disable configured MCP servers for this thread, including WikiSkill itself.
        configured = self.request("config/read", {"includeLayers": False}).get("config", {})
        overrides = {f"mcp_servers.{name}.enabled": False for name in configured.get("mcp_servers", {})}
        overrides.update({"features.apps": False, "features.shell_tool": False, "web_search": "disabled"})
        params = {"cwd": str(self.config.root), "ephemeral": False, "sandbox": "read-only",
                  "approvalPolicy": "never", "developerInstructions": INSTRUCTIONS,
                  "config": overrides}
        if title.startswith('WikiSkill skill '):
            params['dynamicTools'] = [{'type': 'function', 'name': t['function']['name'],
                'description': t['function']['description'], 'inputSchema': t['function']['parameters']} for t in TOOLS]
        if self.config.model:
            params["model"] = self.config.model
        thread = self.request("thread/start", params).get("thread", {})
        self.thread_id = thread.get("id")
        if not isinstance(self.thread_id, str) or not self.thread_id:
            raise RuntimeError("Codex did not return a persistent thread id")
        from .store import Store
        with Store(self.config).transaction() as db:
            db.execute("INSERT OR IGNORE INTO generated_sessions VALUES(?)", (self.thread_id,))
        return self.thread_id

    def resume(self, thread_id: str):
        self.request("thread/resume", {"threadId": thread_id})
        self.thread_id = thread_id

    def turn(self, prompt: str, schema: dict | None = None) -> str:
        self.events.clear()
        params = {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}]}
        if schema:
            params["outputSchema"] = schema
        result = self.request("turn/start", params)
        turn_id = result["turn"]["id"]
        deadline = time.monotonic() + self.config.timeout_seconds
        texts = {}
        while True:
            message = self.events.popleft() if self.events else self.receive(deadline)
            data = message.get("params", {})
            if data.get("threadId") != self.thread_id or data.get("turnId", turn_id) != turn_id:
                continue
            if message.get("method") == "item/completed":
                item = data.get("item", {})
                if item.get("type") == "agentMessage":
                    texts[item["id"]] = item.get("text", "")
            if message.get("method") == "turn/completed" and data.get("turn", {}).get("id") == turn_id:
                turn = data["turn"]
                if turn.get("status") != "completed":
                    raise RuntimeError(f"Codex turn {turn.get('status')}: {turn.get('error')}")
                if not texts and self.proposer is not None and self.proposer.result is not None:
                    return ""
                if not texts:
                    raise RuntimeError("Codex completed without an agent response")
                return list(texts.values())[-1]

    def generate(self, stage: str, context: dict) -> dict:
        self.metrics.update(calls=0, reads=[])
        if stage == 'raw':
            self.metrics['calls'] += 1
            schema = maintainer_schema(context)
            output = self.turn(maintainer_prompt(context), schema)
            if self.on_response:
                self.on_response({'stage': stage, 'provider': 'codex', 'call': self.metrics['calls'], 'response': {'content': output}})
            return maintain(context, parse_json(output, schema, 'Wiki Maintainer'))
        self.proposer = Proposer(context, self.metrics)
        try:
            self.metrics['calls'] += 1
            output = self.turn(self.proposer.prompt())
            if self.on_response:
                self.on_response({'stage': stage, 'provider': 'codex', 'call': self.metrics['calls'], 'response': {'content': output}})
            if self.proposer.result is None:
                raise ValueError('Codex 未调用 finish 工具；请查看模型响应原文')
            return self.proposer.result
        finally:
            self.proposer = None

    def report(self, report: dict) -> str:
        return report['summary']
