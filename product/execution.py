"""Durable run events, lease heartbeats and idempotent action records."""
from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
import threading
from uuid import UUID

from psycopg.types.json import Jsonb

from product.db import connect


RETRYABLE_ERRORS = {"rate_limit", "timeout", "unavailable"}


def append_event(db, run_id: UUID, event_type: str, payload: dict | None = None):
    """Append one per-run ordered event inside the caller's transaction."""
    db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(run_id),))
    seq = db.execute(
        "SELECT COALESCE(MAX(seq),0)+1 AS seq FROM run_events WHERE run_id=%s", (run_id,)
    ).fetchone()["seq"]
    return db.execute(
        "INSERT INTO run_events(run_id,seq,event_type,payload) VALUES(%s,%s,%s,%s) "
        "RETURNING run_id,seq,event_type,payload,created_at",
        (run_id, seq, event_type, Jsonb(payload or {})),
    ).fetchone()


def action_fingerprint(action: dict) -> str:
    body = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def record_action(db, run_id, action: dict, attempt: int, status: str, result_summary=None, error_code=None):
    action_id = str(action["action_id"])
    fingerprint = action_fingerprint(action)
    return db.execute(
        "INSERT INTO action_records(run_id,action_id,attempt,action_type,status,request_fingerprint,result_summary,error_code) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(run_id,action_id,attempt) DO UPDATE SET "
        "status=excluded.status,result_summary=excluded.result_summary,error_code=excluded.error_code,updated_at=now() "
        "WHERE action_records.request_fingerprint=excluded.request_fingerprint RETURNING *",
        (run_id, action_id, attempt, action["action_type"], status, fingerprint, result_summary, error_code),
    ).fetchone()


class LeaseHeartbeat(AbstractContextManager):
    """Extend only the lease owned by this worker attempt."""
    def __init__(self, settings, job_id, token, every_seconds=15, lease_seconds=180):
        self.settings, self.job_id, self.token = settings, job_id, token
        self.every_seconds, self.lease_seconds = every_seconds, lease_seconds
        self.stop = threading.Event()
        self.thread = None

    def beat(self):
        with connect(self.settings) as db:
            return bool(db.execute(
                "UPDATE jobs SET heartbeat_at=now(),lease_until=now()+(%s * interval '1 second') "
                "WHERE id=%s AND lease_token=%s AND status='running' RETURNING id",
                (self.lease_seconds, self.job_id, self.token),
            ).fetchone())

    def _loop(self):
        while not self.stop.wait(self.every_seconds):
            if not self.beat():
                return

    def __enter__(self):
        self.beat()
        self.thread = threading.Thread(target=self._loop, name="product-lease-heartbeat", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
