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
                prompt = body['messages'][1]['content']
                if 'response_format' in body:
                    result = {'create_patterns':[{'name':'build.md','content':'Use configured JDK'}], 'update_patterns':[],
                              'update_index':'- [build](wiki/patterns/build.md): Use configured JDK', 'append_log':'Recorded build lesson'}
                    message = {'role':'assistant','content':json.dumps(result)}
                elif not any(m['role']=='tool' for m in body['messages']):
                    catalog = json.loads(prompt.split('\nCatalog:\n')[1].split('\nResources:\n')[0])
                    message = {'role':'assistant','content':None,'tool_calls':[{'id':f'read-{i}','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':entry['path']})}} for i,entry in enumerate(catalog)]}
                else:
                    import re
                    name = re.search(r'only authorized Skill name is ([a-z0-9-]+)',prompt).group(1)
                    proposal = {'action':'create','name':name,'skill_md':'---\nname: '+name+'\ndescription: Build project\n---\nUse configured JDK','purpose_md':'# Origin\nBuild pattern'}
                    message = {'role':'assistant','content':None,'tool_calls':[{'id':'finish','type':'function','function':{'name':'finish','arguments':json.dumps(proposal)}}]}
                encoded = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
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
                    api_model="fixture", api_key="test-secret", capture_mode="automatic", auto_start=False))
                key = runtime.store.project(str(root))
                seed_record(runtime, key)
                self.assertEqual(runtime.drain(), 2)
                jobs = runtime.store.rows("SELECT * FROM jobs")
                self.assertTrue(all(j["state"] == "done" for j in jobs), jobs)
                self.assertTrue(all(j["thread_id"] is None for j in jobs))
                self.assertNotIn("test-secret", json.dumps(jobs))
                self.assertNotIn("test-secret", json.dumps(runtime.store.rows("SELECT * FROM job_execution")))
                self.assertTrue(all("max_tokens" not in x[2] for x in captured))
                self.assertTrue(all("context_window" not in x[2] for x in captured))
                self.assertEqual([x[1] for x in captured], ["Bearer test-secret"] * 3)
        finally:
            server.shutdown(); server.server_close(); thread.join()
