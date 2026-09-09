import json
import tempfile
import unittest
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.skills import skill_text, snapshot


OBSERVATION = {"problem": "Build failed", "action": "Set project JDK", "outcome": "Build passed", "lesson": "Read project JDK config"}


class FakeSession:
    starts = []
    generations = []
    reports = []
    fail_generate = False
    fail_report = False
    no_change = False
    during_generate = None

    def __init__(self, config):
        self.thread_id = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, title):
        self.thread_id = f"thread-{len(self.starts)}"
        self.starts.append(self.thread_id)
        return self.thread_id

    def resume(self, thread_id):
        self.thread_id = thread_id

    def generate(self, stage, context):
        self.generations.append((self.thread_id, stage))
        if self.fail_generate:
            raise RuntimeError("Codex offline")
        if type(self).during_generate:
            type(self).during_generate()
        if stage == "raw":
            return {"summary": "Recorded build lesson", "pages": [] if self.no_change else [{"name": "build", "body": "Read the configured JDK before building."}]}
        return {"summary": "Added the build procedure", "skill_md": None if self.no_change else
                f"---\nname: {context['suggested_name']}\ndescription: Build this project.\n---\n\nRead the project JDK configuration.\n"}

    def report(self, report):
        if self.fail_report:
            raise RuntimeError("report unavailable")
        self.reports.append((self.thread_id, report))
        return report["summary"]


def seed_record(runtime, project, event="one"):
    import time
    with runtime.store.transaction() as db:
        db.execute("INSERT OR IGNORE INTO trace_sources(id,identity,path,fingerprint,prefix_length) VALUES(1,'fixture','fixture','',0)")
        db.execute("INSERT OR IGNORE INTO trace_turns(source,turn_key,project,ended) VALUES(1,?,?,1)", (event,project))
        turn = db.execute("SELECT id FROM trace_turns WHERE source=1 AND turn_key=?", (event,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO trace_records(source,offset,original,project,turn_id,event_type,created) VALUES(1,?,?,?,?,?,?)",
                   (turn, json.dumps({"type":"response_item","payload":{"event":event,**OBSERVATION}}).encode()+b"\n", project,turn,"response_item",time.time()))
        runtime.store.event(db, "capture.updated", project=project)
    return {"raw_id": str(turn)}


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        for name in ("starts", "generations", "reports"):
            setattr(FakeSession, name, [])
        FakeSession.fail_generate = FakeSession.fail_report = FakeSession.no_change = False
        FakeSession.during_generate = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / "home", raw_threshold=1, wiki_threshold=1, auto_start=False, raw_auto=True, wiki_auto=True), FakeSession)
        self.key = self.runtime.store.project(str(self.project))

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, event="one"):
        return seed_record(self.runtime, self.key, event)

    def test_two_stages_create_separate_threads_report_and_consume_once(self):
        self.collect()
        self.assertEqual(self.runtime.drain(), 2)
        self.assertEqual(len(FakeSession.starts), 2)
        self.assertEqual(len(FakeSession.reports), 2)
        self.assertEqual([x[1] for x in FakeSession.generations], ["raw", "skill"])
        status = self.runtime.store.status(self.key)["projects"][0]
        self.assertEqual(status["raw_pending"], 0)
        self.assertEqual(status["skills"][0]["wiki_pending"], 0)
        self.assertIn("Read the project", self.runtime.context(str(self.project))["skills"][0]["skill_md"])
        self.collect("one")
        self.assertEqual(self.runtime.drain(), 0)
        for report_file in (self.runtime.config.root / "reports").glob("*.json"):
            self.assertIn("thread_id", json.loads(report_file.read_text()))

    def test_model_failure_preserves_inputs_and_retry_uses_same_batch(self):
        self.collect()
        FakeSession.fail_generate = True
        self.assertEqual(self.runtime.drain(), 1)
        job = self.runtime.store.rows("SELECT * FROM jobs")[0]
        self.assertEqual(job["state"], "failed")
        self.assertEqual(self.runtime.store.status(self.key)["projects"][0]["raw_pending"], 1)
        self.assertEqual(self.runtime.drain(), 0)
        FakeSession.fail_generate = False
        self.runtime.retry(job["id"])
        self.assertEqual(self.runtime.drain(), 2)
        self.assertEqual(len(FakeSession.starts), 2)

    def test_report_failure_retry_never_republishes(self):
        self.collect()
        FakeSession.fail_report = True
        self.runtime.drain()
        job = self.runtime.store.rows("SELECT * FROM jobs WHERE stage='skill'")[0]
        self.assertEqual(job["state"], "done")
        before_generations = len(FakeSession.generations)
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM versions")), 1)
        FakeSession.fail_report = False
        self.runtime.retry(job["id"])
        self.assertEqual(len(FakeSession.generations), before_generations)
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM versions")), 1)
        self.assertEqual(self.runtime.store.job(job["id"])["report_sent"], 1)

    def test_retry_replaces_failed_attempt_report_without_reusing_sent_flag(self):
        self.collect()
        FakeSession.fail_generate = True
        self.runtime.drain()
        job_id = self.runtime.store.rows("SELECT id FROM jobs")[0]["id"]
        self.assertEqual(self.runtime.store.job(job_id)["report_sent"], 1)
        self.runtime.retry(job_id)
        FakeSession.fail_generate = False
        FakeSession.fail_report = True
        self.runtime.run_job(job_id)
        job = self.runtime.store.job(job_id)
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["report"]["outcome"], "changed")
        self.assertEqual(job["report_sent"], 0)
        FakeSession.fail_report = False
        self.runtime.retry(job_id)
        self.assertEqual(self.runtime.store.job(job_id)["report_sent"], 1)

    def test_detached_worker_process_drains_both_stages(self):
        import os
        import signal
        import sys
        import time
        program = self.root / "fake_codex.py"
        program.write_text('''import json,sys,uuid
thread_id=uuid.uuid4().hex
for line in sys.stdin:
 m=json.loads(line)
 if "id" not in m: continue
 method=m["method"]; params=m.get("params",{})
 result={}
 if method=="config/read": result={"config":{}}
 elif method=="thread/start": result={"thread":{"id":thread_id}}
 elif method=="turn/start": result={"turn":{"id":str(m["id"])}}
 print(json.dumps({"id":m["id"],"result":result}),flush=True)
 if method=="turn/start":
  schema=params.get("outputSchema")
  if schema and "pages" in schema["properties"]:
   text=json.dumps({"summary":"Build lesson","pages":[{"name":"build","body":"Use the configured JDK."}]})
  elif schema:
   text=json.dumps({"summary":"Created build skill","skill_md":"---\\nname: build\\ndescription: Build the project.\\n---\\nUse the configured JDK.\\n"})
  else: text="Final report recorded."
  turn_id=str(m["id"])
  print(json.dumps({"method":"item/completed","params":{"threadId":thread_id,"turnId":turn_id,"item":{"type":"agentMessage","id":"answer","text":text}}}),flush=True)
  print(json.dumps({"method":"turn/completed","params":{"threadId":thread_id,"turn":{"id":turn_id,"status":"completed"}}}),flush=True)
''')
        runtime = Runtime(Config(self.root / "worker-home", raw_threshold=1, wiki_threshold=1,
                                 poll_seconds=1, timeout_seconds=5, raw_auto=True, wiki_auto=True, codex_home=str(self.root / "empty-codex"), codex_command=[sys.executable, str(program)]))
        seed_record(runtime, runtime.store.project(str(self.project)), "worker")
        started = runtime.wake()
        process_id = started["pid"]
        try:
            deadline = time.monotonic() + 15
            jobs = []
            while time.monotonic() < deadline:
                jobs = runtime.store.rows("SELECT state,report_sent,error FROM jobs")
                if len(jobs) == 2 and all(j["state"] == "done" for j in jobs):
                    break
                time.sleep(0.05)
            self.assertEqual(len(jobs), 2, jobs)
            self.assertTrue(all(j["state"] == "done" and j["report_sent"] for j in jobs), jobs)
        finally:
            try:
                os.killpg(process_id, signal.SIGTERM)
                os.killpg(started["collector_pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(process_id, 0)
            except ChildProcessError:
                pass

    def test_no_change_consumes_batch_without_skill_publication(self):
        self.collect()
        FakeSession.no_change = True
        self.assertEqual(self.runtime.drain(), 1)
        self.assertEqual(self.runtime.store.rows("SELECT * FROM versions"), [])
        job = self.runtime.store.rows("SELECT id FROM jobs")[0]
        self.assertEqual(self.runtime.store.job(job["id"])["report"]["outcome"], "no_change")

    def test_new_input_during_generation_stays_pending_for_next_batch(self):
        self.collect()
        def collect_extra():
            FakeSession.during_generate = None
            self.collect("new")
        FakeSession.during_generate = collect_extra
        self.runtime.schedule()
        job = self.runtime.store.rows("SELECT id FROM jobs")[0]
        self.runtime.run_job(job["id"])
        self.assertEqual(self.runtime.store.status(self.key)["projects"][0]["raw_pending"], 1)

    def test_concurrent_wiki_edit_is_not_overwritten(self):
        self.collect()
        def edit_wiki():
            FakeSession.during_generate = None
            self.runtime.put_wiki(str(self.project), [{"name": "build", "body": "User's newer knowledge"}])
        FakeSession.during_generate = edit_wiki
        self.runtime.schedule()
        job = self.runtime.store.rows("SELECT id FROM jobs")[0]
        self.runtime.run_job(job["id"])
        self.assertEqual(self.runtime.store.job(job["id"])["state"], "failed")
        self.assertEqual(self.runtime.store.query(self.key, "wiki")["items"][0]["body"], "User's newer knowledge")
        self.runtime.retry(job["id"], regenerate=True)
        self.runtime.run_job(job["id"])
        self.assertEqual(self.runtime.store.job(job["id"])["state"], "done")

    def test_shared_skill_only_has_one_outstanding_batch(self):
        project2 = self.root / "project2"
        project2.mkdir()
        key2 = self.runtime.store.project(str(project2))
        shared = self.runtime.store.skills(self.key)[0]["id"]
        with self.runtime.store.transaction() as db:
            db.execute("INSERT INTO project_skills VALUES(?,?)", (key2, shared))
        for project in (self.project, project2):
            self.runtime.put_wiki(str(project), [{"name": "build", "body": "Check the build"}])
        self.runtime.schedule()
        jobs = self.runtime.store.rows("SELECT * FROM jobs WHERE skill=?", (shared,))
        self.assertEqual(len(jobs), 1)
        self.runtime.drain()
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM jobs WHERE skill=?", (shared,))), 1)
        # Other projects explicitly select Wiki when updating this summary Skill.
        self.runtime.enqueue(key2, "skill", skill=shared)
        with self.assertRaises(ValueError):
            self.runtime.enqueue(self.key, "skill", skill=shared)
        self.runtime.drain()
        self.assertEqual(len(self.runtime.store.rows("SELECT * FROM jobs WHERE skill=?", (shared,))), 2)


if __name__ == "__main__":
    unittest.main()
