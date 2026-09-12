"""Short PostgreSQL transactions, with no transaction held over agent execution."""
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
from typing import BinaryIO
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from repopilot.service.logging import event
from repopilot.service.settings import Settings
from repopilot.trajectory import redact_value
from repopilot.trajectory.schema import stable_trace_id


QUEUED_QUERY = """SELECT * FROM tasks WHERE status='QUEUED'
    AND NOT(id=ANY(%s::uuid[])) ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED"""
RECOVERY_QUERY = """SELECT t.* FROM runs r JOIN tasks t ON t.id=r.task_id AND t.latest_run_id=r.id
    WHERE r.status='RUNNING' AND t.status='RUNNING' AND r.lease_expires_at < statement_timestamp()
    AND NOT(t.id=ANY(%s::uuid[])) ORDER BY r.lease_expires_at,r.id
    LIMIT 1 FOR UPDATE OF t SKIP LOCKED"""


def _safe_pool_log(record):
    # Pool internals may interpolate raw driver exceptions or connection reprs.
    # Preserve an observable warning without credentials or server error text.
    record.msg = '{"event":"database_pool_warning"}'
    record.args = ()
    record.exc_info = record.exc_text = record.stack_info = None
    return True


class AdmissionFull(Exception):
    pass


class IdempotencyConflict(ValueError):
    pass


@dataclass
class Claim:
    task: dict
    run: dict
    lock: BinaryIO
    exhausted: bool = False

    def release(self):
        # Close only; never LOCK_UN a descriptor shared with a surviving helper.
        # Never unlink lock files: removing one permits two different locked inodes.
        self.lock.close()

    @property
    def identifiers(self):
        return dict(request_id=self.task["request_id"], task_id=self.task["id"],
                    run_id=self.run["id"], trace_id=self.run["trace_id"], worker_id=self.run["worker_id"])


class Store:
    def __init__(self, settings: Settings, *, pool_size: int = 0):
        self.settings = settings
        self.draining = False
        self._pool = None
        self._connection_options = dict(row_factory=dict_row, connect_timeout=3,
                                       options="-c statement_timeout=5000 -c lock_timeout=3000")
        if pool_size:
            # Raw driver exception strings must not bypass service log redaction.
            logger = logging.getLogger("psycopg.pool")
            if _safe_pool_log not in logger.filters:
                logger.addFilter(_safe_pool_log)
            logger.setLevel(logging.WARNING)
            logger.propagate = True
            self._pool = ConnectionPool(
                settings.database_url, kwargs=self._connection_options,
                min_size=0, max_size=pool_size, open=True, timeout=3,
                max_waiting=64, num_workers=1, reconnect_timeout=5,
                check=ConnectionPool.check_connection,
            )

    def connect(self):
        # Both context managers commit/rollback before releasing the connection.
        # A standalone Store remains useful for migrations/inspection without a pool.
        if self._pool is not None:
            return self._pool.connection()
        return psycopg.connect(self.settings.database_url, **self._connection_options)

    def close(self):
        if self._pool is not None:
            self._pool.close()

    def __del__(self):
        # Short-lived run_once callers also release resources. Normal API/worker
        # lifetimes close explicitly; do not block interpreter shutdown here.
        pool = getattr(self, "_pool", None)
        if pool is not None:
            pool.close(timeout=0)

    def ready(self):
        with self.connect() as db:
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            return bool(revision and revision["version_num"] == "0003")

    def submit(self, payload: dict, key: str | None, request_id: UUID):
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest() if key is not None else None
        task_id = uuid4()
        with self.connect() as db:
            try:
                task = db.execute("SELECT * FROM admit_task(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (task_id,request_id,Jsonb(payload),
                     'scripted' if self.settings.scripted else self.settings.provider,
                     'scripted-final' if self.settings.scripted else self.settings.model,
                     key_hash,fingerprint,self.settings.max_inflight)).fetchone()
            except psycopg.Error as exc:
                if exc.sqlstate == 'RP001':
                    raise AdmissionFull() from None
                raise
            created = task['id'] == task_id
            if created:
                self.settings.repository(payload['repository'])
            elif task['fingerprint'] != fingerprint:
                raise IdempotencyConflict('idempotency key already belongs to a different request')
        event("task_created" if created else "task_deduplicated", request_id=request_id, task_id=task["id"],
              original_request_id=task["request_id"])
        return task, created

    def get(self, task_id: UUID):
        with self.connect() as db:
            # A single snapshot keeps task status and attempt list consistent.
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            task = db.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()
            if task:
                task["runs"] = db.execute("SELECT * FROM runs WHERE task_id=%s ORDER BY attempt", (task_id,)).fetchall()
            return task

    def claim(self, worker_id: str) -> Claim | None:
        directory = self.settings.artifact_root / ".locks"
        directory.mkdir(parents=True, exist_ok=True)
        lock = None
        selected = None
        try:
            with self.connect() as db:
                # Lock one candidate at a time so other workers can claim
                # independent tasks immediately, even before this transaction commits.
                skipped = []
                while True:
                    task = db.execute(RECOVERY_QUERY, (skipped,)).fetchone()
                    if task is None:
                        task = db.execute(QUEUED_QUERY, (skipped,)).fetchone()
                    if task is None:
                        break
                    skipped.append(task["id"])
                    lock = (directory / str(task["id"])).open("a+b")
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        lock.close()
                        lock = None
                        continue
                    previous = None
                    if task["latest_run_id"]:
                        previous = db.execute("SELECT * FROM runs WHERE id=%s FOR UPDATE", (task["latest_run_id"],)).fetchone()
                        # A heartbeat may have renewed between the candidate query and row lock.
                        expired = db.execute("SELECT %s < clock_timestamp() AS expired", (previous["lease_expires_at"],)).fetchone()["expired"]
                        if not expired:
                            lock.close()
                            lock = None
                            continue
                    if self.draining:
                        lock.close()
                        lock = None
                        break
                    attempt = previous["attempt"] + 1 if previous else 1
                    if attempt > self.settings.max_attempts:
                        # Reuse the final attempt only for cleanup and terminal failure;
                        # no additional agent execution is permitted.
                        run = db.execute("""UPDATE runs SET worker_id=%s,heartbeat_at=clock_timestamp(),
                            lease_expires_at=clock_timestamp() + %s * interval '1 second'
                            WHERE id=%s RETURNING *""",
                            (worker_id, self.settings.lease_seconds, previous["id"])).fetchone()
                        selected = Claim(task, run, lock, exhausted=True)
                    else:
                        if previous:
                            db.execute("""UPDATE runs SET status='ABANDONED', completed_at=clock_timestamp(),
                                stop_reason='worker_lease_expired',error_code='lease_expired' WHERE id=%s""", (previous["id"],))
                        run_id = uuid4()
                        run = db.execute("""
                            INSERT INTO runs(id,task_id,attempt,status,worker_id,trace_id,lease_expires_at)
                            VALUES (%s,%s,%s,'RUNNING',%s,%s,clock_timestamp() + %s * interval '1 second') RETURNING *
                        """, (run_id, task["id"], attempt, worker_id, stable_trace_id(str(run_id)), self.settings.lease_seconds)).fetchone()
                        db.execute("""UPDATE tasks SET status='RUNNING',latest_run_id=%s,
                                      updated_at=clock_timestamp() WHERE id=%s""", (run_id, task["id"]))
                        selected = Claim(task, run, lock)
                    break
                self._worker_seen(db, worker_id, 'RUNNING' if selected else 'IDLE', claimed=selected is not None)
            if selected:
                event("attempts_exhausted" if selected.exhausted else "run_claimed", **selected.identifiers)
            return selected
        except BaseException:
            if lock is not None:
                lock.close()
            raise

    def heartbeat(self, claim: Claim) -> bool:
        with self.connect() as db:
            updated = db.execute("""
                UPDATE runs SET heartbeat_at=clock_timestamp(),
                    lease_expires_at=clock_timestamp() + %s * interval '1 second'
                WHERE id=%s AND worker_id=%s AND status='RUNNING' AND lease_expires_at > clock_timestamp()
                RETURNING id
            """, (self.settings.lease_seconds, claim.run["id"], claim.run["worker_id"])).fetchone()
            if updated:
                self._worker_seen(db, claim.run['worker_id'], 'RUNNING')
        return updated is not None

    def _worker_seen(self, db, worker_id, state, *, claimed=False):
        if self.draining and state != 'STOPPED':
            state = 'DRAINING'
        db.execute("""INSERT INTO service_workers(worker_id,state,expires_at,claim_count,claim_seconds)
            VALUES (%s,%s,clock_timestamp()+%s*interval '1 second',%s,
                CASE WHEN %s THEN extract(epoch FROM(clock_timestamp()-transaction_timestamp())) ELSE 0 END)
            ON CONFLICT(worker_id) DO UPDATE SET state=EXCLUDED.state,expires_at=EXCLUDED.expires_at,
                claim_count=service_workers.claim_count+EXCLUDED.claim_count,
                claim_seconds=service_workers.claim_seconds+EXCLUDED.claim_seconds""",
            (worker_id,state,self.settings.lease_seconds,int(claimed),claimed))

    def stopped(self, worker_id):
        with self.connect() as db:
            self._worker_seen(db,worker_id,'STOPPED')

    def finish(self, claim: Claim, *, result: dict | None = None, error_code: str | None = None) -> bool:
        result = redact_value(result) if result is not None else None
        status = "SUCCEEDED" if result and result.get("success") and not error_code else "FAILED"
        reason = error_code or (result or {}).get("stop_reason", "execution_failed")
        with self.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (claim.task["id"],)).fetchone()
            if task["status"] != "RUNNING" or task["latest_run_id"] != claim.run["id"]:
                return False
            run = db.execute("""
                UPDATE runs SET status=%s,completed_at=clock_timestamp(),stop_reason=%s,error_code=%s,
                    result=%s,artifact_path=%s
                WHERE id=%s AND worker_id=%s AND status='RUNNING' AND lease_expires_at > clock_timestamp()
                RETURNING id
            """, (status, reason, error_code, Jsonb(result),
                  str(claim.run["id"]) if result else None, claim.run["id"], claim.run["worker_id"])).fetchone()
            if not run:
                return False
            db.execute("""UPDATE tasks SET status=%s,updated_at=clock_timestamp(),completed_at=clock_timestamp()
                          WHERE id=%s""", (status, claim.task["id"]))
            self._worker_seen(db, claim.run['worker_id'], 'IDLE')
        event("run_finished", **claim.identifiers, status=status, stop_reason=reason)
        return True
