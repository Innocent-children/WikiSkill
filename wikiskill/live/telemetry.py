from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid

from .store import Store


class WorkerHeartbeat:
    """Record process liveness independently of scheduling and model response waits."""

    interval = 5.0

    def __init__(self, store: Store):
        self.store = store
        self.instance = uuid.uuid4().hex
        self.job_id: str | None = None
        self.last_activity: float | None = None
        self.error: str | None = None
        self.stopped = threading.Event()
        self.mutex = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="wikiskill-heartbeat", daemon=True)

    def __enter__(self):
        now = time.time()
        with self.store.transaction() as db:
            db.execute("INSERT INTO worker_runtime VALUES(1,?,?,?,?, 'online',NULL,NULL,NULL) "
                       "ON CONFLICT(id) DO UPDATE SET instance=excluded.instance,pid=excluded.pid,"
                       "started=excluded.started,heartbeat=excluded.heartbeat,state='online',"
                       "job_id=NULL,last_activity=NULL,error=NULL",
                       (self.instance, os.getpid(), now, now))
            self.store.event(db, "worker.started", pid=os.getpid())
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stopped.set()
        self.thread.join()
        self.error = str(exc) if exc else None
        self._write("stopped")

    def set_job(self, job_id: str | None):
        with self.mutex:
            self.job_id = job_id
            self.last_activity = time.time()
        self._write()

    def touch(self):
        with self.mutex:
            self.last_activity = time.time()

    def _write(self, state: str = "online"):
        with self.mutex:
            values = (time.time(), state, self.job_id, self.last_activity, self.error, self.instance)
        try:
            with self.store.connect(timeout=1) as db:
                db.execute("UPDATE worker_runtime SET heartbeat=?,state=?,job_id=?,last_activity=?,error=? "
                           "WHERE id=1 AND instance=?", values)
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).exception("Unable to record worker heartbeat")

    def _run(self):
        while not self.stopped.wait(self.interval):
            self._write()
