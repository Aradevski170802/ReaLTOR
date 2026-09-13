"""Database-backed job queue: idempotent enqueue, atomic claim, retries with exponential backoff, stale recovery.

Works on SQLite and PostgreSQL (claim uses a conditional UPDATE and checks the affected row count).
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db import utcnow
from app.enums import JobStatus
from app.models import Job

ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


class RetryableJobError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def enqueue(session: Session, kind: str, payload: dict[str, Any] | None = None, *, project_id: int | None = None,
            property_id: int | None = None, idempotency_key: str | None = None, parent_id: int | None = None,
            max_attempts: int = 4, delay_seconds: float = 0, created_by: str | None = None) -> Job:
    if idempotency_key:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key, Job.status.in_(ACTIVE)))
        if existing is not None:
            return existing
    job = Job(kind=kind, payload=payload or {}, project_id=project_id, property_id=property_id, idempotency_key=idempotency_key,
              parent_id=parent_id, max_attempts=max_attempts, run_after=utcnow() + timedelta(seconds=delay_seconds),
              created_by=created_by, status=JobStatus.QUEUED)
    session.add(job)
    session.flush()
    return job


def claim_next(factory: sessionmaker[Session], worker_id: str, kinds: list[str] | None = None) -> int | None:
    with factory() as session:
        query = select(Job.id).where(Job.status == JobStatus.QUEUED, Job.run_after <= utcnow()).order_by(Job.run_after, Job.id).limit(20)
        if kinds:
            query = query.where(Job.kind.in_(kinds))
        for job_id in session.scalars(query).all():
            now = utcnow()
            result = session.execute(
                update(Job).where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING, locked_by=worker_id, locked_at=now, heartbeat_at=now,
                        started_at=now, attempts=Job.attempts + 1)
            )
            if result.rowcount == 1:
                session.commit()
                return job_id
            session.rollback()
    return None


def heartbeat(session: Session, job_id: int) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=utcnow()))


def set_progress(session: Session, job_id: int, done: int, total: int) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(progress_done=done, progress_total=total, heartbeat_at=utcnow()))


def complete(session: Session, job: Job, result: dict[str, Any] | None = None) -> None:
    job.status = JobStatus.SUCCEEDED
    job.result = result or {}
    job.error = None
    job.finished_at = utcnow()
    job.locked_by = None


def fail(session: Session, job: Job, error: str, retryable: bool, retry_after: float | None = None) -> None:
    job.error = error[:4000]
    job.locked_by = None
    if retryable and job.attempts < job.max_attempts:
        delay = retry_after if retry_after else min(3600.0, 15 * (2 ** (job.attempts - 1)))
        delay += random.uniform(0, delay * 0.2)
        job.status = JobStatus.QUEUED
        job.run_after = utcnow() + timedelta(seconds=delay)
    else:
        job.status = JobStatus.FAILED
        job.finished_at = utcnow()


def cancel(session: Session, job: Job) -> int:
    count = 0
    targets = [job] + list(session.scalars(select(Job).where(Job.parent_id == job.id, Job.status == JobStatus.QUEUED)))
    for target in targets:
        if target.status in ACTIVE:
            target.status = JobStatus.CANCELLED
            target.finished_at = utcnow()
            count += 1
    return count


def recover_stale(session: Session, stale_after: timedelta = timedelta(minutes=15)) -> int:
    cutoff = utcnow() - stale_after
    stale = session.scalars(select(Job).where(Job.status == JobStatus.RUNNING, Job.heartbeat_at < cutoff)).all()
    for job in stale:
        fail(session, job, "Worker heartbeat lost; job re-queued", retryable=True)
    return len(stale)


def children_progress(session: Session, parent_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in session.scalars(select(Job.status).where(Job.parent_id == parent_id)):
        counts[status] = counts.get(status, 0) + 1
    return counts
