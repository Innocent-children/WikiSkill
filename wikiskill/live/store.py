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

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
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

    def project(self, directory: str) -> str:
        key, path = project_identity(directory)
        with self.transaction() as db:
            db.execute("INSERT INTO projects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path",
                       (key, path, Path(path).name))
            skill_path = self.config.root / "skills" / f"wikiskill-{key}"
            skill_id = digest(str(skill_path))[:24]
            db.execute("INSERT OR IGNORE INTO skills VALUES(?,?,1)", (skill_id, str(skill_path)))
            db.execute("INSERT OR IGNORE INTO project_skills VALUES(?,?)", (key, skill_id))
        return key

    def enroll(self, project: str, directory: str) -> dict:
        path = Path(directory).expanduser().resolve(strict=True)
        if not path.is_dir() or not (path / "SKILL.md").is_file():
            raise ValueError("External skill directory must contain SKILL.md")
        if not self.config.permits(path, False):
            raise ValueError("Enable manage_external and list this directory in external_skills first")
        key = digest(str(path))[:24]
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO skills VALUES(?,?,0)", (key, str(path)))
            db.execute("INSERT OR IGNORE INTO project_skills VALUES(?,?)", (project, key))
        return {"skill_id": key, "path": str(path)}

    def collect(self, project: str, source_id: str, observations: list[dict], metadata: dict) -> dict:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a stable nonempty event identifier")
        if not isinstance(observations, list) or not isinstance(metadata, dict):
            raise ValueError("observations must be a list and metadata an object")
        bodies = []
        for item in observations:
            if not isinstance(item, dict) or set(item) != {"problem", "action", "outcome", "lesson"}:
                raise ValueError("Each observation requires problem, action, outcome and lesson")
            if any(not isinstance(value, str) for value in item.values()):
                raise ValueError("Observation fields must be text")
            body = {key: normalized(value) for key, value in item.items()}
            if not body["problem"] or not body["outcome"]:
                raise ValueError("An observation requires an actual problem and outcome")
            bodies.append(body)
        payload = {"observations": observations, "metadata": metadata}
        with self.transaction() as db:
            old = db.execute("SELECT * FROM raw WHERE project=? AND source_id=?", (project, source_id)).fetchone()
            if old:
                if json.loads(old["payload"])["observations"] != observations:
                    raise ValueError("source_id was already used with different observations")
                return {"raw_id": old["id"], "new_observations": 0, "duplicate": True}
            key = uuid.uuid4().hex
            db.execute("INSERT INTO raw VALUES(?,?,?,?,?)", (key, project, source_id, dumps(payload), time.time()))
            count = 0
            for body in bodies:
                count += db.execute("INSERT OR IGNORE INTO observations(project,raw_id,digest,body) VALUES(?,?,?,?)",
                                    (project, key, digest(body), dumps(body))).rowcount
        return {"raw_id": key, "new_observations": count, "duplicate": False}

    def job(self, key: str) -> dict:
        values = self.rows("SELECT * FROM jobs WHERE id=?", (key,))
        if not values:
            raise ValueError("Unknown job")
        row = values[0]
        for name in ("inputs", "context", "result", "report"):
            row[name] = json.loads(row[name]) if row[name] is not None else None
        return row

    def update_job(self, key: str, **fields) -> None:
        allowed = {"state", "context", "result", "thread_id", "report", "report_sent", "error"}
        if not fields or set(fields) - allowed:
            raise ValueError("Invalid job fields")
        values = [dumps(v) if k in {"context", "result", "report"} else v for k, v in fields.items()]
        with self.transaction() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", [*values, key])

    def skills(self, project: str) -> list[dict]:
        return self.rows("SELECT s.* FROM skills s JOIN project_skills p ON p.skill=s.id WHERE p.project=?",
                         (project,))

    def status(self, project: str | None = None) -> dict:
        projects = self.rows("SELECT * FROM projects" + (" WHERE id=?" if project else ""),
                             (project,) if project else ())
        for item in projects:
            key = item["id"]
            item["raw_pending"] = self.rows("SELECT count(*) n FROM observations WHERE project=? AND consumed_by IS NULL", (key,))[0]["n"]
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
