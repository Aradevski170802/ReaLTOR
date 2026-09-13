import logging
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.audit import record_audit, verify_chain
from app.db import utcnow
from app.jobs.queue import cancel, claim_next, complete, enqueue, fail, recover_stale
from app.jobs.scheduler import enqueue_daily_refresh
from app.models import AuditLog, Job, Project
from app.security.secrets import RedactingFilter, delete_secret, get_secret, redact_text, secret_status, set_secret


def test_secrets_are_encrypted_and_redacted(db):
    set_secret(db, "provider.attom.api_key", "attom-live-key-987654", "admin")
    db.commit()
    stored = db.execute(text("select ciphertext, hint from encrypted_secrets")).one()
    assert "attom-live-key-987654" not in stored.ciphertext and stored.hint == "…7654"
    assert get_secret(db, "provider.attom.api_key") == "attom-live-key-987654"
    assert secret_status(db, "provider.attom.api_key")["configured"]
    assert "attom-live-key-987654" not in redact_text("calling with key attom-live-key-987654")
    assert delete_secret(db, "provider.attom.api_key") and get_secret(db, "provider.attom.api_key") is None


def test_log_redaction_filter(caplog):
    logger = logging.getLogger("usi.test.redaction")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="usi.test.redaction"):
        logger.info("GET https://api.test/x?apikey=%s&y=1", "plain-secret-value")
    assert "plain-secret-value" not in caplog.text and "***REDACTED***" in caplog.text


def test_audit_chain_detects_tampering(db):
    for i in range(3):
        record_audit(db, "tester", f"action.{i}", "thing", i, details={"password": "hunter2secret", "n": i})
    db.commit()
    assert verify_chain(db) == (True, None)
    entry = db.query(AuditLog).first()
    assert "hunter2secret" not in str(entry.details)
    entry.details = {"n": 99}
    with pytest.raises(PermissionError):
        db.flush()
    db.rollback()
    db.execute(text("update audit_log set actor='mallory' where id = (select min(id) from audit_log)"))
    db.commit()
    ok, bad = verify_chain(db)
    assert not ok and bad is not None


def test_queue_idempotency_claim_retry_and_failure(db):
    from app.db import session_factory

    job = enqueue(db, "noop", {"a": 1}, idempotency_key="k1")
    assert enqueue(db, "noop", {"a": 2}, idempotency_key="k1").id == job.id
    db.commit()
    claimed = claim_next(session_factory(), "w1")
    assert claimed == job.id and claim_next(session_factory(), "w2") is None
    db.expire_all()
    job = db.get(Job, job.id)
    assert job.status == "running" and job.attempts == 1
    fail(db, job, "temporary", retryable=True)
    assert job.status == "queued" and job.run_after > utcnow()
    job.attempts = job.max_attempts
    fail(db, job, "permanent", retryable=True)
    assert job.status == "failed"
    db.commit()
    assert enqueue(db, "noop", {}, idempotency_key="k1").id != job.id  # finished jobs don't block new ones


def test_stale_recovery_and_cancel(db):
    parent = enqueue(db, "enrich_batch", {})
    child = enqueue(db, "enrich_property", {}, parent_id=parent.id)
    running = enqueue(db, "noop", {})
    running.status, running.heartbeat_at, running.attempts = "running", utcnow() - timedelta(hours=1), 1
    db.commit()
    assert recover_stale(db) == 1 and running.status == "queued"
    assert cancel(db, parent) == 2 and db.get(Job, child.id).status == "cancelled"
    complete(db, running, {"ok": True})
    assert running.status == "succeeded"


def test_daily_refresh_enqueue_is_idempotent_per_day(db):
    db.add_all([Project(name="A", county="montco"), Project(name="B", county="delco"), Project(name="C", county="delco", archived=True)])
    db.commit()
    first = enqueue_daily_refresh()
    second = enqueue_daily_refresh()
    assert len(first) == 2 and first == second
