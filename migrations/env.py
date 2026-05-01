"""Alembic environment – felvi_games."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make the src/ package importable without an editable install
# ---------------------------------------------------------------------------
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Import ORM metadata so Alembic can detect schema changes automatically
from felvi_games.db import Base, get_engine  # noqa: E402

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """Suppress known SQLite phantom diffs from autogenerate.

    1. felhasznalok.id nullable – SQLite PRAGMA reports notnull=0 for
       INTEGER PRIMARY KEY even though it is implicitly NOT NULL.
    2. menetek unnamed FK – a legacy constraint with no name that cannot
       be dropped/created by Alembic; was logically removed in c491db828a78.
    """
    if type_ == "column" and name == "id" and getattr(obj, "table", None) is not None:
        if obj.table.name == "felhasznalok":
            return False
    if type_ == "foreign_key_constraint" and obj.parent.name == "menetek":
        return False
    return True


def _get_url() -> str:
    """Return the DB URL, preferring the app's own config over alembic.ini."""
    url = config.get_main_option("sqlalchemy.url", "")
    if url:
        return url
    # Fall back to the same DB the app uses (respects FELVI_DB env var)
    from felvi_games.config import get_db_path
    return f"sqlite:///{get_db_path()}"


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    engine = get_engine(None)  # uses FELVI_DB / default path
    url_override = config.get_main_option("sqlalchemy.url", "")
    if url_override:
        from sqlalchemy import create_engine
        engine = create_engine(url_override, poolclass=pool.NullPool)

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # SQLite batch-alter support
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
