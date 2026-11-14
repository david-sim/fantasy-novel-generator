"""
FastAPI application entrypoint for NovelEngine.

Run locally with:
    uvicorn app.main:app --reload --port 8000

Wraps the existing, unchanged LangGraph/LangChain pipeline (app.agents) with
a REST API, replacing the retired Streamlit UI. The frontend is a separate
Next.js app (see /frontend) that talks to this API using a Supabase session
JWT for authentication.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import novels
from app.core.config import settings
from app.db.init_db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NovelEngine API",
    description=(
        "REST API wrapping the NovelEngine LangGraph pipeline. "
        "Phase 1 scaffold: the /generate endpoint is synchronous for now "
        "and will become an async queued job (Upstash Redis + RQ) in Phase 2."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(novels.router)


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Starting NovelEngine API — initialising database schema…")
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    """Simple liveness/readiness probe used by Render/Fly.io and Docker HEALTHCHECK."""
    return {"status": "ok"}
