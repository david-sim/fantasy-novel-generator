"""
Shared Redis connection + RQ queue accessors for NovelEngine's async
generation jobs (Phase 2 of the architecture migration).

Both the FastAPI process (enqueueing jobs from ``app.api.novels``) and the
worker process (``app.worker``) import from here so they always agree on the
Redis connection and queue name.

Works against any Redis-compatible endpoint configured via ``REDIS_URL`` —
including Upstash's Redis-compatible TCP endpoint — without any
Upstash-specific SDK. Connections are lazy (redis-py does not open a socket
until the first command), so importing this module never requires a live
Redis server, which keeps it safe to import in CI/tests.
"""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.core.config import settings

# Single queue for all novel-generation jobs. A dedicated name keeps this
# queue separate from any other RQ queues that might share the same Redis
# instance in the future.
GENERATION_QUEUE_NAME: str = "novel-generation"


@lru_cache(maxsize=1)
def get_redis_connection() -> Redis:
    """Return a cached Redis connection built from ``settings.redis_url``."""
    return Redis.from_url(settings.redis_url)


def get_queue(name: str = GENERATION_QUEUE_NAME) -> Queue:
    """Return an RQ ``Queue`` bound to the shared Redis connection."""
    return Queue(name, connection=get_redis_connection())
