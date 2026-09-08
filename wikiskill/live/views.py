from __future__ import annotations

import base64
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import Config, digest, normalized
from .skills import skill_text


def rows(db, sql: str, args=()) -> list[dict]:
    return [dict(row) for row in db.execute(sql, args)]


def page(items: list, offset: int, limit: int) -> dict:
    return {"items": items[:limit], "next_offset": offset + limit if len(items) > limit else None}


def has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class ReadView:
    """Build browser views using existing records and short, read-only transactions."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "state.sqlite3"

    @contextmanager
    def read(self):
        if not self.path.is_file():
            yield None
            return
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db if has_table(db, "projects") else None
        finally:
            db.rollback()
            db.close()

    def config(self) -> tuple[Config | None, str | None]:
        try:
            return Config.load(self.root), None
        except (ValueError, OSError) as exc:
            return None, str(exc)

    @staticmethod
    def worker(db) -> dict:
        if db is None or not has_table(db, "worker_runtime"):
            return {"status": "unknown"}
        result = rows(db, "SELECT * FROM worker_runtime WHERE id=1")
        if not result:
            return {"status": "unknown"}
        item = result[0]
        item["status"] = ("stopped" if item["state"] == "stopped" else
                          "online" if time.time() - item["heartbeat"] <= 15 else "stale")
        return item

    @staticmethod
    def require_project(db, project: str):
        if db is None or not db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
            raise KeyError("项目不存在")

    @staticmethod
    def require_job(db, job_id: str) -> dict:
        found = rows(db, "SELECT id,project,stage,skill,inputs FROM jobs WHERE id=?", (job_id,)) if db else []
        if not found:
            raise KeyError("批次不存在")
        return found[0]

    @staticmethod
    def require_skill(db, skill_id: str) -> dict:
        found = rows(db, "SELECT * FROM skills WHERE id=?", (skill_id,)) if db else []
        if not found:
            raise KeyError("Skill 不存在")
        return found[0]

    @staticmethod
    def queue(pending: int, batched: int, threshold: int | None, active: dict | None,
              project: str, enabled: bool | None = True) -> dict:
        if enabled is None or threshold is None:
            reason = "config_error"
        elif not enabled:
            reason = "disabled"
        elif active:
            reason = ("failed" if active["state"] == "failed" else
                      "shared_busy" if active["project"] != project else
                      "queued" if active["state"] == "queued" else "processing")
        else:
            reason = "ready" if pending >= threshold else "accumulating"
        return {"pending": pending, "batched": batched, "waiting": max(0, pending - batched),
                "threshold": threshold, "reason": reason, "job_id": active["id"] if active else None,
                "job_project": active["project"] if active else None}

    def snapshot(self) -> dict:
        config, error = self.config()
        settings = ({name: getattr(config, name) for name in (
            "raw_threshold", "wiki_threshold", "manage_external", "external_skills", "model",
            "timeout_seconds", "poll_seconds", "auto_start", "codex_command")} if config else None)
        with self.read() as db:
            result = {"initialized": db is not None, "root": str(self.root), "captured_at": time.time(),
                      "config": settings, "config_error": error, "worker": self.worker(db),
                      "cursor": 0, "projects": [], "totals": {"projects": 0, "skills": 0, "active": 0, "failed": 0}}
            if db is None:
                return result
            events = has_table(db, "runtime_events")
            if events:
                result["cursor"] = db.execute("SELECT coalesce(max(seq),0) FROM runtime_events").fetchone()[0]
            active_jobs = rows(db, "SELECT id,project,stage,skill,state,inputs FROM jobs WHERE state!='done' ORDER BY created,id")
            for project in rows(db, "SELECT * FROM projects ORDER BY name,id"):
                key = project["id"]
                raw = next((j for j in active_jobs if j["stage"] == "raw" and j["project"] == key), None)
                counts = db.execute("SELECT count(*),sum(consumed_by IS NULL) FROM observations WHERE project=?", (key,)).fetchone()
                batched = (db.execute("SELECT count(*) FROM observations WHERE project=? AND consumed_by IS NULL "
                                      "AND id IN (SELECT value FROM json_each(?))", (key, raw["inputs"])).fetchone()[0] if raw else 0)
                project["raw"] = self.queue(counts[1] or 0, batched, config.raw_threshold if config else None, raw, key)
                project["observation_count"] = counts[0]
                project["raw_count"] = db.execute("SELECT count(*) FROM raw WHERE project=?", (key,)).fetchone()[0]
                project["wiki_pages"] = db.execute("SELECT count(*) FROM wiki WHERE project=?", (key,)).fetchone()[0]
                project["skills"] = []
                for skill in rows(db, "SELECT s.* FROM skills s JOIN project_skills p ON p.skill=s.id WHERE p.project=? ORDER BY s.path", (key,)):
                    skill["name"] = Path(skill["path"]).name
                    skill["enabled"] = config.permits(Path(skill["path"]), bool(skill["owned"])) if config else None
                    skill["project_count"] = db.execute("SELECT count(*) FROM project_skills WHERE skill=?", (skill["id"],)).fetchone()[0]
                    active = next((j for j in active_jobs if j["skill"] == skill["id"]), None)
                    pending_sql = ("SELECT count(*) FROM wiki_changes w WHERE w.project=? AND NOT EXISTS "
                                   "(SELECT 1 FROM consumed_wiki c WHERE c.change_id=w.id AND c.skill=?)")
                    args = (key, skill["id"])
                    pending = db.execute(pending_sql, args).fetchone()[0]
                    batched = (db.execute(pending_sql + " AND w.id IN (SELECT value FROM json_each(?))",
                                          (*args, active["inputs"])).fetchone()[0] if active and active["project"] == key else 0)
                    skill["wiki"] = self.queue(pending, batched, config.wiki_threshold if config else None, active, key, skill["enabled"])
                    skill["version_count"] = db.execute("SELECT count(*) FROM versions WHERE skill=?", (skill["id"],)).fetchone()[0]
                    project["skills"].append(skill)
                recent = db.execute("SELECT max(created) FROM raw WHERE project=?", (key,)).fetchone()[0]
                event_at = db.execute("SELECT max(created) FROM runtime_events WHERE project=?", (key,)).fetchone()[0] if events else None
                project["updated_at"] = max(filter(None, (recent, event_at)), default=None)
                project["active_jobs"] = sum(j["project"] == key and j["state"] != "failed" for j in active_jobs)
                project["failed_jobs"] = sum(j["project"] == key and j["state"] == "failed" for j in active_jobs)
                result["projects"].append(project)
            result["totals"] = {"projects": len(result["projects"]),
                                "skills": db.execute("SELECT count(*) FROM skills").fetchone()[0],
                                "active": sum(j["state"] != "failed" for j in active_jobs),
                                "failed": sum(j["state"] == "failed" for j in active_jobs)}
            return result

    def _jobs(self, db, where: str, args, limit: int, offset: int = 0) -> list[dict]:
        events = has_table(db, "runtime_events")
        event_columns = ("(SELECT kind FROM runtime_events e WHERE e.job_id=j.id ORDER BY seq DESC LIMIT 1) phase,"
                         "(SELECT max(created) FROM runtime_events e WHERE e.job_id=j.id) updated_at," if events else
                         "NULL phase,NULL updated_at,")
        sql = ("SELECT j.id,j.project,p.name project_name,j.stage,j.skill,s.path skill_path,j.state,j.created,j.thread_id,j.error,"
               "j.report_sent,json_array_length(j.inputs) input_count,json_extract(j.report,'$.outcome') outcome,"
               "json_extract(j.report,'$.summary') summary,json_extract(j.report,'$.report_error') report_error,"
               "(SELECT state FROM versions v WHERE v.job_id=j.id) publication_state,"
               "(SELECT id FROM versions v WHERE v.job_id=j.id) version_id," + event_columns +
               ("(SELECT count(*) FROM runtime_events e WHERE e.job_id=j.id AND e.kind='job.retried') retry_count " if events else "0 retry_count ") +
               "FROM jobs j JOIN projects p ON p.id=j.project LEFT JOIN skills s ON s.id=j.skill WHERE " + where +
               " ORDER BY j.created DESC,j.id DESC LIMIT ? OFFSET ?")
        items = rows(db, sql, (*args, limit, offset))
        worker = self.worker(db)
        for item in items:
            item["report_from_previous_attempt"] = item["retry_count"] > 0 and item["state"] in {"queued", "generating", "prepared"}
            if item["report_from_previous_attempt"]:
                item.update(summary=None, outcome=None, report_error=None, report_sent=0)
            item["running_confirmed"] = worker["status"] == "online" and worker.get("job_id") == item["id"]
            item["last_activity"] = worker.get("last_activity") if worker.get("job_id") == item["id"] else item["updated_at"]
            item["content_status"] = (
                "published" if item["publication_state"] == "applied" else
                "publication_pending" if item["publication_state"] == "prepared" else
                "no_change" if item["outcome"] == "no_change" else
                "wiki_written" if item["outcome"] == "changed" and item["stage"] == "raw" else
                "failed" if item["state"] == "failed" else "pending")
            item["report_status"] = "sent" if item["report_sent"] else "failed" if item["report_error"] else "pending"
            if item["phase"] == "report.sending":
                item["report_status"] = "sending" if item["running_confirmed"] else "unconfirmed"
                item["report_error"] = None
        return items

    def jobs(self, project: str | None = None, state: str | None = None, stage: str | None = None,
             offset: int = 0, limit: int = 20) -> dict:
        with self.read() as db:
            if project:
                self.require_project(db, project)
            if db is None:
                return page([], offset, limit)
            where, args = ["1=1"], []
            for column, value in (("project", project), ("state", state), ("stage", stage)):
                if value:
                    where.append(f"j.{column}=?")
                    args.append(value)
            return page(self._jobs(db, " AND ".join(where), args, limit + 1, offset), offset, limit)

    def job(self, job_id: str) -> dict:
        with self.read() as db:
            self.require_job(db, job_id)
            job = self._jobs(db, "j.id=?", (job_id,), 1)[0]
            detail = db.execute("SELECT inputs,result,report FROM jobs WHERE id=?", (job_id,)).fetchone()
            for key in ("inputs", "result", "report"):
                job[key] = json.loads(detail[key]) if detail[key] is not None else None
            job["previous_report"] = job["report"] if job["report_from_previous_attempt"] else None
            if job["report_from_previous_attempt"]:
                job["report"] = None
            job["started_at"] = job["finished_at"] = None
            if has_table(db, "runtime_events"):
                start = db.execute("SELECT seq,created FROM runtime_events WHERE job_id=? AND kind='job.started' ORDER BY seq DESC LIMIT 1", (job_id,)).fetchone()
                if start:
                    job["started_at"] = start["created"]
                    job["finished_at"] = db.execute("SELECT max(created) FROM runtime_events WHERE job_id=? AND seq>? AND kind IN ('job.done','job.failed')", (job_id, start["seq"])).fetchone()[0]
            return job

    def inputs(self, job_id: str, offset: int = 0, limit: int = 50) -> dict:
        with self.read() as db:
            job = self.require_job(db, job_id)
            if job["stage"] == "raw":
                sql = ("SELECT o.*,r.source_id FROM observations o JOIN raw r ON r.id=o.raw_id "
                       "WHERE o.project=? AND o.id IN (SELECT value FROM json_each(?)) ORDER BY o.id LIMIT ? OFFSET ?")
            else:
                sql = "SELECT * FROM wiki_changes WHERE project=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id LIMIT ? OFFSET ?"
            items = rows(db, sql, (job["project"], job["inputs"], limit + 1, offset))
            if job["stage"] == "raw":
                for item in items:
                    item["body"] = json.loads(item["body"])
            return page(items, offset, limit)

    def events(self, job_id: str, offset: int = 0, limit: int = 50) -> dict:
        with self.read() as db:
            self.require_job(db, job_id)
            items = rows(db, "SELECT * FROM runtime_events WHERE job_id=? ORDER BY seq LIMIT ? OFFSET ?",
                         (job_id, limit + 1, offset)) if has_table(db, "runtime_events") else []
            for item in items:
                item["payload"] = json.loads(item["payload"])
            return page(items, offset, limit)

    def raw(self, project: str, offset: int = 0, limit: int = 20) -> dict:
        with self.read() as db:
            self.require_project(db, project)
            return page(rows(db, "SELECT r.id,r.source_id,r.created,json_array_length(r.payload,'$.observations') submitted,"
                             "json_extract(r.payload,'$.observations[0].problem') title,"
                             "(SELECT count(*) FROM observations o WHERE o.raw_id=r.id) added "
                             "FROM raw r WHERE r.project=? ORDER BY r.created DESC,r.id DESC LIMIT ? OFFSET ?",
                             (project, limit + 1, offset)), offset, limit)

    def raw_item(self, project: str, raw_id: str) -> dict:
        with self.read() as db:
            self.require_project(db, project)
            found = rows(db, "SELECT * FROM raw WHERE project=? AND id=?", (project, raw_id))
            if not found:
                raise KeyError("原始提交不存在")
            result = found[0]
            result["payload"] = json.loads(result["payload"])
            pending = rows(db, "SELECT id,state,inputs FROM jobs WHERE project=? AND stage='raw' AND state!='done'", (project,))
            batches = {key: job for job in pending for key in json.loads(job["inputs"])}
            observations = []
            seen = set()
            for original in result["payload"]["observations"]:
                key = digest({k: normalized(v) for k, v in original.items()})
                canonical = db.execute("SELECT id,raw_id,consumed_by FROM observations WHERE project=? AND digest=?", (project, key)).fetchone()
                item = {"body": original, "id": canonical["id"] if canonical else None,
                        "duplicate": key in seen or bool(canonical and canonical["raw_id"] != raw_id),
                        "canonical_raw_id": canonical["raw_id"] if canonical else None,
                        "consumed_by": canonical["consumed_by"] if canonical else None,
                        "batch": batches.get(canonical["id"]) if canonical else None}
                if item["batch"]:
                    item["batch"] = {k: item["batch"][k] for k in ("id", "state")}
                observations.append(item)
                seen.add(key)
            result["observations"] = observations
            return result

    def wiki(self, project: str, offset: int = 0, limit: int = 20) -> dict:
        with self.read() as db:
            self.require_project(db, project)
            return page(rows(db, "SELECT name,digest,substr(body,1,180) excerpt,length(body) characters,"
                             "(SELECT count(*) FROM wiki_changes c WHERE c.project=w.project AND c.name=w.name) revisions "
                             "FROM wiki w WHERE project=? ORDER BY name LIMIT ? OFFSET ?", (project, limit + 1, offset)), offset, limit)

    def wiki_item(self, project: str, name: str, offset: int = 0, limit: int = 20) -> dict:
        with self.read() as db:
            self.require_project(db, project)
            found = rows(db, "SELECT * FROM wiki WHERE project=? AND name=?", (project, name))
            if not found:
                raise KeyError("Wiki 页面不存在")
            result = found[0]
            result["metadata"] = json.loads(result["metadata"])
            changes = rows(db, "SELECT * FROM wiki_changes WHERE project=? AND name=? ORDER BY id DESC LIMIT ? OFFSET ?", (project, name, limit + 1, offset))
            for change in changes:
                change["source_job"] = change["job_id"] if db.execute("SELECT 1 FROM jobs WHERE id=?", (change["job_id"],)).fetchone() else None
                change["consumers"] = rows(db, "SELECT s.id,s.path,c.job_id consumed_by FROM skills s JOIN project_skills p ON p.skill=s.id "
                                           "LEFT JOIN consumed_wiki c ON c.skill=s.id AND c.change_id=? WHERE p.project=? ORDER BY s.path", (change["id"], project))
            result["changes"] = page(changes, offset, limit)
            return result

    def skill(self, skill_id: str, offset: int = 0, limit: int = 20) -> dict:
        config, _ = self.config()
        with self.read() as db:
            result = self.require_skill(db, skill_id)
            result["name"] = Path(result["path"]).name
            result["enabled"] = config.permits(Path(result["path"]), bool(result["owned"])) if config else None
            result["projects"] = rows(db, "SELECT p.* FROM projects p JOIN project_skills s ON s.project=p.id WHERE s.skill=? ORDER BY p.name", (skill_id,))
            result["versions"] = page(rows(db, "SELECT id,job_id,state,created,length(diff) diff_size FROM versions WHERE skill=? ORDER BY created DESC,id DESC LIMIT ? OFFSET ?", (skill_id, limit + 1, offset)), offset, limit)
            latest = db.execute("SELECT after_bundle FROM versions WHERE skill=? AND state='applied' ORDER BY created DESC,id DESC LIMIT 1", (skill_id,)).fetchone()
            result["published_text"] = skill_text(json.loads(latest[0])) if latest else None
        directory = Path(result["path"])
        result["disk_text"] = None
        result["disk_error"] = None
        try:
            target = directory / "SKILL.md"
            if directory.resolve() != directory or target.is_symlink():
                raise ValueError("目录或 SKILL.md 是软链接，无法读取")
            if target.is_file():
                with target.open("r", encoding="utf-8") as stream:
                    result["disk_text"] = stream.read(1_000_001)
                if len(result["disk_text"]) > 1_000_000:
                    result["disk_text"] = None
                    raise ValueError("SKILL.md 超过预览大小限制")
        except (OSError, ValueError) as exc:
            result["disk_error"] = str(exc)
        return result

    def version(self, skill_id: str, version_id: str) -> dict:
        with self.read() as db:
            self.require_skill(db, skill_id)
            found = rows(db, "SELECT * FROM versions WHERE skill=? AND id=?", (skill_id, version_id))
            if not found:
                raise KeyError("版本不存在")
            result = found[0]
            result["source_job"] = result["job_id"] if db.execute("SELECT 1 FROM jobs WHERE id=?", (result["job_id"],)).fetchone() else None
            for side in ("before", "after"):
                bundle = json.loads(result.pop(side + "_bundle"))
                result[side] = {"skill_md": skill_text(bundle), "files": [
                    {"path": name, "type": entry["type"], "mode": oct(entry["mode"]),
                     "bytes": len(entry["data"]) * 3 // 4 - entry["data"][-2:].count("=") if entry["type"] == "file" else 0}
                    for name, entry in sorted(bundle.items())]}
            return result

    def version_file(self, skill_id: str, version_id: str, side: str, name: str) -> bytes:
        if side not in {"before", "after"}:
            raise ValueError("side must be before or after")
        with self.read() as db:
            self.require_skill(db, skill_id)
            found = db.execute(f"SELECT {side}_bundle FROM versions WHERE skill=? AND id=?", (skill_id, version_id)).fetchone()
            entry = json.loads(found[0]).get(name) if found else None
            if not entry or entry["type"] != "file":
                raise KeyError("快照文件不存在")
            return base64.b64decode(entry["data"])


class ChangeCursor:
    """Observe commits on one read-only connection, including writes from other processes."""

    def __init__(self, view: ReadView):
        self.view = view
        self.db = None
        self.identity = None

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def poll(self) -> tuple[tuple, int]:
        config = self.view.root / "config.json"
        config_stamp = config.stat().st_mtime_ns if config.is_file() else None
        stat = self.view.path.stat() if self.view.path.is_file() else None
        identity = (stat.st_dev, stat.st_ino) if stat else None
        if identity != self.identity:
            self.close()
            self.identity = identity
        if identity is None:
            return (None, config_stamp), 0
        if self.db is None:
            self.db = sqlite3.connect(self.view.path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
            self.db.execute("PRAGMA query_only=ON")
        version = self.db.execute("PRAGMA data_version").fetchone()[0]
        seq = self.db.execute("SELECT coalesce(max(seq),0) FROM runtime_events").fetchone()[0] if has_table(self.db, "runtime_events") else 0
        worker = self.db.execute("SELECT state,heartbeat FROM worker_runtime WHERE id=1").fetchone() if has_table(self.db, "worker_runtime") else None
        stale = bool(worker and worker[0] == "online" and time.time() - worker[1] > 15)
        return (identity, version, config_stamp, stale), seq
