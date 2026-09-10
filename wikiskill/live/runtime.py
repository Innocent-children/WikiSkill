from __future__ import annotations

import json
import os
import re
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
from .evolution import enrich_context, record_sources, purpose_bundle


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


def write_pages(db, project: str, pages: list[dict], metadata: dict, job_id: str, *, record_body_changes: bool = False) -> int:
    count = 0
    for page in pages:
        content_digest = digest(normalized(page["body"]))
        current = db.execute("SELECT digest,body FROM wiki WHERE project=? AND name=?", (project, page["name"])).fetchone()
        db.execute("INSERT INTO wiki VALUES(?,?,?,?,?) ON CONFLICT(project,name) DO UPDATE SET "
                   "body=excluded.body,digest=excluded.digest,metadata=excluded.metadata",
                   (project, page["name"], page["body"], content_digest, dumps(metadata)))
        if current is None or current["digest"] != content_digest or (record_body_changes and current["body"] != page["body"]):
            cursor = db.execute("INSERT INTO wiki_changes(project,name,digest,body,job_id) VALUES(?,?,?,?,?)",
                                (project, page["name"], content_digest, page["body"], job_id))
            count += cursor.rowcount
            record_sources(db, project, page['name'], cursor.lastrowid, metadata.get('source_ids', {}).get(page['name'], []))
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
        if self.config.capture_mode != "automatic":
            return []
        created = []
        with self.store.transaction() as db:
            for project in db.execute("SELECT id FROM projects").fetchall():
                key = project["id"]
                last = db.execute("SELECT last_run FROM automatic_analysis WHERE project=?", (key,)).fetchone()
                if not last or time.time() - last[0] >= self.config.analysis_interval_minutes * 60:
                    groups = self._raw_groups(db, self._raw_inputs(db, key, automatic=True))
                    busy = self._busy_raw_sessions(db, key)
                    queued = [self._enqueue(db, key, 'raw', None, records, automatic=True)
                              for session, records in groups.items() if session not in busy]
                    if queued:
                        created.extend(queued)
                        db.execute("INSERT OR REPLACE INTO automatic_analysis VALUES(?,?)", (key, time.time()))
                # Each Wiki updates its linked Skills; unassigned pages get their own focused Skill.
                pages = db.execute("SELECT w.name,max(c.id) id FROM wiki w JOIN wiki_changes c ON c.project=w.project AND c.name=w.name WHERE w.project=? GROUP BY w.name", (key,)).fetchall()
                targets = {}
                for page in pages:
                    links = db.execute("SELECT skill,change_id FROM skill_wiki WHERE project=? AND name=?", (key, page['name'])).fetchall()
                    if not links:
                        base = f"{page['name'][:36]}-{digest(key + '/' + page['name'])[:8]}"
                        path = self.config.root / 'skills' / base
                        suffix = 2
                        while path.exists() or db.execute('SELECT 1 FROM skills WHERE path=?', (str(path),)).fetchone():
                            path = self.config.root / 'skills' / f'{base}-{suffix}'
                            suffix += 1
                        sid = digest(str(path))[:24]
                        db.execute("INSERT INTO skills VALUES(?,?,1)", (sid, str(path)))
                        db.execute("INSERT OR IGNORE INTO project_skills VALUES(?,?)", (key, sid))
                        db.execute('INSERT INTO skill_wiki VALUES(?,?,?,0)', (sid, key, page['name']))
                        links = [{'skill': sid, 'change_id': 0}]
                    for link in links:
                        if link['change_id'] != page['id']:
                            targets.setdefault(link['skill'], []).append(page['id'])
                for sid, inputs in targets.items():
                    busy = db.execute("SELECT 1 FROM jobs WHERE skill=? AND state!='done' AND id IN (SELECT job FROM job_execution)", (sid,)).fetchone()
                    if not busy:
                        created.append(self._enqueue(db, key, 'skill', sid, inputs, automatic=True))
        return created

    def _raw_inputs(self, db, project, automatic=False):
        records = db.execute("SELECT r.id FROM trace_records r JOIN trace_turns t ON t.id=r.turn_id "
            "WHERE r.project=? AND r.consumed_by IS NULL" + (" AND t.ended=1" if automatic else "") + " ORDER BY r.id", (project,)).fetchall()
        inputs = [r['id'] for r in records]
        if automatic:
            now = time.time()
            return [record for session, selected in self._raw_groups(db, inputs).items()
                    if self._automatic_session_ready(db, session, now) for record in selected]
        return inputs

    def _automatic_session_ready(self, db, session, now):
        """Wait for a full hour of session inactivity before automatic analysis."""
        column = 's.session' if session[0] == 'session' else 's.identity'
        rows = db.execute(
            "SELECT max(r.created) latest, "
            "max(CASE WHEN t.ended=0 AND t.turn_key!='unassigned' THEN 1 ELSE 0 END) unfinished "
            "FROM trace_sources s JOIN trace_records r ON r.source=s.id "
            "JOIN trace_turns t ON t.id=r.turn_id WHERE " + column + "=?", (session[1],)).fetchone()
        if rows['latest'] is None or rows['latest'] > now - 3600 or rows['unfinished']:
            return False
        sources = db.execute(
            "SELECT s.path,s.position FROM trace_sources s WHERE " + column + "=? "
            "AND s.generation=(SELECT max(other.generation) FROM trace_sources other WHERE other.identity=s.identity)",
            (session[1],)).fetchall()
        for source in sources:
            try:
                stat = Path(source['path']).stat()
            except OSError:
                return False
            # File activity also covers records the collector has yet to ingest.
            if stat.st_mtime > now - 3600 or stat.st_size != source['position']:
                return False
        return True

    def _automatic_job_ready(self, db, job):
        if job['stage'] != 'raw' or job['state'] != 'queued' or not db.execute(
                'SELECT 1 FROM automatic_jobs WHERE job=?', (job['id'],)).fetchone():
            return True
        groups = self._raw_groups(db, job['inputs'])
        return bool(groups) and all(self._automatic_session_ready(db, session, time.time()) for session in groups)

    def _raw_groups(self, db, inputs):
        """Group complete selected records by Codex session, using source identity when absent."""
        rows = db.execute("SELECT r.id,r.session,s.identity FROM trace_records r "
                          "JOIN trace_sources s ON s.id=r.source "
                          "WHERE r.id IN (SELECT value FROM json_each(?)) ORDER BY r.id", (dumps(inputs),))
        groups = {}
        for row in rows:
            key = ('session', row['session']) if row['session'] else ('source', row['identity'])
            groups.setdefault(key, []).append(row['id'])
        return groups

    def _busy_raw_sessions(self, db, project):
        inputs = [row[0] for row in db.execute(
            "SELECT value FROM jobs j,json_each(j.inputs) WHERE j.project=? AND j.stage='raw' "
            "AND j.state!='done' AND j.id IN (SELECT job FROM job_execution)", (project,))]
        return set(self._raw_groups(db, inputs))

    def _enqueue_raw(self, db, project, inputs):
        groups = self._raw_groups(db, inputs)
        if self._busy_raw_sessions(db, project).intersection(groups):
            raise ValueError('选中会话已有等待、执行中或失败的批次，请在执行记录中处理该会话的现有批次')
        return [self._enqueue(db, project, 'raw', None, records) for records in groups.values()]

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
            if stage == 'skill' and active:
                raise ValueError("A batch is already pending or failed; open execution history to run or retry it")
            if inputs is not None and (not isinstance(inputs, list) or not inputs or any(type(x) is not int for x in inputs)
                                       or len(inputs) != len(set(inputs)) or not set(inputs) <= set(pending)):
                raise ValueError("Select distinct, pending input IDs from this project")
            chosen = inputs if inputs is not None else pending
            if stage == 'raw' and inputs is None:
                busy = self._busy_raw_sessions(db, project)
                chosen = [record for session, records in self._raw_groups(db, chosen).items()
                          if session not in busy for record in records]
            if not chosen:
                raise ValueError("No pending inputs")
            keys = self._enqueue_raw(db, project, chosen) if stage == 'raw' else [self._enqueue(db, project, stage, skill, chosen)]
        return {"job_id": keys[0], "job_ids": keys, "state": "queued"}

    def _enqueue(self, db, project: str, stage: str, skill: str | None, inputs: list[int], automatic: bool = False) -> str:
        key = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,project,stage,skill,state,inputs,created) VALUES(?,?,?,?,'queued',?,?)",
                   (key, project, stage, skill, dumps(inputs), time.time()))
        settings = {k: getattr(self.config, k) for k in ("executor", "api_provider", "api_url", "api_model", "model", "ollama_model")}
        db.execute("INSERT INTO job_execution VALUES(?,?)", (key, dumps(settings)))
        if automatic:
            db.execute('INSERT INTO automatic_jobs VALUES(?)', (key,))
        Store.event(db, "job.queued", project=project, job_id=key, skill=skill, input_count=len(inputs))
        return key

    def _context(self, job: dict) -> dict:
        placeholders = ",".join("?" for _ in job["inputs"])
        context = {"project": job["project"]}
        if job["stage"] == "raw":
            context["wiki"] = self.store.rows("SELECT name,body FROM wiki WHERE project=? ORDER BY name", (job["project"],))
            rows = self.store.rows(f"SELECT id,original,session,turn_id FROM trace_records WHERE id IN ({placeholders}) ORDER BY source,offset", job["inputs"])
            context["records"] = [{"id": r["id"], "session": r["session"], "turn_id": r["turn_id"], "original": r["original"].decode("utf-8", errors="replace")} for r in rows]
        else:
            skill = self.skills.get(job["skill"])
            bundle = snapshot(Path(skill["path"]))
            context.update(before_bundle=bundle, skill_md=skill_text(bundle),
                           suggested_name=Path(skill["path"]).name,
                           changes=self.store.rows(f"SELECT c.id,w.name,w.body FROM wiki w JOIN wiki_changes c ON c.id=(SELECT max(id) FROM wiki_changes WHERE project=w.project AND name=w.name) WHERE w.project=? AND w.name IN (SELECT name FROM wiki_changes WHERE id IN ({placeholders})) ORDER BY w.name", [job['project'], *job['inputs']]))
            context["wiki"] = [{"name": p["name"], "body": p["body"]} for p in context["changes"]]
        with self.store.connect() as db:
            return enrich_context(db, context, job["skill"])

    def _apply(self, job: dict) -> dict:
        output = job["result"]
        expected = {"summary", "pages" if job["stage"] == "raw" else "skill_md"}
        if not isinstance(output, dict) or not expected <= set(output) or set(output) - expected - ({"wiki_documents"} if job["stage"] == "raw" else {"purpose_md"}) or not isinstance(output["summary"], str):
            raise ValueError("Codex result has invalid fields")
        details = {"changed": False}
        if job["stage"] == "raw":
            pages = [{k: p[k] for k in ('name', 'body')} for p in output['pages']]
            validate_pages(pages)
            allowed = set(job['inputs'])
            for page in output['pages']:
                ids = page.get('source_ids', [])
                if not ids or any(type(i) is not int or i not in allowed for i in ids):
                    raise ValueError('Wiki 来源必须引用本批原始记录')
        else:
            before = job["context"]["before_bundle"]
            text = output["skill_md"]
            if text is not None and not isinstance(text, str):
                raise ValueError("skill_md must be text or null")
            after = before if text is None or normalized(text) == normalized(skill_text(before)) else with_skill(before, text)
            if after != before:
                import yaml
                if yaml.safe_load(text.split('---', 2)[1])['name'] != job['context']['suggested_name']:
                    raise ValueError('Skill 名称必须与选定目标一致')
                after = purpose_bundle(after, job, output['summary'], output.get('purpose_md'))
            details = self.skills.publish(job["skill"], job["id"], before, after)
        with self.store.transaction() as db:
            if job["stage"] == "raw":
                original = {page["name"]: normalized(page["body"]) for page in job["context"]["wiki"]}
                for page in output["pages"]:
                    current = db.execute("SELECT body FROM wiki WHERE project=? AND name=?",
                                         (job["project"], page["name"])).fetchone()
                    if (normalized(current["body"]) if current else None) != original.get(page["name"]):
                        raise ValueError("Wiki page changed during generation; retry with regenerate=true")
                if 'wiki_documents' in output:
                    current_wiki = {r['name']: normalized(r['body']) for r in db.execute('SELECT name,body FROM wiki WHERE project=?', (job['project'],))}
                    if current_wiki != original:
                        raise ValueError('Wiki changed during generation; regenerate before updating its index')
                    saved_documents = {r['kind']:r['body'] for r in db.execute('SELECT kind,body FROM wiki_documents WHERE project=?', (job['project'],))}
                    if saved_documents != job['context'].get('wiki_documents', {}):
                        raise ValueError('Wiki index or log changed during generation; regenerate')
                    document = output['wiki_documents']
                    db.execute('INSERT OR REPLACE INTO wiki_documents VALUES(?,?,?)', (job['project'],'index',document['index']))
                    log = saved_documents.get('log', '') + f"\n## {job['id']}\n\n{document['append_log']}\n"
                    db.execute('INSERT OR REPLACE INTO wiki_documents VALUES(?,?,?)', (job['project'],'log',log))
                count = write_pages(db, job["project"], pages, {"job_id": job["id"],
                                    'source_ids': {p['name']: p['source_ids'] for p in output['pages']}}, job["id"])
                details = {"changed": count > 0, "wiki_changes": count, "pages": output["pages"]}
                db.executemany("UPDATE trace_records SET consumed_by=? WHERE id=? AND consumed_by IS NULL",
                               [(job["id"], key) for key in job["inputs"]])
            else:
                for p in job['context']['changes']:
                    db.execute('INSERT OR REPLACE INTO skill_wiki VALUES(?,?,?,?)', (job['skill'], job['project'], p['name'], p['id']))
                db.executemany("INSERT OR IGNORE INTO consumed_wiki VALUES(?,?,?)",
                               [(job["skill"], key, job["id"]) for key in job["inputs"]])
            report = {"job_id": job["id"], "project": job["project"], "stage": job["stage"], "skill": job["skill"],
                      "thread_id": job["thread_id"], "outcome": "changed" if details["changed"] else "no_change",
                      "summary": output["summary"], "inputs": job["inputs"],
                      'analysis': (job.get('report') or {}).get('analysis'), **details}
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
            return self._run_job(job_id)
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
            with self.store.transaction() as db:
                if not self._automatic_job_ready(db, job):
                    return False
                if job['stage'] == 'raw' and job['state'] == 'queued' and db.execute(
                        'SELECT 1 FROM automatic_jobs WHERE job=?', (job_id,)).fetchone():
                    sessions = self._raw_groups(db, job['inputs'])
                    pending = self._raw_groups(db, self._raw_inputs(db, job['project'], automatic=True))
                    inputs = [record for session in sessions for record in pending.get(session, [])]
                    if not inputs:
                        return False
                    db.execute('UPDATE jobs SET inputs=?,context=NULL WHERE id=?', (dumps(inputs), job_id))
            job = self.store.job(job_id)
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
                factory = ApiSession if config.executor in {"api", "ollama"} else self.session_factory
                self.store.job_event(job_id, "api.connecting" if config.executor in {"api", "ollama"} else "codex.connecting")
                with factory(config) as session:
                    attempt = uuid.uuid4().hex
                    response_ids = []
                    def record_response(value):
                        response_id = f'{attempt}-{len(response_ids) + 1:04d}'
                        path = self.config.root / 'reports' / 'model-responses' / job_id / f'{response_id}.json'
                        atomic_text(path, dumps({**value, 'id': response_id, 'created': time.time()}) + '\n')
                        path.chmod(0o600)
                        response_ids.append(response_id)
                        self.store.job_event(job_id, 'model.response', response_id=response_id, call=value.get('call'))
                    session.on_response = record_response
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
                                try:
                                    result = session.generate(job["stage"], context)
                                finally:
                                    self.store.update_job(job_id, report={'analysis': getattr(session, 'metrics', {})})
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
                  "inputs": job["inputs"], "analysis": (job.get("report") or {}).get("analysis"), "publication": self.store.rows(
                      "SELECT id,state,diff FROM versions WHERE job_id=?", (job_id,))}
        self.store.update_job(job_id, state="failed", error=str(exc), report=report, report_sent=0)

    def drain(self) -> int:
        count = 0
        config_file = self.config.root / 'config.json'
        settings_revision = config_file.stat().st_mtime_ns
        while True:
            current_revision = config_file.stat().st_mtime_ns
            if current_revision != settings_revision:
                self.config = Config.load(self.config.root)
                self.store.config = self.config
                settings_revision = current_revision
            self.schedule()
            jobs = self.store.rows("SELECT id FROM jobs WHERE state NOT IN ('done','failed') AND id IN (SELECT job FROM job_execution)" + (" AND id NOT IN (SELECT job FROM automatic_jobs)" if self.config.capture_mode == "manual" else "") + " ORDER BY created")
            ready = None
            for row in jobs:
                job = self.store.job(row['id'])
                with self.store.transaction() as db:
                    if self._automatic_job_ready(db, job):
                        ready = row['id']
                        break
            if ready is None:
                return count
            if self.run_job(ready) is False:
                return count
            count += 1

    def run_manually(self, job_id: str):
        with self.store.transaction() as db:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] != 'queued':
                raise ValueError('请选择等待执行的批次')
            db.execute('DELETE FROM automatic_jobs WHERE job=?', (job_id,))
            Store.event(db, 'job.manual', job_id=job_id)
        return {'job_id': job_id, 'state': 'queued'}

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
                factory = ApiSession if config.executor in {"api", "ollama"} else self.session_factory
                with factory(config) as session:
                    if job["thread_id"]:
                        session.resume(job["thread_id"])
                    self._report(session, job_id)
            elif job["state"] == "failed":
                if regenerate:
                    if self.store.rows("SELECT id FROM versions WHERE job_id=?", (job_id,)):
                        raise ValueError("A retained publication must be recovered before regenerating")
                with self.store.transaction() as db:
                    if regenerate:
                        settings = {k: getattr(self.config, k) for k in ("executor", "api_provider", "api_url", "api_model", "model", "ollama_model")}
                        db.execute("INSERT OR REPLACE INTO job_execution VALUES(?,?)", (job_id, dumps(settings)))
                        db.execute('UPDATE jobs SET context=NULL,result=NULL,thread_id=NULL WHERE id=?', (job_id,))
                    db.execute('DELETE FROM automatic_jobs WHERE job=?', (job_id,))
                    db.execute("UPDATE jobs SET state='queued',error=NULL WHERE id=?", (job_id,))
                    Store.event(db, 'job.retried', project=job['project'], job_id=job_id,
                                skill=job['skill'], regenerate=regenerate)
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
        from .services import ensure_services
        try:
            return ensure_services(self.config.root)
        except (OSError, ValueError, RuntimeError) as exc:
            return {"worker": "unavailable", "worker_error": str(exc)}
