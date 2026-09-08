from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .config import Config, digest
from .skills import file_lock
from .store import Store


def parse_record(line: bytes) -> tuple[dict, str | None]:
    try:
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("record is not an object")
        return value, None
    except (ValueError, UnicodeError) as exc:
        return {}, str(exc)


class Collector:
    """Own read-only transcript ingestion; Store owns the SQLite transactions."""

    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config)

    def request_history(self):
        with self.store.transaction() as db:
            db.execute("INSERT OR IGNORE INTO capture_state(id,enabled) VALUES(1,?)", (time.time(),))
            db.execute("UPDATE capture_state SET history_requested=1 WHERE id=1")
        return {"requested": True}

    def scan(self) -> int:
        with file_lock(self.config.root / "locks" / "capture.lock"):
            with self.store.transaction() as db:
                db.execute("INSERT OR IGNORE INTO capture_state(id,enabled) VALUES(1,?)", (time.time(),))
                state = dict(db.execute("SELECT * FROM capture_state WHERE id=1").fetchone())
            errors, count = [], 0
            home = Path(self.config.codex_home).expanduser()
            for directory in (home / "sessions", home / "archived_sessions"):
                for path in sorted(directory.rglob("*.jsonl")) if directory.exists() else []:
                    try:
                        count += self._file(path, state)
                    except (OSError, ValueError) as exc:
                        errors.append(f"{path.name}: {exc}")
            with self.store.transaction() as db:
                db.execute("UPDATE capture_state SET heartbeat=?,error=?,history_requested=? WHERE id=1",
                           (time.time(), "\n".join(errors) or None, int(bool(errors) and state["history_requested"])))
                if count:
                    Store.event(db, "capture.updated", records=count)
            return count

    def _file(self, path: Path, state: dict) -> int:
        with path.open("rb") as source:
            stat = source.fileno()
            import os
            info = os.fstat(stat)
            header = source.readline()
            if not header.endswith(b"\n"):
                return 0
            first, _ = parse_record(header)
            meta = first.get("payload", {}) if first.get("type") == "session_meta" else {}
            if not isinstance(meta, dict):
                meta = {}
            session, cwd = meta.get("id"), meta.get("cwd")
            session = session if isinstance(session, str) else None
            cwd = cwd if isinstance(cwd, str) else None
            if (cwd and Path(cwd).expanduser().resolve() == self.config.root) or (
                session and self.store.rows("SELECT id FROM generated_sessions WHERE id=?", (session,))
            ):
                return 0
            project = None
            if cwd:
                try:
                    project = self.store.project(cwd)
                except (ValueError, OSError):
                    # Removed projects keep a stable path identity for their archived transcripts.
                    project = digest(str(Path(cwd).expanduser().resolve()))[:24]
                    with self.store.transaction() as db:
                        db.execute("INSERT OR IGNORE INTO projects VALUES(?,?,?)", (project, cwd, Path(cwd).name))
            identity = session or f"{info.st_dev}:{info.st_ino}"
            rows = self.store.rows("SELECT * FROM trace_sources WHERE identity=? ORDER BY generation DESC LIMIT 1", (identity,))
            old = rows[0] if rows else None
            if old and str(path) != old["path"] and Path(old["path"]).exists() and info.st_size < old["position"]:
                return 0
            if old:
                source.seek(0)
                prefix = source.read(old["prefix_length"])
                changed = info.st_size < old["position"] or hashlib.sha256(prefix).hexdigest() != old["fingerprint"]
            else:
                changed = False
            if not old or changed:
                source.seek(0)
                prefix = source.read(min(info.st_size, 4096))
                # Existing history is indexed for position and turn identity, then imported only on request.
                historical = not changed and info.st_mtime < state["enabled"] and not state["history_requested"]
                position, turn = 0, None
                if historical:
                    source.seek(0)
                    while True:
                        line = source.readline()
                        if not line.endswith(b"\n"):
                            break
                        position = source.tell()
                        record, _ = parse_record(line)
                        turn, _ = self._turn(record, turn)
                with self.store.transaction() as db:
                    cursor = db.execute("INSERT INTO trace_sources(identity,path,session,project,cwd,position,start_position,"
                        "fingerprint,prefix_length,active_turn,generation) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (identity, str(path), session, project, cwd, position, position,
                         hashlib.sha256(prefix).hexdigest(), len(prefix), turn, old["generation"] + 1 if old else 0))
                    old = dict(db.execute("SELECT * FROM trace_sources WHERE id=?", (cursor.lastrowid,)).fetchone())
            with self.store.transaction() as db:
                db.execute("UPDATE trace_sources SET path=? WHERE id=?", (str(path), old["id"]))
            count = 0
            if state["history_requested"] and old["start_position"]:
                count += self._read(source, old, 0, None, old["start_position"], history=True)
            count += self._read(source, old, old["position"], old["active_turn"], info.st_size)
            return count

    @staticmethod
    def _turn(record: dict, active: str | None) -> tuple[str | None, bool]:
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            return active, False
        key = payload.get("turn_id")
        if isinstance(key, str) and key:
            active = key
        return active, record.get("type") == "event_msg" and payload.get("type") in {"task_complete", "turn_aborted"}

    def _read(self, stream, source: dict, position: int, active: str | None, end: int, history=False) -> int:
        stream.seek(position)
        total = 0
        while position < end:
            batch = []
            size = 0
            while stream.tell() < end and size < 1024 * 1024:
                offset = stream.tell()
                line = stream.readline()
                if not line.endswith(b"\n") or stream.tell() > end:
                    stream.seek(offset)
                    break
                record, error = parse_record(line)
                active, ended = self._turn(record, active)
                batch.append((offset, line, record, error, active, ended))
                size += len(line)
            if not batch:
                break
            position = stream.tell()
            with self.store.transaction() as db:
                for offset, line, record, error, turn, ended in batch:
                    # A source segment also groups events which cannot be assigned a Codex turn.
                    turn_key = turn or "unassigned"
                    db.execute("INSERT INTO trace_turns(source,turn_key,project,session,ended) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(source,turn_key) DO UPDATE SET ended=max(ended,excluded.ended)",
                               (source["id"], turn_key, source["project"], source["session"], int(ended)))
                    turn_id = db.execute("SELECT id FROM trace_turns WHERE source=? AND turn_key=?", (source["id"], turn_key)).fetchone()[0]
                    total += db.execute("INSERT OR IGNORE INTO trace_records(source,offset,original,project,session,turn_id,event_type,parse_error,created) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (source["id"], offset, line, source["project"], source["session"], turn_id,
                        str(record.get("type", "unknown")), error, time.time())).rowcount
                if not history:
                    db.execute("UPDATE trace_sources SET position=?,active_turn=? WHERE id=?", (position, active, source["id"]))
        if history and position >= end:
            with self.store.transaction() as db:
                db.execute("UPDATE trace_sources SET start_position=0 WHERE id=?", (source["id"],))
        return total

    def run(self, once=False):
        try:
            with file_lock(self.config.root / "locks" / "collector-process.lock", blocking=False):
                while True:
                    Collector(Config.load(self.config.root)).scan()
                    if once:
                        return
                    time.sleep(self.config.poll_seconds)
        except BlockingIOError:
            return
