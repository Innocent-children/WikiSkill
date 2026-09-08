from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import Config, digest, normalized, project_identity


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS raw(id TEXT PRIMARY KEY, project TEXT NOT NULL, source_id TEXT NOT NULL,
 payload TEXT NOT NULL, created REAL NOT NULL, UNIQUE(project, source_id));
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, project TEXT NOT NULL, raw_id TEXT NOT NULL,
 digest TEXT NOT NULL, body TEXT NOT NULL, consumed_by TEXT, UNIQUE(project,digest));
CREATE TABLE IF NOT EXISTS wiki(project TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
 digest TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(project,name));
CREATE TABLE IF NOT EXISTS wiki_changes(id INTEGER PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL,
 digest TEXT NOT NULL, body TEXT NOT NULL, job_id TEXT NOT NULL, UNIQUE(project,name,digest));
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, owned INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS project_skills(project TEXT NOT NULL, skill TEXT NOT NULL,
 PRIMARY KEY(project,skill));
CREATE TABLE IF NOT EXISTS consumed_wiki(skill TEXT NOT NULL, change_id INTEGER NOT NULL,
 job_id TEXT NOT NULL, PRIMARY KEY(skill,change_id));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT NOT NULL, stage TEXT NOT NULL,
 skill TEXT, state TEXT NOT NULL, inputs TEXT NOT NULL, context TEXT, result TEXT,
 thread_id TEXT, report TEXT, report_sent INTEGER NOT NULL DEFAULT 0, error TEXT,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, skill TEXT NOT NULL, job_id TEXT UNIQUE NOT NULL,
 before_bundle TEXT NOT NULL, after_bundle TEXT NOT NULL, diff TEXT NOT NULL,
 state TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
 project TEXT, job_id TEXT, skill TEXT, kind TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runtime_events_job ON runtime_events(job_id,seq);
CREATE INDEX IF NOT EXISTS runtime_events_project ON runtime_events(project,seq);
CREATE TABLE IF NOT EXISTS worker_runtime(id INTEGER PRIMARY KEY CHECK(id=1),
 instance TEXT NOT NULL, pid INTEGER NOT NULL, started REAL NOT NULL, heartbeat REAL NOT NULL,
 state TEXT NOT NULL, job_id TEXT, last_activity REAL, error TEXT);
CREATE INDEX IF NOT EXISTS jobs_project_created ON jobs(project,created);
CREATE INDEX IF NOT EXISTS observations_pending ON observations(project,consumed_by);
CREATE INDEX IF NOT EXISTS raw_project_created ON raw(project,created);
CREATE INDEX IF NOT EXISTS versions_skill_created ON versions(skill,created);
CREATE TABLE IF NOT EXISTS capture_state(id INTEGER PRIMARY KEY CHECK(id=1), enabled REAL NOT NULL,
 history_requested INTEGER NOT NULL DEFAULT 0, heartbeat REAL, error TEXT);
CREATE TABLE IF NOT EXISTS trace_sources(id INTEGER PRIMARY KEY, identity TEXT NOT NULL, path TEXT NOT NULL,
 session TEXT, project TEXT, cwd TEXT, position INTEGER NOT NULL DEFAULT 0,
 start_position INTEGER NOT NULL DEFAULT 0, fingerprint TEXT NOT NULL, prefix_length INTEGER NOT NULL,
 active_turn TEXT, generation INTEGER NOT NULL DEFAULT 0, UNIQUE(identity,generation));
CREATE TABLE IF NOT EXISTS trace_turns(id INTEGER PRIMARY KEY, source INTEGER NOT NULL, turn_key TEXT NOT NULL,
 project TEXT, session TEXT, ended INTEGER NOT NULL DEFAULT 0, UNIQUE(source,turn_key));
CREATE TABLE IF NOT EXISTS trace_records(id INTEGER PRIMARY KEY, source INTEGER NOT NULL, offset INTEGER NOT NULL,
 original BLOB NOT NULL, project TEXT, session TEXT, turn_id INTEGER, event_type TEXT,
 parse_error TEXT, consumed_by TEXT, created REAL NOT NULL, UNIQUE(source,offset));
CREATE INDEX IF NOT EXISTS trace_records_pending ON trace_records(project,consumed_by,id);
CREATE TABLE IF NOT EXISTS generated_sessions(id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS job_execution(job TEXT PRIMARY KEY, settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS installations(id TEXT PRIMARY KEY, skill TEXT NOT NULL, target TEXT NOT NULL,
 before_bundle TEXT NOT NULL, after_bundle TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
"""


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    """Own transactions and queries; the runtime owns scheduling and consumption decisions."""

    def __init__(self, config: Config):
        self.config = config
        config.initialize()
        self.path = config.root / "state.sqlite3"
        with self.connect() as db:
            db.executescript(SCHEMA)
            # Wiki history records each actual edit, including a return to an earlier body.
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='wiki_changes'").fetchone()[0]
            if "UNIQUE(project,name,digest)" in sql:
                db.executescript("""BEGIN IMMEDIATE;
                ALTER TABLE wiki_changes RENAME TO wiki_changes_previous;
                CREATE TABLE wiki_changes(id INTEGER PRIMARY KEY, project TEXT NOT NULL, name TEXT NOT NULL,
                 digest TEXT NOT NULL, body TEXT NOT NULL, job_id TEXT NOT NULL);
                INSERT INTO wiki_changes SELECT * FROM wiki_changes_previous;
                DROP TABLE wiki_changes_previous;
                COMMIT;""")

    @contextmanager
    def connect(self, timeout: float = 30):
        db = sqlite3.connect(self.path, timeout=timeout)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    @staticmethod
    def event(db, kind: str, *, project: str | None = None, job_id: str | None = None,
              skill: str | None = None, **payload) -> None:
        """Append an event in the transaction that owns its related change."""
        db.execute("INSERT INTO runtime_events(project,job_id,skill,kind,created,payload) VALUES(?,?,?,?,?,?)",
                   (project, job_id, skill, kind, time.time(), dumps(payload)))

    def job_event(self, job_id: str, kind: str, **payload) -> None:
        with self.transaction() as db:
            job = db.execute("SELECT project,skill FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise ValueError("Unknown job")
            self.event(db, kind, project=job["project"], skill=job["skill"], job_id=job_id, **payload)

    def project(self, directory: str) -> str:
        key, path = project_identity(directory)
        with self.transaction() as db:
            previous = db.execute("SELECT path FROM projects WHERE id=?", (key,)).fetchone()
            db.execute("INSERT INTO projects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path",
                       (key, path, Path(path).name))
            skill_path = self.config.root / "skills" / f"wikiskill-{key}"
            skill_id = digest(str(skill_path))[:24]
            db.execute("INSERT OR IGNORE INTO skills VALUES(?,?,1)", (skill_id, str(skill_path)))
            db.execute("INSERT OR IGNORE INTO project_skills VALUES(?,?)", (key, skill_id))
            if previous is None or previous["path"] != path:
                self.event(db, "project.registered", project=key)
        return key

    def job(self, key: str) -> dict:
        values = self.rows("SELECT * FROM jobs WHERE id=?", (key,))
        if not values:
            raise ValueError("Unknown job")
        row = values[0]
        for name in ("inputs", "context", "result", "report"):
            row[name] = json.loads(row[name]) if row[name] is not None else None
        return row

    def update_job(self, key: str, *, event: str | None = None, event_payload: dict | None = None, **fields) -> None:
        allowed = {"state", "context", "result", "thread_id", "report", "report_sent", "error"}
        if not fields or set(fields) - allowed:
            raise ValueError("Invalid job fields")
        values = [dumps(v) if k in {"context", "result", "report"} else v for k, v in fields.items()]
        with self.transaction() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", [*values, key])
            if "state" in fields or event:
                job = db.execute("SELECT project,skill FROM jobs WHERE id=?", (key,)).fetchone()
                if job:
                    self.event(db, event or "job." + fields["state"], project=job["project"], skill=job["skill"],
                               job_id=key, **{"error": fields.get("error"), **(event_payload or {})})

    def skills(self, project: str) -> list[dict]:
        return self.rows("SELECT s.* FROM skills s JOIN project_skills p ON p.skill=s.id WHERE p.project=?",
                         (project,))

    def status(self, project: str | None = None) -> dict:
        projects = self.rows("SELECT * FROM projects" + (" WHERE id=?" if project else ""),
                             (project,) if project else ())
        for item in projects:
            key = item["id"]
            item["raw_pending"] = self.rows("SELECT count(*) n FROM trace_records WHERE project=? AND consumed_by IS NULL", (key,))[0]["n"]
            item["wiki_pages"] = self.rows("SELECT count(*) n FROM wiki WHERE project=?", (key,))[0]["n"]
            item["skills"] = self.skills(key)
            for skill in item["skills"]:
                skill["enabled"] = self.config.permits(Path(skill["path"]), bool(skill["owned"]))
                skill["wiki_pending"] = self.rows("SELECT count(*) n FROM wiki_changes w WHERE project=? AND NOT EXISTS "
                    "(SELECT 1 FROM consumed_wiki c WHERE c.change_id=w.id AND c.skill=?)", (key, skill["id"]))[0]["n"]
            item["jobs"] = self.rows("SELECT id,stage,skill,state,thread_id,error,report_sent,created FROM jobs WHERE project=? ORDER BY created DESC LIMIT 50", (key,))
        return {"projects": projects, "thresholds": {"raw": self.config.raw_threshold, "wiki": self.config.wiki_threshold}}

    def query(self, project: str, layer: str, key: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Use offset >= 0 and limit between 1 and 100")
        tables = {"raw": ("raw", "id"), "wiki": ("wiki", "name"), "reports": ("jobs", "id")}
        if layer not in tables:
            raise ValueError("layer must be raw, wiki or reports; use context/history for skills")
        table, column = tables[layer]
        where, args = "project=?", [project]
        if key is not None:
            where += f" AND {column}=?"
            args.append(key)
        rows = self.rows(f"SELECT * FROM {table} WHERE {where} ORDER BY {column} LIMIT ? OFFSET ?",
                         [*args, limit + 1, offset])
        for row in rows:
            for field in ("payload", "metadata", "inputs", "context", "result", "report"):
                if field in row and row[field] is not None:
                    row[field] = json.loads(row[field])
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}
