from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from wikiskill.storage import atomic_text

from .codex import CodexSession
from .config import Config, digest, normalized
from .skills import SkillManager, file_lock, skill_text, snapshot, with_skill
from .store import Store, dumps
from .telemetry import WorkerHeartbeat


def validate_pages(pages: list[dict]) -> None:
    if not isinstance(pages, list):
        raise ValueError("pages must be an array")
    names = set()
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"name", "body"}:
            raise ValueError("Wiki pages require name and body")
        if not isinstance(page["name"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", page["name"]):
            raise ValueError("Wiki names must be lowercase letters, digits and hyphens")
        if page["name"] in names or not isinstance(page["body"], str) or not normalized(page["body"]):
            raise ValueError("Wiki names must be unique and bodies nonempty")
        names.add(page["name"])


def write_pages(db, project: str, pages: list[dict], metadata: dict, job_id: str) -> int:
    count = 0
    for page in pages:
        content_digest = digest(normalized(page["body"]))
        current = db.execute("SELECT digest FROM wiki WHERE project=? AND name=?", (project, page["name"])).fetchone()
        db.execute("INSERT INTO wiki VALUES(?,?,?,?,?) ON CONFLICT(project,name) DO UPDATE SET "
                   "body=excluded.body,digest=excluded.digest,metadata=excluded.metadata",
                   (project, page["name"], page["body"], content_digest, dumps(metadata)))
        if current is None or current["digest"] != content_digest:
            count += db.execute("INSERT OR IGNORE INTO wiki_changes(project,name,digest,body,job_id) VALUES(?,?,?,?,?)",
                                (project, page["name"], content_digest, page["body"], job_id)).rowcount
    if pages:
        Store.event(db, "wiki.updated", project=project,
                    job_id=None if job_id.startswith("manual-") else job_id,
                    names=[page["name"] for page in pages], new_changes=count)
    return count


class Runtime:
    def __init__(self, config: Config, session_factory=CodexSession, heartbeat: WorkerHeartbeat | None = None):
        self.config = config
        self.store = Store(config)
        self.skills = SkillManager(self.store)
        self.session_factory = session_factory
        self.heartbeat = heartbeat

    def put_wiki(self, project: str, pages: list, metadata: dict | None = None, expected: dict | None = None) -> dict:
        validate_pages(pages)
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        key = self.store.project(project)
        with self.store.transaction() as db:
            if expected is not None:
                if not isinstance(expected, dict) or set(expected) != {p["name"] for p in pages}:
                    raise ValueError("Expected digests must cover every page")
                for page in pages:
                    row = db.execute("SELECT digest FROM wiki WHERE project=? AND name=?", (key, page["name"])).fetchone()
                    if (row[0] if row else None) != expected[page["name"]]:
                        raise ValueError("Wiki page changed; refresh before saving")
            count = write_pages(db, key, pages, metadata or {}, "manual-" + uuid.uuid4().hex)
        return {"project_id": key, "new_changes": count}

    def rollback_wiki(self, project: str, change_id: int, expected: str):
        rows = self.store.rows("SELECT * FROM wiki_changes WHERE project=? AND id=?", (project, change_id))
        paths = self.store.rows("SELECT path FROM projects WHERE id=?", (project,))
        if not rows or not paths:
            raise ValueError("Unknown Wiki version")
        page = rows[0]
        return self.put_wiki(paths[0]["path"], [{"name": page["name"], "body": page["body"]}],
                             {"rollback_of": change_id}, {page["name"]: expected})

    def context(self, project: str) -> dict:
        key = self.store.project(project)
        skills = []
        for item in self.store.skills(key):
            if self.config.permits(Path(item["path"]), bool(item["owned"])):
                with self.skills.lock(item["id"]):
                    bundle = snapshot(Path(item["path"]))
                skills.append({**item, "skill_md": skill_text(bundle), "digest": digest(bundle),
                               "files": list(bundle)})
        return {"project_id": key, "skills": skills,
                "loading": "Use these contents for this business task; published updates apply to subsequent tasks."}

    def schedule(self) -> list[str]:
        created = []
        with self.store.transaction() as db:
            for project in db.execute("SELECT id FROM projects").fetchall():
                key = project["id"]
                raw = self._raw_inputs(db, key, automatic=True)
                active = db.execute("SELECT 1 FROM jobs WHERE project=? AND stage='raw' AND state!='done' AND id IN (SELECT job FROM job_execution)", (key,)).fetchone()
                turns = db.execute("SELECT count(DISTINCT r.turn_id) FROM trace_records r JOIN trace_turns t ON t.id=r.turn_id "
                                   "WHERE r.project=? AND r.consumed_by IS NULL AND t.ended=1", (key,)).fetchone()[0]
                if self.config.raw_auto and turns >= self.config.raw_threshold and raw and not active:
                    created.append(self._enqueue(db, key, "raw", None, raw))
                targets = db.execute("SELECT s.* FROM skills s JOIN project_skills p ON s.id=p.skill WHERE p.project=?", (key,)).fetchall()
                for skill in targets:
                    if not self.config.permits(Path(skill["path"]), bool(skill["owned"])):
                        continue
                    pending = [r["id"] for r in db.execute("SELECT id FROM wiki_changes w WHERE project=? AND NOT EXISTS "
                        "(SELECT 1 FROM consumed_wiki c WHERE c.change_id=w.id AND c.skill=?) ORDER BY id", (key, skill["id"]))]
                    active = db.execute("SELECT 1 FROM jobs WHERE skill=? AND state!='done' AND id IN (SELECT job FROM job_execution)", (skill["id"],)).fetchone()
                    if self.config.wiki_auto and len(pending) >= self.config.wiki_threshold and not active:
                        created.append(self._enqueue(db, key, "skill", skill["id"], pending))
        return created

    def _raw_inputs(self, db, project, automatic=False):
        return [r[0] for r in db.execute("SELECT r.id FROM trace_records r JOIN trace_turns t ON t.id=r.turn_id "
            "WHERE r.project=? AND r.consumed_by IS NULL" + (" AND t.ended=1" if automatic else "") + " ORDER BY r.id", (project,))]

    def enqueue(self, project: str, stage: str, inputs: list[int] | None = None, skill: str | None = None) -> dict:
        if stage not in {"raw", "skill"}:
            raise ValueError("stage must be raw or skill")
        with self.store.transaction() as db:
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
                raise ValueError("Unknown project")
            if stage == "skill":
                target = db.execute("SELECT s.* FROM skills s JOIN project_skills p ON p.skill=s.id "
                                    "WHERE p.project=? AND s.id=?", (project, skill)).fetchone()
                if not target or not self.config.permits(Path(target["path"]), bool(target["owned"])):
                    raise ValueError("Choose an owned Skill for this project")
                pending = [r[0] for r in db.execute("SELECT id FROM wiki_changes w WHERE project=? AND NOT EXISTS "
                    "(SELECT 1 FROM consumed_wiki c WHERE c.change_id=w.id AND c.skill=?) ORDER BY id", (project, skill))]
            else:
                skill = None
                pending = self._raw_inputs(db, project)
            active = db.execute("SELECT id FROM jobs WHERE stage=? AND (project=? OR (skill IS NOT NULL AND skill=?)) AND state!='done' AND id IN (SELECT job FROM job_execution)", (stage, project, skill)).fetchone()
            if active:
                raise ValueError("A batch is already pending or failed; open execution history to retry it")
            if inputs is not None and (not isinstance(inputs, list) or not inputs or any(type(x) is not int for x in inputs)
                                       or len(inputs) != len(set(inputs)) or not set(inputs) <= set(pending)):
                raise ValueError("Select distinct, pending input IDs from this project")
            chosen = inputs if inputs is not None else pending
            if not chosen:
                raise ValueError("No pending inputs")
            key = self._enqueue(db, project, stage, skill, chosen)
        return {"job_id": key, "state": "queued"}

    def _enqueue(self, db, project: str, stage: str, skill: str | None, inputs: list[int]) -> str:
        # Bound each batch without truncating or consuming records left for the next batch.
        table, field = ("trace_records", "original") if stage == "raw" else ("wiki_changes", "body")
        selected, size = [], 0
        for item in inputs:
            length = db.execute(f"SELECT length({field}) FROM {table} WHERE id=?", (item,)).fetchone()[0]
            if selected and size + length > self.config.input_budget // 2:
                break
            selected.append(item)
            size += length
        inputs = selected
        key = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,project,stage,skill,state,inputs,created) VALUES(?,?,?,?,'queued',?,?)",
                   (key, project, stage, skill, dumps(inputs), time.time()))
        settings = {k: getattr(self.config, k) for k in ("executor", "api_provider", "api_url", "api_model", "model", "input_budget")}
        db.execute("INSERT INTO job_execution VALUES(?,?)", (key, dumps(settings)))
        Store.event(db, "job.queued", project=project, job_id=key, skill=skill, input_count=len(inputs))
        return key

    def _context(self, job: dict) -> dict:
        placeholders = ",".join("?" for _ in job["inputs"])
        wiki = self.store.rows("SELECT name,body FROM wiki WHERE project=? ORDER BY name", (job["project"],))
        context = {"project": job["project"], "wiki": wiki}
        if job["stage"] == "raw":
            rows = self.store.rows(f"SELECT id,original FROM trace_records WHERE id IN ({placeholders}) ORDER BY source,offset", job["inputs"])
            context["records"] = [{"id": r["id"], "original": r["original"].decode("utf-8", errors="replace")} for r in rows]
        else:
            skill = self.skills.get(job["skill"])
            bundle = snapshot(Path(skill["path"]))
            context.update(before_bundle=bundle, skill_md=skill_text(bundle),
                           suggested_name=Path(skill["path"]).name,
                           changes=self.store.rows(f"SELECT id,name,body FROM wiki_changes WHERE id IN ({placeholders}) ORDER BY id", job["inputs"]))
        return context

    def _apply(self, job: dict) -> dict:
        output = job["result"]
        expected = {"summary", "pages" if job["stage"] == "raw" else "skill_md"}
        if not isinstance(output, dict) or set(output) != expected or not isinstance(output["summary"], str):
            raise ValueError("Codex result has invalid fields")
        details = {"changed": False}
        if job["stage"] == "raw":
            validate_pages(output["pages"])
        else:
            before = job["context"]["before_bundle"]
            text = output["skill_md"]
            if text is not None and not isinstance(text, str):
                raise ValueError("skill_md must be text or null")
            after = before if text is None or normalized(text) == normalized(skill_text(before)) else with_skill(before, text)
            details = self.skills.publish(job["skill"], job["id"], before, after)
        with self.store.transaction() as db:
            if job["stage"] == "raw":
                original = {page["name"]: normalized(page["body"]) for page in job["context"]["wiki"]}
                for page in output["pages"]:
                    current = db.execute("SELECT body FROM wiki WHERE project=? AND name=?",
                                         (job["project"], page["name"])).fetchone()
                    if (normalized(current["body"]) if current else None) != original.get(page["name"]):
                        raise ValueError("Wiki page changed during generation; retry with regenerate=true")
                count = write_pages(db, job["project"], output["pages"], {"job_id": job["id"]}, job["id"])
                details = {"changed": count > 0, "wiki_changes": count, "pages": output["pages"]}
                db.executemany("UPDATE trace_records SET consumed_by=? WHERE id=? AND consumed_by IS NULL",
                               [(job["id"], key) for key in job["inputs"]])
            else:
                db.executemany("INSERT OR IGNORE INTO consumed_wiki VALUES(?,?,?)",
                               [(job["skill"], key, job["id"]) for key in job["inputs"]])
            report = {"job_id": job["id"], "project": job["project"], "stage": job["stage"], "skill": job["skill"],
                      "thread_id": job["thread_id"], "outcome": "changed" if details["changed"] else "no_change",
                      "summary": output["summary"], "inputs": job["inputs"], **details}
            db.execute("UPDATE jobs SET state='applied',report=?,report_sent=0,error=NULL WHERE id=?", (dumps(report), job["id"]))
            self.store.event(db, "job.applied", project=job["project"], job_id=job["id"],
                             skill=job["skill"], outcome=report["outcome"])
        return report

    def save_report(self, job_id: str) -> None:
        job = self.store.job(job_id)
        directory = self.config.root / "reports"
        atomic_text(directory / f"{job_id}.json", dumps(job["report"]) + "\n")
        report = job["report"] or {}
        text = (f"# WikiSkill {job_id}\n\nOutcome: {report.get('outcome')}\n\n"
                f"Codex session: {job['thread_id']}\n\n{report.get('summary', '')}\n\n")
        if report.get("diff"):
            text += "```diff\n" + report["diff"] + "\n```\n"
        text += "\n```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n"
        atomic_text(directory / f"{job_id}.md", text)

    def _report(self, session, job_id: str):
        job = self.store.job(job_id)
        self.store.job_event(job_id, "report.sending")
        self.save_report(job_id)
        try:
            text = session.report(job["report"])
            report = {**job["report"], "conversation_report": text}
            report.pop("report_error", None)
            self.store.update_job(job_id, report=report, report_sent=1, event="report.sent")
        except Exception as exc:
            self.store.update_job(job_id, report={**job["report"], "report_error": str(exc)},
                                  event="report.failed", event_payload={"error": str(exc)})
        self.save_report(job_id)

    def run_job(self, job_id: str):
        if self.heartbeat:
            self.heartbeat.set_job(job_id)
        try:
            self._run_job(job_id)
        finally:
            if self.heartbeat:
                self.heartbeat.set_job(None)

    def _run_job(self, job_id: str):
        job = self.store.job(job_id)
        lock = self.skills.lock(job["skill"]) if job["skill"] else nullcontext()
        with lock:
            job = self.store.job(job_id)
            if job["state"] in {"done", "failed"}:
                return
            self.store.job_event(job_id, "job.started")
            session = None
            try:
                if job["context"] is None:
                    self.store.update_job(job_id, context=self._context(job))
                saved = self.store.rows("SELECT settings FROM job_execution WHERE job=?", (job_id,))
                if not saved:
                    raise ValueError("This retained batch uses old summary inputs; create a new transcript batch")
                config = replace(self.config, **json.loads(saved[0]["settings"]))
                from .models import ApiSession
                factory = ApiSession if config.executor == "api" else self.session_factory
                self.store.job_event(job_id, "api.connecting" if config.executor == "api" else "codex.connecting")
                with factory(config) as session:
                    if self.heartbeat:
                        session.on_activity = self.heartbeat.touch
                    if job["thread_id"]:
                        session.resume(job["thread_id"])
                    else:
                        thread_id = session.start(f"WikiSkill {job['stage']} {job_id}")
                        self.store.update_job(job_id, thread_id=thread_id, event="codex.session")
                    job = self.store.job(job_id)
                    try:
                        if job["state"] != "applied":
                            if job["result"] is None:
                                self.store.update_job(job_id, state="generating")
                                context = {k: v for k, v in job["context"].items() if k != "before_bundle"}
                                if "before_bundle" in job["context"]:
                                    context["resource_inventory"] = list(job["context"]["before_bundle"])
                                from .generation import generation_prompt
                                if len(generation_prompt(job["stage"], context)) > config.input_budget:
                                    raise ValueError("Input exceeds configured model budget; increase input_budget and regenerate. Raw remains intact.")
                                result = session.generate(job["stage"], context)
                                self.store.update_job(job_id, result=result, state="prepared")
                            self._apply(self.store.job(job_id))
                    except Exception as exc:
                        self._failure(job_id, exc)
                        self._report(session, job_id)
                        return
                    self._report(session, job_id)
                    self.store.update_job(job_id, state="done")
            except Exception as exc:
                job = self.store.job(job_id)
                if job["state"] == "applied":
                    self.store.update_job(job_id, state="done", report={**job["report"], "report_error": str(exc)})
                else:
                    self._failure(job_id, exc)
                self.save_report(job_id)
                # The local report is authoritative even if Codex is unavailable.

    def _failure(self, job_id: str, exc: Exception):
        job = self.store.job(job_id)
        report = {"job_id": job_id, "project": job["project"], "stage": job["stage"],
                  "thread_id": job["thread_id"], "outcome": "failed", "summary": str(exc),
                  "inputs": job["inputs"], "publication": self.store.rows(
                      "SELECT id,state,diff FROM versions WHERE job_id=?", (job_id,))}
        self.store.update_job(job_id, state="failed", error=str(exc), report=report, report_sent=0)

    def drain(self) -> int:
        count = 0
        while True:
            self.schedule()
            jobs = self.store.rows("SELECT id FROM jobs WHERE state NOT IN ('done','failed') AND id IN (SELECT job FROM job_execution) ORDER BY created LIMIT 1")
            if not jobs:
                return count
            self.run_job(jobs[0]["id"])
            count += 1

    def retry(self, job_id: str, regenerate: bool = False) -> dict:
        with file_lock(self.config.root / "locks" / "worker.lock", blocking=False):
            job = self.store.job(job_id)
            saved = self.store.rows("SELECT settings FROM job_execution WHERE job=?", (job_id,))
            if not saved:
                raise ValueError("Old summary batches are retained for viewing; create a new transcript batch")
            if job["state"] == "done":
                if job["report_sent"]:
                    return {"job_id": job_id, "state": "done", "report_sent": True}
                from .models import ApiSession
                config = replace(self.config, **json.loads(saved[0]["settings"]))
                factory = ApiSession if config.executor == "api" else self.session_factory
                with factory(config) as session:
                    if job["thread_id"]:
                        session.resume(job["thread_id"])
                    self._report(session, job_id)
            elif job["state"] == "failed":
                fields = {"state": "queued", "error": None}
                if regenerate:
                    if self.store.rows("SELECT id FROM versions WHERE job_id=?", (job_id,)):
                        raise ValueError("A retained publication must be recovered before regenerating")
                    fields.update(context=None, result=None, thread_id=None)
                    with self.store.transaction() as db:
                        settings = {k: getattr(self.config, k) for k in ("executor", "api_provider", "api_url", "api_model", "model", "input_budget")}
                        db.execute("INSERT OR REPLACE INTO job_execution VALUES(?,?)", (job_id, dumps(settings)))
                self.store.update_job(job_id, event="job.retried", event_payload={"regenerate": regenerate}, **fields)
            else:
                raise ValueError("Job is already pending or running")
        return {"job_id": job_id, "state": self.store.job(job_id)["state"]}

    def worker(self, once: bool = False):
        try:
            with file_lock(self.config.root / "locks" / "worker-process.lock", blocking=False), WorkerHeartbeat(self.store) as heartbeat:
                while True:
                    current = Runtime(Config.load(self.config.root), self.session_factory, heartbeat)
                    with file_lock(self.config.root / "locks" / "worker.lock"):
                        current.drain()
                    if once:
                        return
                    time.sleep(current.config.poll_seconds)
        except BlockingIOError:
            return

    def wake(self) -> dict:
        if not self.config.auto_start:
            return {"worker": "manual"}
        with (self.config.root / "worker.log").open("ab") as log:
            collector = subprocess.Popen([sys.executable, "-m", "wikiskill.live.cli", "--root", str(self.config.root), "collector"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             cwd=self.config.root, start_new_session=True, close_fds=True)
            process = subprocess.Popen([sys.executable, "-m", "wikiskill.live.cli", "--root", str(self.config.root), "worker"],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       cwd=self.config.root, start_new_session=True, close_fds=True)
        return {"worker": "requested", "pid": process.pid, "collector_pid": collector.pid}
