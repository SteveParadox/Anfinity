from logging.config import fileConfig
import os
import sys

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add the parent directory to sys.path so we can import app module
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import the Base model from app
from app.database.models import Base

# Alembic Config object
config = context.config

# 🔥 FORCE Alembic to use DATABASE_URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Logging setup
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ✅ FIX: enable metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Pass SSL settings (if provided via env) to the SQLAlchemy engine so
    # Alembic uses the same libpq/psycopg2 SSL configuration as the app.
    connect_args = {}
    pgsslroot = os.getenv("PGSSLROOTCERT")
    pgsslmode = os.getenv("PGSSLMODE")
    if pgsslroot:
        connect_args["sslrootcert"] = pgsslroot
    if pgsslmode:
        connect_args["sslmode"] = pgsslmode

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()