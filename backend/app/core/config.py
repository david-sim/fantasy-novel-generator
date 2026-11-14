"""
Environment-driven application settings.

All values are read once at import time from environment variables (loaded
from ``.env`` via python-dotenv during local development; real deployments
set these directly in the hosting platform's environment).
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Lightweight settings container (plain env-var reads, no pydantic needed)."""

    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    supabase_jwt_secret: str = os.getenv("SUPABASE_JWT_SECRET", "")

    # Database (same DSN used by app.db.models)
    database_url: str = os.getenv("DATABASE_URL", "")

    # Async job queue (Phase 2) — any Redis-compatible endpoint, including
    # Upstash's Redis-compatible TCP endpoint.
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # LLM
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # CORS — comma-separated list of allowed frontend origins
    cors_origins: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ]


settings = Settings()
