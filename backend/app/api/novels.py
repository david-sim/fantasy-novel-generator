"""
REST API for novels, their chapters/characters/creatures, and generation.

All endpoints are scoped to the authenticated user (``owner_id`` = the
Supabase auth user id extracted from the request's JWT). A user can only
ever see or act on their own novels.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.agents.graph import execute_graph
from app.core.auth import get_current_user_id
from app.db.models import Chapter, Character, Creature, Novel, engine

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
    novel_id: int
    chapter_count: int
    feedback_score: int


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
def list_novels(owner_id: str = Depends(get_current_user_id)) -> list[Novel]:
    """List every novel owned by the authenticated user."""
    with Session(engine) as session:
        novels = (
            session.query(Novel)
            .filter(Novel.owner_id == owner_id)
            .order_by(Novel.created_at.desc())
            .all()
        )
        session.expunge_all()
        return novels


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


@router.post("/{novel_id}/generate", response_model=GenerateResponse)
async def generate_novel(
    novel_id: int,
    payload: GenerateRequest,
    owner_id: str = Depends(get_current_user_id),
) -> GenerateResponse:
    """
    Run the LangGraph generation pipeline for this novel and return once done.

    NOTE (Phase 1 -> Phase 2): this call is synchronous and will block the
    request for the full multi-minute duration of a generation run. In
    Phase 2 this will be replaced with an endpoint that enqueues an
    Upstash Redis + RQ background job and returns immediately with a job id,
    polled by the frontend. The call to ``execute_graph`` itself is expected
    to carry over unchanged into the worker.
    """
    with Session(engine) as session:
        _get_owned_novel(session, novel_id, owner_id)

    logger.info("[generate_novel] starting synchronous run for novel_id=%s", novel_id)
    final_state = await execute_graph(novel_id=novel_id, initial_prompt=payload.initial_prompt)

    with Session(engine) as session:
        chapter_count = (
            session.query(Chapter).filter(Chapter.novel_id == novel_id).count()
        )

    return GenerateResponse(
        novel_id=novel_id,
        chapter_count=chapter_count,
        feedback_score=final_state.get("feedback_score", 0),
    )
