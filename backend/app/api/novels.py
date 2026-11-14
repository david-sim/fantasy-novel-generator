"""
REST API for novels, their chapters/characters/creatures, and generation.

All endpoints are scoped to the authenticated user (``owner_id`` = the
Supabase auth user id extracted from the request's JWT). A user can only
ever see or act on their own novels.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.core.auth import get_current_user_id
from app.core.queue import get_queue
from app.db.models import AgentLog, Chapter, Character, Creature, GenerationJob, Novel, engine
from app.services.export import (
    export_filename_stem,
    has_exportable_content,
    render_markdown,
    render_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels", tags=["novels"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NovelCreate(BaseModel):
    title: str
    genre: Optional[str] = None
    tone: Optional[str] = None
    themes: Optional[str] = None
    synopsis: Optional[str] = None


class NovelSummary(BaseModel):
    id: int
    title: str
    genre: Optional[str] = None
    tone: Optional[str] = None
    synopsis: Optional[str] = None
    chapter_count: int = 0

    model_config = {"from_attributes": True}


class ChapterOut(BaseModel):
    id: int
    chapter_number: int
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None

    model_config = {"from_attributes": True}


class CharacterOut(BaseModel):
    id: int
    name: str
    role: Optional[str] = None
    backstory: Optional[str] = None
    arc_summary: Optional[str] = None

    model_config = {"from_attributes": True}


class CreatureOut(BaseModel):
    id: int
    name: str
    species_type: Optional[str] = None
    lore_description: Optional[str] = None

    model_config = {"from_attributes": True}


class NovelDetail(NovelSummary):
    chapters: list[ChapterOut] = []
    characters: list[CharacterOut] = []
    creatures: list[CreatureOut] = []


class GenerateRequest(BaseModel):
    initial_prompt: str = ""


class GenerateResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    novel_id: int
    status: str
    error_message: Optional[str] = None
    chapter_count: Optional[int] = None
    feedback_score: Optional[int] = None
    current_agent: Optional[str] = None
    latest_log: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_owned_novel(session: Session, novel_id: int, owner_id: str) -> Novel:
    novel = session.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found.")
    if novel.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your novel.")
    return novel


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[NovelSummary])
def list_novels(owner_id: str = Depends(get_current_user_id)) -> list[NovelSummary]:
    """List every novel owned by the authenticated user, with its chapter count."""
    with Session(engine) as session:
        novels = (
            session.query(Novel)
            .options(selectinload(Novel.chapters))
            .filter(Novel.owner_id == owner_id)
            .order_by(Novel.created_at.desc())
            .all()
        )
        return [
            NovelSummary(
                id=novel.id,
                title=novel.title,
                genre=novel.genre,
                tone=novel.tone,
                synopsis=novel.synopsis,
                chapter_count=len(novel.chapters),
            )
            for novel in novels
        ]


@router.post("", response_model=NovelSummary, status_code=status.HTTP_201_CREATED)
def create_novel(
    payload: NovelCreate, owner_id: str = Depends(get_current_user_id)
) -> Novel:
    """Create a new novel owned by the authenticated user."""
    with Session(engine) as session:
        with session.begin():
            novel = Novel(owner_id=owner_id, **payload.model_dump())
            session.add(novel)
            session.flush()
            session.refresh(novel)
            session.expunge(novel)
        return novel


@router.get("/{novel_id}", response_model=NovelDetail)
def get_novel(
    novel_id: int, owner_id: str = Depends(get_current_user_id)
) -> Novel:
    """Fetch a single novel with its chapters, characters, and creatures."""
    with Session(engine) as session:
        novel = (
            session.query(Novel)
            .options(
                selectinload(Novel.chapters),
                selectinload(Novel.characters),
                selectinload(Novel.creatures),
            )
            .filter(Novel.id == novel_id)
            .first()
        )
        if novel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found.")
        if novel.owner_id != owner_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your novel.")
        session.expunge_all()
        return novel


@router.post(
    "/{novel_id}/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_novel(
    novel_id: int,
    payload: GenerateRequest,
    owner_id: str = Depends(get_current_user_id),
) -> GenerateResponse:
    """
    Enqueue an async LangGraph generation job for this novel and return
    immediately with a job id.

    Phase 2: generation now runs in a separate ``rq worker`` process (see
    ``app.worker``) instead of blocking this request for the full
    multi-minute run. Poll ``GET /{novel_id}/jobs/{job_id}`` for progress and
    the final result.
    """
    with Session(engine) as session:
        _get_owned_novel(session, novel_id, owner_id)

    job_id = str(uuid.uuid4())
    with Session(engine) as session:
        with session.begin():
            session.add(GenerationJob(id=job_id, novel_id=novel_id, status="queued"))

    get_queue().enqueue(
        "app.worker.run_generation_job",
        novel_id=novel_id,
        initial_prompt=payload.initial_prompt,
        job_id=job_id,
        job_timeout="20m",
    )

    logger.info(
        "[generate_novel] enqueued job_id=%s for novel_id=%s", job_id, novel_id
    )

    return GenerateResponse(job_id=job_id, status="queued")


@router.get("/{novel_id}/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    novel_id: int,
    job_id: str,
    owner_id: str = Depends(get_current_user_id),
) -> JobStatusResponse:
    """Return the current status/progress of a previously enqueued generation job."""
    with Session(engine) as session:
        _get_owned_novel(session, novel_id, owner_id)

        job = session.get(GenerationJob, job_id)
        if job is None or job.novel_id != novel_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

        latest_log = (
            session.query(AgentLog)
            .filter(AgentLog.novel_id == novel_id)
            .order_by(AgentLog.run_at.desc())
            .first()
        )

        return JobStatusResponse(
            job_id=job.id,
            novel_id=job.novel_id,
            status=job.status,
            error_message=job.error_message,
            chapter_count=job.chapter_count,
            feedback_score=job.feedback_score,
            current_agent=latest_log.agent_name if latest_log else None,
            latest_log=(
                (latest_log.error_message or latest_log.output_summary) if latest_log else None
            ),
        )


@router.get("/{novel_id}/export")
def export_novel(
    novel_id: int,
    format: str,  # noqa: A002 - "format" is the clearest query param name here
    owner_id: str = Depends(get_current_user_id),
) -> Response:
    """
    Export a completed novel as a downloadable Markdown or PDF document.

    Phase 3: reuses the Phase 1 owner-only authorization dependency and the
    same content structure (title, ordered chapters, character/creature lore
    appendix) for both formats — Markdown is serialized directly, PDF is
    rendered via Jinja2 -> WeasyPrint from the same data.
    """
    if format not in ("markdown", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format. Use 'markdown' or 'pdf'.",
        )

    with Session(engine) as session:
        novel = (
            session.query(Novel)
            .options(
                selectinload(Novel.chapters),
                selectinload(Novel.characters),
                selectinload(Novel.creatures),
            )
            .filter(Novel.id == novel_id)
            .first()
        )
        if novel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found.")
        if novel.owner_id != owner_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your novel.")

        if not has_exportable_content(novel):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This novel has no completed chapters yet. Generate it "
                    "before exporting."
                ),
            )

        filename_stem = export_filename_stem(novel)

        if format == "markdown":
            body = render_markdown(novel)
            return Response(
                content=body,
                media_type="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename_stem}.md"'
                },
            )

        pdf_bytes = render_pdf(novel)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"'
            },
        )
