"""
Stories 3.4, 3.4.1, 3.5, 3.6.1 — Scene Writer, Red Team Auditor, Prose Stylist.

These three nodes form the creative writing loop of NovelEngine:

  scene_writer    — transforms the PlotAgent beat sheet into markdown chapter
                    prose; on revision passes it incorporates Red Team feedback.

  red_team        — critiques the draft for lore consistency, character
                    authenticity, and narrative quality; outputs a structured
                    1-10 score with actionable revision notes.

  prose_stylist   — applies final stylistic polish after the Red Team approves
                    (score ≥ 8) or the revision budget is exhausted.

Pipeline
--------
  plot_agent → scene_writer → red_team
                  ↑               │ score < 8  (and loops < MAX_REVISION_LOOPS)
                  └───────────────┘
                                  │ score ≥ 8  (or cap reached)
                                  ▼
                           prose_stylist → END

SQLite writes  (Stories 3.4.1 & 3.6.1)
---------------------------------------
* scene_writer:   creates a Chapter row on the first pass; updates it on each
                  revision.  Fields written: chapter_number, title, beat_outline,
                  content (raw draft).
* red_team:       updates Chapter.red_team_score and Chapter.red_team_notes.
* prose_stylist:  overwrites Chapter.content with the polished final text.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.agents.state import NovelState
from src.db.models import AgentLog, Chapter, Novel, engine
from src.db.vector_store import (
    COLLECTION_CHARACTERS,
    COLLECTION_CREATURES,
    COLLECTION_WORLD_LORE,
    search_lore,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM factories  (two instances: creative vs. analytical temperature)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_creative_llm() -> BaseChatModel:
    """High-temperature model for prose generation and stylistic polishing."""
    from dotenv import load_dotenv
    load_dotenv()

    provider    = os.getenv("LLM_PROVIDER", "openai").lower()
    model       = os.getenv("LLM_MODEL", "gpt-4o")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.9"))

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import]
        return ChatOpenAI(model=model, temperature=temperature)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]
        return ChatAnthropic(model=model, temperature=temperature)  # type: ignore[call-arg]
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
        return ChatGoogleGenerativeAI(model=model, temperature=temperature)
    raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'.")


@lru_cache(maxsize=1)
def _build_critique_llm() -> BaseChatModel:
    """Low-temperature model for analytical audit tasks (fixed at 0.25)."""
    from dotenv import load_dotenv
    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    model    = os.getenv("LLM_MODEL", "gpt-4o")

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import]
        return ChatOpenAI(model=model, temperature=0.25)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]
        return ChatAnthropic(model=model, temperature=0.25)  # type: ignore[call-arg]
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
        return ChatGoogleGenerativeAI(model=model, temperature=0.25)
    raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'.")


# ===========================================================================
# SCENE WRITER  (Stories 3.4 & 3.4.1)
# ===========================================================================

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class SceneWriterOutput(BaseModel):
    """Structured output produced by the Scene Writer LLM chain."""

    chapter_title: str = Field(
        description=(
            "A compelling chapter title (4-8 words) that hints at the central "
            "event without spoiling it.  Example: 'The Fall of the Silver Gate'."
        )
    )
    prose: str = Field(
        description=(
            "The full chapter in Markdown, minimum 1,200 words. "
            "Use vivid sensory detail, authentic character voice, and escalating "
            "tension.  Scene breaks are marked with a blank line containing '---'."
        )
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SCENE_SYSTEM_PROMPT = """\
You are a master fantasy prose author working inside a multi-agent novel generation system.
Your task is to transform a detailed beat outline into a richly-written chapter of a \
publication-quality fantasy novel.

CORE REQUIREMENTS
- Minimum 1,200 words of prose.
- Write in close third-person POV, staying with the designated POV character per beat.
- Anchor every beat in at least one grounding sensory detail \
  (what the character sees, hears, feels, smells, or tastes).
- Dialogue must reveal character — not just advance plot. Each character must have \
  a distinct voice shaped by their backstory and personality.
- Magic must follow the established rules exactly — never invent new powers or \
  capabilities that do not exist in this world's magic system.
- Creatures must behave according to their established ecology and threat level.
- Use sentence-level variation: short sentences for kinetic action, longer periodic \
  sentences for reflection and atmosphere.
- End the chapter on a micro-tension hook that compels the reader forward.
- Return ONLY valid structured JSON matching the required schema.
"""

_SCENE_HUMAN_PROMPT = """\
NOVEL CONTEXT
  Genre:    {genre}
  Tone:     {tone}
  Themes:   {themes}
  Synopsis: {synopsis}

WORLD & CHARACTER LORE  (retrieved from knowledge base)
{lore_context}

CHAPTER BEAT OUTLINE
{beat_outline}

{revision_context}\
Write this chapter now.\
"""

_SCENE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SCENE_SYSTEM_PROMPT),
    ("human",  _SCENE_HUMAN_PROMPT),
])


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_next_chapter_number(novel_id: int) -> int:
    """Return the next sequential chapter number for *novel_id*."""
    with Session(engine) as session:
        count = session.scalar(
            select(func.count())
            .select_from(Chapter)
            .where(Chapter.novel_id == novel_id)
        )
        return (count or 0) + 1


def _upsert_chapter_draft(
    novel_id: int,
    chapter_id: int,
    chapter_number: int,
    title: str,
    beat_outline: str,
    content: str,
) -> int:
    """
    Create a new Chapter row when *chapter_id* == 0, otherwise update the
    existing row.  Returns the chapter primary key in both cases.
    """
    with Session(engine) as session:
        if chapter_id:
            chapter = session.get(Chapter, chapter_id)
            if chapter:
                if title:
                    chapter.title = title
                chapter.content = content
                session.commit()
                return chapter_id

        # First pass — create a fresh row
        chapter = Chapter(
            novel_id=novel_id,
            chapter_number=chapter_number,
            title=title,
            beat_outline=beat_outline,
            content=content,
        )
        session.add(chapter)
        session.commit()
        session.refresh(chapter)
        return chapter.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Lore retrieval  (shared by all three nodes)
# ---------------------------------------------------------------------------


def _fetch_writing_context(novel_id: int) -> str:
    """
    Query ChromaDB for world, character, and creature lore associated with
    *novel_id*.  Returns a single formatted string for prompt injection.
    Errors are silenced so the pipeline does not abort on a cold vector store.
    """
    sections: list[str] = []

    def _safe_search(collection: str, query: str, k: int) -> list[dict[str, Any]]:
        try:
            return search_lore(collection, query, k=k, where={"novel_id": novel_id})
        except Exception:
            return []

    world_hits = _safe_search(
        COLLECTION_WORLD_LORE,
        "geography kingdoms magic systems history cosmology",
        k=8,
    )
    if world_hits:
        sections.append("=== WORLD LORE ===")
        for h in world_hits:
            meta  = h.get("metadata") or {}
            label = meta.get("name") or meta.get("type") or "World"
            sections.append(f"[{label}]\n{h['text']}")

    char_hits = _safe_search(
        COLLECTION_CHARACTERS,
        "character backstory personality abilities arc fatal flaw",
        k=6,
    )
    if char_hits:
        sections.append("\n=== CHARACTERS ===")
        for h in char_hits:
            meta  = h.get("metadata") or {}
            label = meta.get("name") or "Character"
            sections.append(f"[{label}]\n{h['text']}")

    creature_hits = _safe_search(
        COLLECTION_CREATURES,
        "creature ecology magical traits threat level lore",
        k=4,
    )
    if creature_hits:
        sections.append("\n=== CREATURES ===")
        for h in creature_hits:
            meta  = h.get("metadata") or {}
            label = meta.get("name") or "Creature"
            sections.append(f"[{label}]\n{h['text']}")

    return "\n\n".join(sections) if sections else "No lore context available yet."


# ---------------------------------------------------------------------------
# AgentLog helper
# ---------------------------------------------------------------------------


def _log_scene(
    novel_id: int,
    status: str,
    input_summary: str = "",
    output_summary: str = "",
    thoughts: str = "",
    error_message: str = "",
) -> None:
    with Session(engine) as session:
        session.add(AgentLog(
            novel_id=novel_id,
            agent_name="scene_writer",
            status=status,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            thoughts=thoughts[:1000],
            error_message=error_message[:500],
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def scene_writer(state: NovelState) -> dict[str, Any]:
    """
    Story 3.4 / 3.4.1 — Generate (or revise) chapter prose from the beat outline.

    First pass  (revision_count == 0):  creates a new Chapter row and stores
                                        the raw draft in Chapter.content.
    Revision passes:                    updates the existing row, applying the
                                        Red Team's feedback to the prose.
    """
    novel_id:       int = state["novel_id"]
    revision_count: int = state.get("revision_count", 0)
    chapter_id:     int = state.get("chapter_id", 0)
    beat_outline:   str = state.get("current_outline", "")

    logger.info(
        "[scene_writer] novel_id=%s  revision=%s  chapter_id=%s",
        novel_id, revision_count, chapter_id,
    )

    _log_scene(
        novel_id, "started",
        input_summary=f"revision={revision_count}  chapter_id={chapter_id}",
        thoughts=f"Beat outline: {len(beat_outline)} chars",
    )

    try:
        # Chapter number only needed when creating a new row
        is_new_chapter = (revision_count == 0 or chapter_id == 0)
        chapter_number = _get_next_chapter_number(novel_id) if is_new_chapter else 0

        # Novel metadata
        with Session(engine) as session:
            novel    = session.get(Novel, novel_id)
            genre    = (novel.genre    or "Epic Fantasy")    if novel else "Epic Fantasy"
            tone     = (novel.tone     or "Dark and lyrical") if novel else "Dark and lyrical"
            themes   = (novel.themes   or "")                 if novel else ""
            synopsis = (novel.synopsis or "")                 if novel else ""

        # Lore context from ChromaDB
        lore_context = _fetch_writing_context(novel_id)

        # Build optional revision section (present on second+ passes)
        if revision_count > 0 and state.get("current_draft") and state.get("feedback_notes"):
            revision_context = (
                "PREVIOUS DRAFT  (rewrite based on the feedback below)\n"
                "---\n"
                f"{state['current_draft']}\n"
                "---\n\n"
                "RED TEAM FEEDBACK — ADDRESS EVERY POINT\n"
                f"{state['feedback_notes']}\n\n"
                "---\n\n"
            )
        else:
            revision_context = ""

        chain = _SCENE_PROMPT | _build_creative_llm().with_structured_output(SceneWriterOutput)
        result: SceneWriterOutput = await chain.ainvoke({
            "genre":            genre,
            "tone":             tone,
            "themes":           themes,
            "synopsis":         synopsis,
            "lore_context":     lore_context,
            "beat_outline":     beat_outline,
            "revision_context": revision_context,
        })

        prose         = result.prose
        chapter_title = result.chapter_title

        # Persist to SQLite
        chapter_id = _upsert_chapter_draft(
            novel_id, chapter_id,
            chapter_number, chapter_title,
            beat_outline, prose,
        )

        _log_scene(
            novel_id, "completed",
            input_summary=(
                f"revision={revision_count}  beats={len(beat_outline.splitlines())}"
            ),
            output_summary=(
                f'chapter_id={chapter_id}  title="{chapter_title}"  '
                f"words≈{len(prose.split())}"
            ),
            thoughts=(
                f"Generated {len(prose)} chars on revision pass {revision_count}. "
                f"chapter_number={chapter_number if is_new_chapter else 'existing'}."
            ),
        )

        return {
            "current_draft":  prose,
            "chapter_id":     chapter_id,
            "revision_count": revision_count + 1,
        }

    except Exception as exc:
        logger.exception("[scene_writer] failed for novel_id=%s", novel_id)
        _log_scene(novel_id, "error", error_message=str(exc)[:500])
        raise


# ===========================================================================
# RED TEAM AUDITOR  (Story 3.5)
# ===========================================================================

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class RedTeamCritique(BaseModel):
    """Structured critique returned by the Red Team Auditor LLM chain."""

    score: int = Field(
        ge=1, le=10,
        description=(
            "Overall quality score 1-10. "
            "8+ means the draft is ready for the Prose Stylist. "
            "Below 8 means the Scene Writer must revise. "
            "10 = publication-ready without changes. "
            "1 = fundamental structural flaws requiring a full rewrite."
        ),
    )
    revision_notes: str = Field(
        description=(
            "Specific, actionable feedback the Scene Writer must address. "
            "Reference exact passages; quote briefly when needed; prescribe fixes. "
            "Minimum 150 words."
        )
    )
    lore_violations: list[str] = Field(
        description=(
            "Each entry is a specific lore inconsistency "
            "(e.g. 'Kael uses fire magic on page 3 but the magic system only "
            "allows cold manipulation'). Empty list if none found."
        )
    )
    strengths: list[str] = Field(
        description="2-4 specific things the draft does well, citing passages where possible."
    )
    priority_fixes: list[str] = Field(
        description=(
            "The top 3 highest-priority issues (most damaging to reader experience) "
            "that the Scene Writer must address first."
        )
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_RED_TEAM_SYSTEM_PROMPT = """\
You are a meticulous senior editor and lore-consistency auditor for a multi-agent \
fantasy novel system. Your role is to rigorously evaluate a chapter draft and return \
structured feedback that will guide the Scene Writer's revisions.

EVALUATION CRITERIA  (weight each equally):

1. LORE CONSISTENCY  — Does the prose honour the established world rules, magic system, \
   creature ecology, and character histories exactly? Flag any violation with context.

2. CHARACTER AUTHENTICITY  — Does every character speak and act consistently with their \
   dossier (backstory, fatal flaw, motivation, voice)? Are their decisions believable?

3. NARRATIVE CRAFT  — Does the chapter follow the beat outline? Is pacing effective? \
   Does tension escalate beat to beat? Does the chapter end with a forward hook?

4. PROSE QUALITY  — Is the language vivid, specific, and free of clichés? \
   Is single-POV maintained? Is dialogue revealing rather than merely functional?

5. THEMATIC RESONANCE  — Does the chapter visibly advance the novel's core themes?

SCORING GUIDE
  9-10: Exceptional — minor polish only.
   8:   Strong — a few specific tweaks; proceed to Prose Stylist.
   6-7: Adequate — targeted revisions before proceeding.
   4-5: Significant issues — substantial revision required.
   1-3: Fundamental problems — near-full rewrite needed.

Be precise and unsparing. Vague notes ("improve the dialogue") are not acceptable; \
cite specific passages and prescribe exact fixes.
Return ONLY valid structured JSON matching the required schema.
"""

_RED_TEAM_HUMAN_PROMPT = """\
NOVEL BRIEF
  Genre:   {genre}
  Tone:    {tone}
  Themes:  {themes}

BEAT OUTLINE  (what this chapter was supposed to achieve)
{beat_outline}

LORE CONTEXT  (world rules and character dossiers from knowledge base)
{lore_context}

CHAPTER DRAFT TO EVALUATE
---
{chapter_draft}
---

Audit this draft now and return your structured critique.\
"""

_RED_TEAM_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _RED_TEAM_SYSTEM_PROMPT),
    ("human",  _RED_TEAM_HUMAN_PROMPT),
])


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _persist_red_team_score(
    chapter_id: int,
    score: int,
    notes: str,
    lore_violations: list[str],
    priority_fixes: list[str],
) -> None:
    """Write the Red Team critique fields into the Chapter row."""
    full_notes = notes
    if lore_violations:
        full_notes += "\n\nLORE VIOLATIONS:\n" + "\n".join(f"• {v}" for v in lore_violations)
    if priority_fixes:
        full_notes += "\n\nPRIORITY FIXES:\n" + "\n".join(f"• {f}" for f in priority_fixes)

    with Session(engine) as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter:
            chapter.red_team_score = score
            chapter.red_team_notes = full_notes
            session.commit()


# ---------------------------------------------------------------------------
# AgentLog helper
# ---------------------------------------------------------------------------


def _log_red_team(
    novel_id: int,
    status: str,
    input_summary: str = "",
    output_summary: str = "",
    thoughts: str = "",
    error_message: str = "",
) -> None:
    with Session(engine) as session:
        session.add(AgentLog(
            novel_id=novel_id,
            agent_name="red_team_auditor",
            status=status,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            thoughts=thoughts[:1000],
            error_message=error_message[:500],
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def red_team(state: NovelState) -> dict[str, Any]:
    """
    Story 3.5 — Critique the scene draft for lore consistency and narrative quality.

    Queries ChromaDB for lore context, evaluates the draft with a low-temperature
    LLM chain, writes the score and notes to the Chapter row, and emits an
    AgentLog entry for the Live Monitor.
    """
    novel_id:     int = state["novel_id"]
    chapter_id:   int = state.get("chapter_id", 0)
    draft:        str = state.get("current_draft", "")
    beat_outline: str = state.get("current_outline", "")

    logger.info("[red_team] novel_id=%s  chapter_id=%s", novel_id, chapter_id)

    _log_red_team(
        novel_id, "started",
        input_summary=f"chapter_id={chapter_id}  draft_len={len(draft)}",
    )

    try:
        with Session(engine) as session:
            novel   = session.get(Novel, novel_id)
            genre   = (novel.genre   or "Epic Fantasy")    if novel else "Epic Fantasy"
            tone    = (novel.tone    or "Dark and lyrical") if novel else "Dark and lyrical"
            themes  = (novel.themes  or "")                 if novel else ""

        lore_context = _fetch_writing_context(novel_id)

        chain = _RED_TEAM_PROMPT | _build_critique_llm().with_structured_output(RedTeamCritique)
        critique: RedTeamCritique = await chain.ainvoke({
            "genre":         genre,
            "tone":          tone,
            "themes":        themes,
            "beat_outline":  beat_outline,
            "lore_context":  lore_context,
            "chapter_draft": draft,
        })

        # Persist score + notes to SQLite
        if chapter_id:
            _persist_red_team_score(
                chapter_id,
                critique.score,
                critique.revision_notes,
                critique.lore_violations,
                critique.priority_fixes,
            )

        # Build the combined feedback notes that scene_writer will read on revision
        feedback_notes = critique.revision_notes
        if critique.priority_fixes:
            feedback_notes += (
                "\n\nPRIORITY FIXES:\n" +
                "\n".join(f"• {f}" for f in critique.priority_fixes)
            )
        if critique.lore_violations:
            feedback_notes += (
                "\n\nLORE VIOLATIONS TO CORRECT:\n" +
                "\n".join(f"• {v}" for v in critique.lore_violations)
            )

        _log_red_team(
            novel_id, "completed",
            input_summary=f"chapter_id={chapter_id}  draft_words≈{len(draft.split())}",
            output_summary=(
                f"score={critique.score}/10  "
                f"violations={len(critique.lore_violations)}  "
                f"priority_fixes={len(critique.priority_fixes)}"
            ),
            thoughts=json.dumps({
                "score":          critique.score,
                "strengths":      critique.strengths,
                "priority_fixes": critique.priority_fixes,
            }, ensure_ascii=False, indent=2),
        )

        return {
            "feedback_score": critique.score,
            "feedback_notes": feedback_notes,
        }

    except Exception as exc:
        logger.exception("[red_team] failed for novel_id=%s", novel_id)
        _log_red_team(novel_id, "error", error_message=str(exc)[:500])
        raise


# ===========================================================================
# PROSE STYLIST  (Story 3.6.1)
# ===========================================================================

# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class ProseStylistOutput(BaseModel):
    """Structured output from the Prose Stylist LLM chain."""

    polished_prose: str = Field(
        description=(
            "The fully polished chapter prose in Markdown. "
            "Preserve all narrative content, scene structure, and lore details; "
            "improve only sentence craft, rhythm, imagery, and voice consistency."
        )
    )
    style_notes: str = Field(
        description=(
            "2-4 sentences summarising the key stylistic changes made. "
            "Stored in the AgentLog for transparency."
        )
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROSE_SYSTEM_PROMPT = """\
You are a master prose stylist performing the final editorial pass on a fantasy novel \
chapter. The narrative content, plot beats, and lore details are already approved. \
Your task is exclusively to elevate the prose to publication quality.

WHAT TO IMPROVE
- Sentence variety: alternate short, punchy sentences with longer, periodic ones.
- Sensory immersion: ensure every scene opens with at least one grounding sensory beat.
- Verb power: replace weak verb + adverb constructions with precise, vivid verbs.
- Dialogue rhythm: ensure speech tags are invisible and every line of dialogue \
  advances character or tension.
- Opening line: the first sentence must be immediately arresting.
- Closing line: the last sentence must leave the reader compelled to turn the page.

WHAT TO PRESERVE
- All plot events, in their existing order.
- Every character name, place name, creature name, and magic term.
- Established magic rules and creature behaviour.
- POV and narrative tense.

Return ONLY valid structured JSON matching the required schema.
"""

_PROSE_HUMAN_PROMPT = """\
NOVEL CONTEXT
  Genre:    {genre}
  Tone:     {tone}
  Themes:   {themes}

WORLD CONTEXT  (for tone and atmosphere reference)
{world_context}

CHAPTER DRAFT TO POLISH
---
{chapter_draft}
---

Apply your full stylistic craft to this chapter now.\
"""

_PROSE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _PROSE_SYSTEM_PROMPT),
    ("human",  _PROSE_HUMAN_PROMPT),
])


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _finalize_chapter(chapter_id: int, polished_prose: str) -> None:
    """Overwrite Chapter.content with the polished text (Story 3.6.1)."""
    with Session(engine) as session:
        chapter = session.get(Chapter, chapter_id)
        if chapter:
            chapter.content = polished_prose
            session.commit()


# ---------------------------------------------------------------------------
# AgentLog helper
# ---------------------------------------------------------------------------


def _log_prose(
    novel_id: int,
    status: str,
    input_summary: str = "",
    output_summary: str = "",
    thoughts: str = "",
    error_message: str = "",
) -> None:
    with Session(engine) as session:
        session.add(AgentLog(
            novel_id=novel_id,
            agent_name="prose_stylist",
            status=status,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            thoughts=thoughts[:1000],
            error_message=error_message[:500],
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


async def prose_stylist(state: NovelState) -> dict[str, Any]:
    """
    Story 3.6.1 — Apply final prose polish to the approved chapter draft.

    Overwrites Chapter.content in SQLite with the polished text so the
    Manuscript Reader tab always surfaces the best available version.
    """
    novel_id:   int = state["novel_id"]
    chapter_id: int = state.get("chapter_id", 0)
    draft:      str = state.get("current_draft", "")

    logger.info("[prose_stylist] novel_id=%s  chapter_id=%s", novel_id, chapter_id)

    _log_prose(
        novel_id, "started",
        input_summary=f"chapter_id={chapter_id}  draft_words≈{len(draft.split())}",
    )

    try:
        with Session(engine) as session:
            novel         = session.get(Novel, novel_id)
            genre         = (novel.genre    or "Epic Fantasy")    if novel else "Epic Fantasy"
            tone          = (novel.tone     or "Dark and lyrical") if novel else "Dark and lyrical"
            themes        = (novel.themes   or "")                 if novel else ""
            world_context = (novel.synopsis or "")                 if novel else ""

        chain = _PROSE_PROMPT | _build_creative_llm().with_structured_output(ProseStylistOutput)
        result: ProseStylistOutput = await chain.ainvoke({
            "genre":         genre,
            "tone":          tone,
            "themes":        themes,
            "world_context": world_context,
            "chapter_draft": draft,
        })

        polished = result.polished_prose

        # Persist to SQLite — Story 3.6.1
        if chapter_id:
            _finalize_chapter(chapter_id, polished)

        _log_prose(
            novel_id, "completed",
            input_summary=f"chapter_id={chapter_id}  raw_words≈{len(draft.split())}",
            output_summary=(
                f"polished_words≈{len(polished.split())}  "
                f'style_notes="{result.style_notes[:120]}"'
            ),
            thoughts=result.style_notes,
        )

        return {"current_draft": polished}

    except Exception as exc:
        logger.exception("[prose_stylist] failed for novel_id=%s", novel_id)
        _log_prose(novel_id, "error", error_message=str(exc)[:500])
        raise
