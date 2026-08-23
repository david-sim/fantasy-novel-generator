"""
SQLAlchemy 2.0 ORM models for NovelEngine.

Database URL is read from the DATABASE_URL environment variable.
Falls back to a local SQLite file: sqlite:///./novelengine.db
"""

import os
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
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

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./novelengine.db")

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
