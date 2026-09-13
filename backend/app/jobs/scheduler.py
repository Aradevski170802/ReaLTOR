"""Daily refresh scheduling (APScheduler cron inside the worker process)."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.jobs.queue import enqueue
from app.models import Project

log = logging.getLogger(__name__)


def enqueue_daily_refresh(trigger: str = "scheduled") -> list[int]:
    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    job_ids = []
    with session_scope() as session:
        for project in session.scalars(select(Project).where(Project.archived.is_(False))):
            job = enqueue(session, "refresh_tax_status", {"trigger": trigger}, project_id=project.id,
                          idempotency_key=f"daily_refresh:{project.id}:{today}", created_by="scheduler", max_attempts=3)
            job_ids.append(job.id)
    log.info("Daily tax-status refresh enqueued for %d project(s)", len(job_ids))
    return job_ids


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(
        enqueue_daily_refresh,
        CronTrigger(hour=settings.daily_refresh_hour, minute=settings.daily_refresh_minute, timezone=ZoneInfo(settings.timezone)),
        id="daily_tax_status_refresh",
        replace_existing=True,
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    return scheduler
