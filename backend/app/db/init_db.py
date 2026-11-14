"""
Database initialisation script for NovelEngine.

Usage (from backend/):
    python -m app.db.init_db

The DATABASE_URL environment variable controls the target database.
Must point at a Postgres instance with the ability to create extensions
(Supabase projects grant this by default). Defaults to a local Postgres DSN.
"""

import logging

from sqlalchemy import inspect, text

from app.db.models import Base, DATABASE_URL, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_pgvector_extension() -> None:
    """Enable the pgvector extension, required by the LoreEmbedding table."""
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    logger.info("Ensured 'vector' Postgres extension is enabled.")


def _run_column_migrations() -> None:
    """
    Add columns introduced after a table's initial creation.

    ``Base.metadata.create_all`` only creates missing *tables* — it never
    alters existing ones — so any new column added to a model must be
    back-filled here with an idempotent ``ALTER TABLE ... ADD COLUMN``.
    Safe to run on every startup: each migration checks column existence
    first.
    """
    inspector = inspect(engine)
    if "chapter" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("chapter")}
    if "summary" not in existing_columns:
        logger.info("Migrating 'chapter' table: adding 'summary' column.")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE chapter ADD COLUMN summary TEXT"))

    if "novel" in inspector.get_table_names():
        novel_columns = {col["name"] for col in inspector.get_columns("novel")}
        if "owner_id" not in novel_columns:
            logger.info("Migrating 'novel' table: adding 'owner_id' column.")
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE novel ADD COLUMN owner_id VARCHAR(255) NOT NULL DEFAULT ''")
                )


def init_db() -> None:
    """Create all tables defined in Base.metadata if they do not already exist."""
    logger.info("Initialising database at: %s", DATABASE_URL)

    _ensure_pgvector_extension()

    # Create all tables (safe to run repeatedly — uses CREATE TABLE IF NOT EXISTS)
    Base.metadata.create_all(bind=engine)

    # Back-fill columns added to models after their table already existed.
    _run_column_migrations()

    # Log which tables are now present
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    logger.info("Tables available: %s", tables)

    # Verify connectivity with a lightweight query
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    logger.info("Database initialisation complete.")


if __name__ == "__main__":
    init_db()
