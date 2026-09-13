"""Background worker: `python -m app.jobs.worker` (add --no-scheduler to disable the daily refresh cron)."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
from datetime import timedelta

import httpx
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import init_engine, session_factory
from app.jobs.handlers import HANDLERS
from app.jobs.queue import RetryableJobError, claim_next, complete, fail, heartbeat, recover_stale
from app.jobs.scheduler import build_scheduler
from app.models import Job
from app.security.secrets import install_log_redaction

log = logging.getLogger("app.worker")


def _transient(exc: Exception) -> bool:
    return isinstance(exc, OperationalError | httpx.HTTPError | TimeoutError | ConnectionError)


class Worker:
    def __init__(self, worker_id: str | None = None, poll_seconds: float | None = None, kinds: list[str] | None = None,
                 enable_scheduler: bool = True):
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        self.poll_seconds = poll_seconds if poll_seconds is not None else get_settings().worker_poll_seconds
        self.kinds = kinds
        self.enable_scheduler = enable_scheduler
        self._stop = threading.Event()
        self._last_recovery = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> bool:
        job_id = claim_next(session_factory(), self.worker_id, self.kinds)
        if job_id is None:
            return False
        self._execute(job_id)
        return True

    def run_until_empty(self, max_jobs: int = 10_000) -> int:
        count = 0
        while count < max_jobs and self.run_once():
            count += 1
        return count

    def _execute(self, job_id: int) -> None:
        factory = session_factory()
        beat_stop = threading.Event()

        def beat() -> None:
            while not beat_stop.wait(30):
                try:
                    with factory() as s:
                        heartbeat(s, job_id)
                        s.commit()
                except Exception:  # noqa: BLE001
                    log.debug("heartbeat failed", exc_info=True)

        threading.Thread(target=beat, daemon=True).start()
        with factory() as session:
            job = session.get(Job, job_id)
            fn = HANDLERS.get(job.kind)
            started = time.monotonic()
            try:
                if fn is None:
                    raise ValueError(f"No handler for job kind {job.kind!r}")
                result = fn(session, job)
                job = session.get(Job, job_id)
                complete(session, job, result)
                session.commit()
                log.info("job %s %s succeeded in %.1fs", job.id, job.kind, time.monotonic() - started)
            except RetryableJobError as exc:
                session.rollback()
                job = session.get(Job, job_id)
                fail(session, job, str(exc), retryable=True, retry_after=exc.retry_after)
                session.commit()
                log.warning("job %s %s will retry: %s", job.id, job.kind, exc)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                job = session.get(Job, job_id)
                fail(session, job, f"{type(exc).__name__}: {exc}", retryable=_transient(exc))
                session.commit()
                log.exception("job %s %s failed", job.id, job.kind)
            finally:
                beat_stop.set()
            if job.parent_id:
                self._update_parent(session, job.parent_id)

    @staticmethod
    def _update_parent(session, parent_id: int) -> None:
        from sqlalchemy import func, select

        done = session.scalar(select(func.count()).select_from(Job).where(
            Job.parent_id == parent_id, Job.status.in_(("succeeded", "failed", "cancelled"))))
        total = session.scalar(select(func.count()).select_from(Job).where(Job.parent_id == parent_id))
        parent = session.get(Job, parent_id)
        if parent is not None:
            parent.progress_done = done or 0
            parent.progress_total = total or 0
            session.commit()

    def run_forever(self) -> None:
        scheduler = build_scheduler() if self.enable_scheduler else None
        if scheduler:
            scheduler.start()
            log.info("Daily refresh scheduled at %02d:%02d %s", get_settings().daily_refresh_hour,
                     get_settings().daily_refresh_minute, get_settings().timezone)
        log.info("Worker %s started", self.worker_id)
        try:
            while not self._stop.is_set():
                if time.monotonic() - self._last_recovery > 300:
                    with session_factory()() as s:
                        recovered = recover_stale(s, timedelta(minutes=15))
                        s.commit()
                    if recovered:
                        log.warning("Re-queued %d stale job(s)", recovered)
                    self._last_recovery = time.monotonic()
                try:
                    worked = self.run_once()
                except OperationalError:
                    log.warning("Database busy; retrying", exc_info=True)
                    worked = False
                if not worked:
                    self._stop.wait(self.poll_seconds)
        finally:
            if scheduler:
                scheduler.shutdown(wait=False)
            log.info("Worker %s stopped", self.worker_id)


def start_in_process_worker() -> Worker:
    worker = Worker(enable_scheduler=True)
    threading.Thread(target=worker.run_forever, name="usi-worker", daemon=True).start()
    return worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Upset Sale Intel background worker")
    parser.add_argument("--once", action="store_true", help="process queued jobs then exit")
    parser.add_argument("--no-scheduler", action="store_true", help="do not schedule the daily tax-status refresh")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()
    init_engine()
    worker = Worker(enable_scheduler=not args.no_scheduler and not args.once)
    if args.once:
        processed = worker.run_until_empty()
        log.info("Processed %d job(s)", processed)
        return
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    worker.run_forever()


if __name__ == "__main__":
    main()
