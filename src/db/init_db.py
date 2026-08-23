"""
Database initialisation script for NovelEngine.

Usage (from the project root):
    python -m src.db.init_db

The DATABASE_URL environment variable controls the target database.
If unset, defaults to: sqlite:///./novelengine.db
"""

import logging

from sqlalchemy import inspect, text

from src.db.models import Base, DATABASE_URL, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def init_db() -> None:
    """Create all tables defined in Base.metadata if they do not already exist."""
    logger.info("Initialising database at: %s", DATABASE_URL)

    # Create all tables (safe to run repeatedly — uses CREATE TABLE IF NOT EXISTS)
    Base.metadata.create_all(bind=engine)

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
