"""Alembic migration environment.

Uses the sync SQLAlchemy URL from `Settings.db_url_sync` so Alembic can run
its own connection without async overhead. The models themselves are imported
from `snapd_invest.models` (and `snapd_invest.persistence.Base` for metadata).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from snapd_invest.config import get_settings
from snapd_invest.persistence import Base
from snapd_invest import models  # noqa: F401 - registers models with Base.metadata

config = context.config
settings = get_settings()
# Ensure the SQLite directory exists before Alembic tries to open the file.
# `Settings.db_path.parent.mkdir(...)` would do it via `make_engine`, but
# Alembic uses `engine_from_config` directly and never calls our factory.
settings.db_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.db_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout, no DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,  # SQLite-friendly schema changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
