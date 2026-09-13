from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite stores naive values)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def init_engine(url: str | None = None) -> Engine:
    global _engine, _session_factory
    settings = get_settings()
    url = url or settings.resolved_database_url
    if url.startswith("sqlite"):
        settings.ensure_dirs()
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        _configure_sqlite(engine)
    else:
        engine = create_engine(url, pool_pre_ping=True)
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
