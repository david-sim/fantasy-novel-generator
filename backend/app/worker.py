"""
RQ worker entrypoint for NovelEngine's async generation jobs (Phase 2).

Runs as a separate, always-on process alongside the FastAPI API in
production (e.g. a second Render/Fly.io service built from the same image):

    cd backend && python -m app.worker

Equivalently, the RQ CLI can be pointed at the same queue:

    cd backend && rq worker novel-generation --url $REDIS_URL

Phase 1 -> Phase 2: ``POST /api/novels/{id}/generate`` no longer calls
``execute_graph()`` synchronously. Instead it enqueues
``run_generation_job`` here and returns a job id immediately. The call to
``execute_graph`` itself — and the whole LangGraph pipeline in
``app.agents`` — is unchanged; only *how* it's invoked and how its progress
is tracked have changed.

This module must stay import-safe (no network calls at import time) so it
can double as a CI smoke test via ``python -c "import app.worker"`` — Redis
connections are lazy and are not opened until a job actually runs.
"""

from __future__ import annotations

import asyncio
import logging

from rq import Worker, get_current_job
from sqlalchemy.orm import Session

from app.agents.graph import execute_graph
from app.core.queue import get_queue, get_redis_connection
from app.db.models import Chapter, GenerationJob, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _update_job(job_id: str, **fields: object) -> None:
    """Patch a ``GenerationJob`` row in place. Safe to call from the worker process."""
    with Session(engine) as session:
        with session.begin():
            job = session.get(GenerationJob, job_id)
            if job is None:
                logger.warning("[worker] GenerationJob %s not found while updating", job_id)
                return
            for key, value in fields.items():
                setattr(job, key, value)


def run_generation_job(novel_id: int, initial_prompt: str = "") -> None:
    """
    RQ task body: run the LangGraph setup + Chapter 1 pipeline for a novel
    and keep its ``GenerationJob`` row up to date.

    Per-node progress (current agent, output/error summaries) is already
    captured by the ``AgentLog`` rows each agent node writes as it runs
    (keyed by ``novel_id``) — this function only tracks overall job
    status/result, read together with the latest ``AgentLog`` by the
    ``GET /api/novels/{id}/jobs/{job_id}`` endpoint.
    """
    job = get_current_job()
    job_id = job.id if job is not None else None

    if job_id is None:
        # Should never happen when enqueued via app.core.queue.get_queue(),
        # but guard against being called outside of an RQ worker context.
        logger.error("[worker] run_generation_job invoked with no active RQ job")
        raise RuntimeError("run_generation_job must be run inside an RQ worker")

    logger.info("[worker] starting job_id=%s novel_id=%s", job_id, novel_id)
    _update_job(job_id, status="running")

    try:
        final_state = asyncio.run(
            execute_graph(novel_id=novel_id, initial_prompt=initial_prompt)
        )
    except Exception as exc:  # noqa: BLE001 - we want to record *any* failure
        logger.exception("[worker] job_id=%s failed", job_id)
        _update_job(job_id, status="failed", error_message=str(exc))
        raise

    with Session(engine) as session:
        chapter_count = (
            session.query(Chapter).filter(Chapter.novel_id == novel_id).count()
        )

    _update_job(
        job_id,
        status="succeeded",
        chapter_count=chapter_count,
        feedback_score=final_state.get("feedback_score", 0),
    )
    logger.info("[worker] job_id=%s complete", job_id)


def main() -> None:
    """Block and process jobs from the generation queue. Entry point for `python -m app.worker`."""
    logger.info("Starting NovelEngine RQ worker…")
    worker = Worker([get_queue()], connection=get_redis_connection())
    worker.work()


if __name__ == "__main__":
    main()
