"""
SQLAlchemy 2.0 ORM models for NovelEngine.

Database URL is read from the DATABASE_URL environment variable. The
production target is a Supabase-managed Postgres instance; a local Postgres
DSN is used as the fallback default for local development
(``postgresql+psycopg://postgres:postgres@localhost:5432/novelengine``).
SQLite is no longer supported — ``LoreEmbedding`` (see below) requires the
``pgvector`` Postgres extension.
"""

import os
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedColumn,
    mapped_column,
    relationship,
)
from pgvector.sqlalchemy import Vector

# Dimensionality of OpenAI's text-embedding-3-small model, used by
# app.db.vector_store. Keep in sync if the embedding model changes.
EMBEDDING_DIMENSIONS = 1536

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/novelengine",
)

engine = create_engine(DATABASE_URL, echo=False)


class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


# ---------------------------------------------------------------------------
# Novel
# ---------------------------------------------------------------------------

class Novel(Base):
    __tablename__ = "novel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )  # Supabase auth user id (JWT 'sub' claim) — scopes novels per-user
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[Optional[str]] = mapped_column(String(100))
    tone: Mapped[Optional[str]] = mapped_column(String(100))
    themes: Mapped[Optional[str]] = mapped_column(Text)          # comma-separated or JSON string
    synopsis: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    chapters: Mapped[List["Chapter"]] = relationship(
        "Chapter", back_populates="novel", cascade="all, delete-orphan"
    )
    characters: Mapped[List["Character"]] = relationship(
        "Character", back_populates="novel", cascade="all, delete-orphan"
    )
    creatures: Mapped[List["Creature"]] = relationship(
        "Creature", back_populates="novel", cascade="all, delete-orphan"
    )
    agent_logs: Mapped[List["AgentLog"]] = relationship(
        "AgentLog", back_populates="novel", cascade="all, delete-orphan"
    )
    generation_jobs: Mapped[List["GenerationJob"]] = relationship(
        "GenerationJob", back_populates="novel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Novel id={self.id!r} title={self.title!r}>"


# ---------------------------------------------------------------------------
# Chapter
# ---------------------------------------------------------------------------

class Chapter(Base):
    __tablename__ = "chapter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    beat_outline: Mapped[Optional[str]] = mapped_column(Text)    # plot beat notes
    content: Mapped[Optional[str]] = mapped_column(Text)         # generated markdown prose
    summary: Mapped[Optional[str]] = mapped_column(Text)         # short recap for continuity
    red_team_score: Mapped[Optional[int]] = mapped_column(Integer)  # 1-10 critique score
    red_team_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    novel: Mapped["Novel"] = relationship("Novel", back_populates="chapters")

    def __repr__(self) -> str:
        return f"<Chapter id={self.id!r} number={self.chapter_number!r} novel_id={self.novel_id!r}>"


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------

class Character(Base):
    __tablename__ = "character"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(100))     # protagonist, antagonist, etc.
    backstory: Mapped[Optional[str]] = mapped_column(Text)
    personality: Mapped[Optional[str]] = mapped_column(Text)
    abilities: Mapped[Optional[str]] = mapped_column(Text)
    arc_summary: Mapped[Optional[str]] = mapped_column(Text)
    chroma_doc_id: Mapped[Optional[str]] = mapped_column(String(255))  # reference to ChromaDB doc
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    novel: Mapped["Novel"] = relationship("Novel", back_populates="characters")

    def __repr__(self) -> str:
        return f"<Character id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Creature
# ---------------------------------------------------------------------------

class Creature(Base):
    __tablename__ = "creature"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    species_type: Mapped[Optional[str]] = mapped_column(String(100))
    ecology: Mapped[Optional[str]] = mapped_column(Text)
    magical_traits: Mapped[Optional[str]] = mapped_column(Text)
    lore_description: Mapped[Optional[str]] = mapped_column(Text)
    chroma_doc_id: Mapped[Optional[str]] = mapped_column(String(255))  # reference to ChromaDB doc
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    novel: Mapped["Novel"] = relationship("Novel", back_populates="creatures")

    def __repr__(self) -> str:
        return f"<Creature id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# AgentLog
# ---------------------------------------------------------------------------

AGENT_NAMES = (
    "world_architect",
    "creature_architect",
    "character_agent",
    "plot_agent",
    "scene_writer",
    "red_team_auditor",
    "prose_stylist",
    "orchestrator",
)

AGENT_STATUSES = ("started", "running", "completed", "error")


class AgentLog(Base):
    __tablename__ = "agent_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(
        Enum(*AGENT_NAMES, name="agent_name_enum"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(*AGENT_STATUSES, name="agent_status_enum"),
        nullable=False,
        default="started",
    )
    input_summary: Mapped[Optional[str]] = mapped_column(Text)
    output_summary: Mapped[Optional[str]] = mapped_column(Text)
    thoughts: Mapped[Optional[str]] = mapped_column(Text)        # chain-of-thought / scratchpad
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    novel: Mapped["Novel"] = relationship("Novel", back_populates="agent_logs")

    def __repr__(self) -> str:
        return (
            f"<AgentLog id={self.id!r} agent={self.agent_name!r} status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# GenerationJob — Phase 2 async job tracking (Redis + RQ)
# ---------------------------------------------------------------------------

GENERATION_JOB_STATUSES = ("queued", "running", "succeeded", "failed")


class GenerationJob(Base):
    """
    Tracks a single async ``execute_graph()`` run enqueued via RQ.

    ``id`` is the same string used as the RQ job id (a UUID generated by the
    API when the job is enqueued — see ``app.api.novels.generate_novel``),
    so this row and the underlying RQ job are always addressable by the same
    identifier. ``app.worker.run_generation_job`` updates ``status`` /
    ``error_message`` / result fields as the job progresses; fine-grained
    per-node progress is read from the existing ``AgentLog`` rows each agent
    node already writes (via ``novel_id``), rather than duplicated here.
    """

    __tablename__ = "generation_job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # RQ job id (UUID string)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum(*GENERATION_JOB_STATUSES, name="generation_job_status_enum"),
        nullable=False,
        default="queued",
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    chapter_count: Mapped[Optional[int]] = mapped_column(Integer)
    feedback_score: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    novel: Mapped["Novel"] = relationship("Novel", back_populates="generation_jobs")

    def __repr__(self) -> str:
        return f"<GenerationJob id={self.id!r} novel_id={self.novel_id!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# LoreEmbedding — pgvector-backed replacement for the ChromaDB vector store
# ---------------------------------------------------------------------------

class LoreEmbedding(Base):
    """
    A single embedded lore document, replacing the old ChromaDB collections.

    ``collection_name`` mirrors the old Chroma "collection" concept (e.g.
    ``world_lore``, ``creatures``) so ``app.db.vector_store`` can keep the
    same read/write API agent nodes already use. Similarity search uses the
    pgvector ``<=>`` cosine-distance operator via ``embedding.cosine_distance``.
    """

    __tablename__ = "lore_embedding"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID string
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novel.id", ondelete="CASCADE"), nullable=False
    )
    collection_name: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_lore_embedding_novel_collection", "novel_id", "collection_name"),
    )

    def __repr__(self) -> str:
        return (
            f"<LoreEmbedding id={self.id!r} collection={self.collection_name!r} "
            f"novel_id={self.novel_id!r}>"
        )
