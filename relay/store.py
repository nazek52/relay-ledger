import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL,
                    digest TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','running','delivered','dead')),
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
                    lease TEXT, lease_until REAL, created REAL NOT NULL,
                    last_status INTEGER, last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS due_jobs ON deliveries(state, due);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, delivery_id TEXT NOT NULL,
                    number INTEGER NOT NULL, started REAL NOT NULL, finished REAL,
                    status INTEGER, error TEXT, lease TEXT UNIQUE NOT NULL,
                    FOREIGN KEY(delivery_id) REFERENCES deliveries(id)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def enqueue(self, key, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM deliveries WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise Conflict("Idempotency key already belongs to another payload")
                db.commit()
                return dict(old), False
            ident = uuid.uuid4().hex
            db.execute(
                "INSERT INTO deliveries(id,key,digest,payload,state,due,created) VALUES(?,?,?,?,'pending',?,?)",
                (ident, key, digest, body, now, now),
            )
            db.commit()
        return self.get(ident), True

    def get(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (ident,)).fetchone()
            return dict(row) if row else None

    def list(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM deliveries ORDER BY created DESC,id LIMIT 100")
            ]

    def history(self, ident):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT number,started,finished,status,error FROM attempts WHERE delivery_id=? ORDER BY number",
                    (ident,),
                )
            ]

    def claim(self, *, lease_seconds=30, max_attempts=5):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A crash is an attempt too: repeated crashes cannot retry forever.
            db.execute(
                "UPDATE attempts SET finished=?,error='lease expired' WHERE finished IS NULL AND lease IN (SELECT lease FROM deliveries WHERE state='running' AND lease_until<=?)",
                (now, now),
            )
            db.execute(
                "UPDATE deliveries SET state=CASE WHEN attempts>=? THEN 'dead' ELSE 'pending' END,last_error='lease expired',lease=NULL,lease_until=NULL,due=? WHERE state='running' AND lease_until<=?",
                (max_attempts, now, now),
            )
            row = db.execute(
                "SELECT * FROM deliveries WHERE state='pending' AND due<=? ORDER BY due,created,id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                db.commit()
                return None
            lease = uuid.uuid4().hex
            db.execute(
                "UPDATE deliveries SET state='running',attempts=attempts+1,lease=?,lease_until=? WHERE id=?",
                (lease, now + lease_seconds, row["id"]),
            )
            db.execute(
                "INSERT INTO attempts(delivery_id,number,started,lease) VALUES(?,?,?,?)",
                (row["id"], row["attempts"] + 1, now, lease),
            )
            claimed = dict(
                db.execute("SELECT * FROM deliveries WHERE id=?", (row["id"],)).fetchone()
            )
            db.commit()
            return claimed

    def finish(self, job, status, error=None, *, max_attempts=5, jitter=0.0):
        now = self.clock()
        success = status is not None and 200 <= status < 300
        retryable = status is None or status in (408, 429) or status >= 500
        state = (
            "delivered"
            if success
            else ("pending" if retryable and job["attempts"] < max_attempts else "dead")
        )
        delay = min(300, 2 ** job["attempts"]) + jitter
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE deliveries SET state=?,due=?,last_status=?,last_error=?,lease=NULL,lease_until=NULL WHERE id=? AND state='running' AND lease=?",
                (state, now + delay, status, error, job["id"], job["lease"]),
            )
            if result.rowcount:
                db.execute(
                    "UPDATE attempts SET finished=?,status=?,error=? WHERE lease=?",
                    (now, status, error, job["lease"]),
                )
            db.commit()
            return bool(result.rowcount)

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT state,count(*) FROM deliveries GROUP BY state"))
