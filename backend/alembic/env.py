from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.db import Base

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().resolved_database_url


def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=url.startswith("sqlite"),
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    if url.startswith("sqlite"):
        get_settings().ensure_dirs()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=url.startswith("sqlite"),
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
