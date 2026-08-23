"""
Story 3.3 — Character Agent and Plot Agent nodes.

Implements the third and fourth tiers of the generation pipeline:

  character_agent — designs character dossiers grounded in world + creature
                    lore retrieved from ChromaDB; produces multi-faceted
                    characters with role, backstory, arc, and creature affinity.

  plot_agent      — reads those characters and generates a multi-POV beat sheet
                    for the next chapter, querying ChromaDB for full character
                    and world lore to produce a richly contextualised outline.

Each node:
  1. Queries ChromaDB to ground output in prior agent context (RAG pattern).
  2. Calls the LLM with structured Pydantic output via ``with_structured_output``.
  3. Persists results to ChromaDB (for downstream retrieval) and SQLite (for UI).
  4. Writes an ``AgentLog`` entry so the Live Monitor can track activity.
  5. Returns a partial ``NovelState`` dict consumed by LangGraph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.agents.state import NovelState
from src.db.models import AgentLog, Chapter, Character, Novel, engine
from src.db.vector_store import (
    COLLECTION_CHARACTERS,
    COLLECTION_PLOT_BEATS,
    COLLECTION_CREATURES,
    COLLECTION_WORLD_LORE,
    add_lore,
    search_lore,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared LLM factory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_llm() -> BaseChatModel:
    """
    Instantiate the chat model from environment variables.

    LLM_PROVIDER    — "openai" (default) | "anthropic" | "google"
    LLM_MODEL       — model name (e.g. "gpt-4o")
    LLM_TEMPERATURE — float, default 0.85
    """
    from dotenv import load_dotenv
    load_dotenv()  # no-op if already loaded; ensures .env is read in non-UI entry points

    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    model = os.getenv("LLM_MODEL", "gpt-4o")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.85"))

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import]

        return ChatOpenAI(model=model, temperature=temperature)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]

        return ChatAnthropic(model=model, temperature=temperature)  # type: ignore[call-arg]

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]

        return ChatGoogleGenerativeAI(model=model, temperature=temperature)

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. Choose from: openai, anthropic, google."
    )


# ===========================================================================
# CHARACTER AGENT  (Story 3.3 – part 1)
# ===========================================================================


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class CharacterDossier(BaseModel):
    """Complete dossier for a single novel character."""

    name: str = Field(description="Full name of the character.")
    role: str = Field(
        description=(
            "Narrative role: 'protagonist', 'antagonist', 'mentor', 'ally', "
            "'foil', 'trickster', or 'supporting'."
        )
    )
    origin: str = Field(
        description="The kingdom, faction, or region they come from and how it shaped them."
    )
    backstory: str = Field(
        description=(
            "Formative history: key events before the novel opens that explain "
            "why they are who they are."
        )
    )
    personality: str = Field(
        description="Core traits, communication style, default emotional register, and contradictions."
    )
    motivation: str = Field(
        description="What they consciously want (external goal) and unconsciously need (internal wound)."
    )
    fatal_flaw: str = Field(
        description=(
            "The specific flaw — pride, cowardice, obsession, etc. — that will "
            "drive their arc and threaten to destroy them."
        )
    )
    abilities: str = Field(
        description=(
            "Combat, magical, social, and practical skills. Magic abilities must "
            "reference the world's established magic rules."
        )
    )
    arc_summary: str = Field(
        description=(
            "One sentence: where they start morally/emotionally vs. where they end. "
            "E.g. 'From self-serving mercenary to reluctant saviour who finally accepts loss.'"
        )
    )
    creature_affinity: str = Field(
        description=(
            "How they relate to the world's creatures — fear, reverence, mastery, "
            "or traumatic history with specific beasts."
        )
    )


class CharacterOutput(BaseModel):
    """Top-level wrapper returned by the LLM for a character generation run."""

    characters: list[CharacterDossier] = Field(
        description="Complete cast of 3–6 characters for the novel."
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_CHARACTER_SYSTEM_PROMPT = """\
You are the Character & Story Architect for a multi-agent fantasy novel generation system.

Your task is to design a complete, diverse cast of characters who feel like they grew \
organically from the specific world and bestiary already constructed. Every character \
must be grounded in that world's kingdoms, magic rules, history, and creatures.

Guidelines:
- Create 3–6 characters with distinctly different roles, origins, and voices.
- Backstories must reference named kingdoms, historical events, or magic systems \
  from the world context below.
- Each ability set must respect the established magic rules — no character can use \
  magic that doesn't exist in this world.
- Every character must have a clear relationship (fear, reverence, mastery, trauma) \
  with at least one named creature from the bestiary.
- Fatal flaws must be character-specific, not genre-generic ("hubris" must be \
  *this* character's specific form of hubris).
- Arc summaries must be in tension — the character must change in a direction that \
  costs them something real.
- Return ONLY valid structured JSON matching the required schema.
"""

_CHARACTER_HUMAN_PROMPT = """\
NOVEL BRIEF
  Genre:  {genre}
  Tone:   {tone}
  Themes: {themes}

WORLD CONTEXT (lore retrieved from knowledge base)
{world_lore_context}

BESTIARY CONTEXT (lore retrieved from knowledge base)
{creature_context}

Design the complete cast of characters for this novel.
"""

_CHARACTER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _CHARACTER_SYSTEM_PROMPT), ("human", _CHARACTER_HUMAN_PROMPT)]
)


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------


def _build_character_document(dossier: CharacterDossier) -> str:
    """
    Assemble a rich plain-text document from a ``CharacterDossier`` for
    high-quality semantic retrieval by downstream agents (e.g. the Scene Writer
    asking "who has the darkest relationship with the Vorath?").
    """
    return (
        f"CHARACTER: {dossier.name}\n"
        f"Role: {dossier.role}\n"
        f"Origin: {dossier.origin}\n\n"
        f"BACKSTORY\n{dossier.backstory}\n\n"
        f"PERSONALITY\n{dossier.personality}\n\n"
        f"MOTIVATION\n{dossier.motivation}\n\n"
        f"FATAL FLAW\n{dossier.fatal_flaw}\n\n"
        f"ABILITIES\n{dossier.abilities}\n\n"
        f"CHARACTER ARC\n{dossier.arc_summary}\n\n"
        f"CREATURE AFFINITY\n{dossier.creature_affinity}"
    ).strip()


# ---------------------------------------------------------------------------
# ChromaDB context retrieval  (synchronous — called via run_in_executor)
# ---------------------------------------------------------------------------


def _fetch_lore_for_characters(novel_id: int) -> tuple[str, str]:
    """
    Query ChromaDB for world and creature context relevant to character creation.

    Returns
    -------
    tuple[str, str]
        ``(world_lore_context, creature_context)`` — formatted strings ready
        for injection into the LLM prompt.
    """
    # Pull kingdoms and magic rules — the primary grounding for character origins
    # and ability sets.  We use broad semantic queries so the embeddings do the
    # heavy lifting rather than relying on exact metadata filters alone.
    kingdom_hits = search_lore(
        COLLECTION_WORLD_LORE,
        query="kingdoms factions culture government society people religion",
        k=5,
        where={"$and": [{"novel_id": novel_id}, {"type": "kingdom"}]},
    )
    magic_hits = search_lore(
        COLLECTION_WORLD_LORE,
        query="magic abilities powers practitioners limitations cost",
        k=3,
        where={"$and": [{"novel_id": novel_id}, {"type": "magic_rule"}]},
    )
    history_hits = search_lore(
        COLLECTION_WORLD_LORE,
        query="historical events wars betrayals tragedies that shape characters",
        k=2,
        where={"$and": [{"novel_id": novel_id}, {"type": "history"}]},
    )
    creature_hits = search_lore(
        COLLECTION_CREATURES,
        query="dangerous creatures relationships threat ecological role encounters",
        k=6,
        where={"novel_id": novel_id},
    )

    world_sections: list[str] = []
    if kingdom_hits:
        world_sections.append("--- KINGDOMS ---\n" + "\n\n".join(h["text"] for h in kingdom_hits))
    if magic_hits:
        world_sections.append("--- MAGIC RULES ---\n" + "\n\n".join(h["text"] for h in magic_hits))
    if history_hits:
        world_sections.append("--- HISTORY ---\n" + "\n\n".join(h["text"] for h in history_hits))

    creature_section = (
        "\n\n".join(h["text"] for h in creature_hits)
        if creature_hits
        else "No creature data available yet."
    )

    world_lore_context = (
        "\n\n".join(world_sections)
        if world_sections
        else "No world lore available yet — using state world_context."
    )

    return world_lore_context, creature_section


# ---------------------------------------------------------------------------
# Persistence helpers  (synchronous — called via run_in_executor)
# ---------------------------------------------------------------------------


def _persist_characters_to_chroma(
    novel_id: int, dossiers: list[CharacterDossier]
) -> dict[str, str]:
    """
    Embed each character dossier in ChromaDB.

    Returns ``{character_name: chroma_doc_id}`` for SQLite back-fill.
    """
    doc_ids: dict[str, str] = {}
    for dossier in dossiers:
        doc_id = add_lore(
            COLLECTION_CHARACTERS,
            text=_build_character_document(dossier),
            metadata={
                "novel_id": novel_id,
                "type": "character",
                "name": dossier.name,
                "role": dossier.role,
                "origin": dossier.origin,
            },
        )
        doc_ids[dossier.name] = doc_id
        logger.debug(
            "[character_agent] stored '%s' in ChromaDB (doc_id=%s)",
            dossier.name,
            doc_id,
        )
    return doc_ids


def _persist_characters_to_db(
    novel_id: int,
    dossiers: list[CharacterDossier],
    chroma_doc_ids: dict[str, str],
) -> None:
    """Write one ``Character`` row per dossier and one ``AgentLog`` entry."""
    with Session(engine) as session:
        for dossier in dossiers:
            session.add(
                Character(
                    novel_id=novel_id,
                    name=dossier.name,
                    role=dossier.role,
                    backstory=dossier.backstory,
                    personality=dossier.personality,
                    abilities=dossier.abilities,
                    arc_summary=dossier.arc_summary,
                    chroma_doc_id=chroma_doc_ids.get(dossier.name),
                )
            )
        session.add(
            AgentLog(
                novel_id=novel_id,
                agent_name="character_agent",
                status="completed",
                output_summary=json.dumps(
                    [{"name": d.name, "role": d.role, "arc": d.arc_summary} for d in dossiers],
                    indent=2,
                ),
            )
        )
        session.commit()


def _log_character_error(novel_id: int, error_message: str) -> None:
    try:
        with Session(engine) as session:
            session.add(
                AgentLog(
                    novel_id=novel_id,
                    agent_name="character_agent",
                    status="error",
                    error_message=error_message,
                )
            )
            session.commit()
    except Exception:
        logger.exception("Failed to write character_agent error log")


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


async def character_agent(state: NovelState) -> dict[str, Any]:
    """
    LangGraph node — Character Agent  (Story 3.3).

    Retrieves grounding context from ChromaDB (kingdoms, magic rules,
    creature lore), prompts the LLM for a structured ``CharacterOutput``,
    persists each dossier to ChromaDB and SQLite, and returns
    ``character_list`` for the state.
    """
    novel_id: int = state["novel_id"]
    world_context: str = state.get("world_context", "")
    logger.info("[character_agent] starting for novel_id=%s", novel_id)

    # Fetch novel brief
    with Session(engine) as session:
        novel = session.get(Novel, novel_id)
        genre  = (novel.genre  or "Epic Fantasy")              if novel else "Epic Fantasy"
        tone   = (novel.tone   or "dark, mythic, and lyrical") if novel else "dark, mythic, and lyrical"
        themes = (novel.themes or "power, sacrifice, fate")    if novel else "power, sacrifice, fate"

    # Retrieve grounding lore from ChromaDB in a thread pool
    loop = asyncio.get_event_loop()
    world_lore_context, creature_context = await loop.run_in_executor(
        None, _fetch_lore_for_characters, novel_id
    )

    # Fall back to state world_context if ChromaDB returned nothing
    if world_lore_context.startswith("No world lore"):
        world_lore_context = world_context or "No world context available."

    try:
        chain = _CHARACTER_PROMPT | _build_llm().with_structured_output(CharacterOutput)
        result: CharacterOutput = await chain.ainvoke(
            {
                "genre": genre,
                "tone": tone,
                "themes": themes,
                "world_lore_context": world_lore_context,
                "creature_context": creature_context,
            }
        )
    except Exception as exc:
        logger.exception("[character_agent] LLM chain failed")
        await loop.run_in_executor(None, _log_character_error, novel_id, str(exc))
        raise

    dossiers = result.characters
    logger.info(
        "[character_agent] generated %d characters: %s",
        len(dossiers),
        [d.name for d in dossiers],
    )

    characters_as_dicts: list[dict[str, Any]] = [d.model_dump() for d in dossiers]

    chroma_doc_ids = await loop.run_in_executor(
        None, _persist_characters_to_chroma, novel_id, dossiers
    )
    await loop.run_in_executor(
        None, _persist_characters_to_db, novel_id, dossiers, chroma_doc_ids
    )

    # Attach ChromaDB doc IDs so the Plot Agent can do point-lookups if needed
    for char_dict in characters_as_dicts:
        char_dict["chroma_doc_id"] = chroma_doc_ids.get(char_dict["name"])

    return {"character_list": characters_as_dicts}


# ===========================================================================
# PLOT AGENT  (Story 3.3 – part 2)
# ===========================================================================


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class SceneBeat(BaseModel):
    """A single beat within a chapter — the atomic unit of plotting."""

    beat_number: int = Field(ge=1, description="Sequential beat number within the chapter.")
    pov_character: str = Field(description="Name of the character through whose eyes we see this beat.")
    location: str = Field(description="Specific named location within the world where the beat occurs.")
    summary: str = Field(
        description=(
            "What physically happens: who does what to whom, what decision is made, "
            "what information is revealed or concealed."
        )
    )
    emotional_shift: str = Field(
        description=(
            "The POV character's internal state at the start → end of this beat. "
            "E.g. 'Cautious resolve → paralysing doubt'."
        )
    )
    creature_involvement: Optional[str] = Field(
        default=None,
        description="Named creature(s) that appear or are referenced, with their role in the beat.",
    )
    tension_level: int = Field(
        ge=1,
        le=10,
        description="Narrative tension at the beat's end: 1 = quiet scene, 10 = mortal crisis.",
    )


class ChapterOutline(BaseModel):
    """Full beat-sheet outline for a single chapter."""

    chapter_number: int = Field(description="Sequential chapter number.")
    chapter_title: str = Field(description="Evocative working title for the chapter.")
    chapter_theme: str = Field(
        description="The thematic throughline — what this chapter is really about beneath the plot."
    )
    opening_hook: str = Field(
        description=(
            "The first sentence or image — must immediately establish voice, "
            "tension, and a question the reader needs answered."
        )
    )
    beats: list[SceneBeat] = Field(
        description="5–8 scene beats in chronological order, spread across multiple POVs."
    )
    chapter_ending: str = Field(
        description=(
            "How the chapter closes: a revelation, a betrayal, a death, a cliffhanger — "
            "something that makes putting the book down feel wrong."
        )
    )
    word_count_target: int = Field(
        ge=1000,
        description="Target prose word count for the Scene Writer. Typical range: 3000–6000.",
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PLOT_SYSTEM_PROMPT = """\
You are the Plot Agent for a multi-agent fantasy novel generation system.

Your task is to write a tight, multi-POV beat sheet for the next chapter. Every \
beat must follow causally from the world state, the characters' established arcs, \
and any unresolved tensions introduced in prior chapters. The scene beats are the \
blueprint the Scene Writer will expand into full prose — so they must be specific, \
not vague.

Guidelines:
- Use 5–8 beats across at least 2 different POV characters.
- Tension should rise across the chapter (beats 1–3 establish, beats 4–6 escalate, \
  beats 7–8 detonate or twist).
- At least one beat must deepen a character's arc — move them toward or away from \
  their fatal flaw.
- At least one beat must include a meaningful creature encounter (not decorative).
- Locations must be named places from the world context; do not invent new places.
- The chapter ending must change something irreversibly.
- Return ONLY valid structured JSON matching the required schema.
"""

_PLOT_HUMAN_PROMPT = """\
NOVEL BRIEF
  Genre:  {genre}
  Tone:   {tone}

CHAPTER TARGET: Chapter {chapter_number}

WORLD CONTEXT
{world_context}

CHARACTERS (full dossiers from knowledge base)
{character_context}

BESTIARY SUMMARY
{creatures_summary}

Write the beat sheet for Chapter {chapter_number}.
"""

_PLOT_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _PLOT_SYSTEM_PROMPT), ("human", _PLOT_HUMAN_PROMPT)]
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_chapter_outline(outline: ChapterOutline) -> str:
    """
    Render a ``ChapterOutline`` as structured plain text for ``NovelState``.

    This string is consumed directly by the Scene Writer node as its
    primary instruction set.
    """
    lines: list[str] = [
        f"CHAPTER {outline.chapter_number}: {outline.chapter_title}",
        f"Theme: {outline.chapter_theme}",
        f'Opening hook: "{outline.opening_hook}"',
        f"Target word count: {outline.word_count_target:,}",
        "",
        "SCENE BEATS",
        "=" * 60,
    ]
    for beat in outline.beats:
        creature_line = (
            f"\n  Creatures:       {beat.creature_involvement}"
            if beat.creature_involvement
            else ""
        )
        lines += [
            f"\nBeat {beat.beat_number}  |  POV: {beat.pov_character}  |  Location: {beat.location}  |  Tension: {beat.tension_level}/10",
            f"  Action:          {beat.summary}",
            f"  Emotional shift: {beat.emotional_shift}" + creature_line,
        ]
    lines += [
        "",
        "=" * 60,
        f"CHAPTER ENDING: {outline.chapter_ending}",
    ]
    return "\n".join(lines)


def _summarise_creatures(creatures_list: list[dict[str, Any]]) -> str:
    if not creatures_list:
        return "No creatures generated yet."
    return "\n".join(
        f"• {c.get('name')} (Threat: {c.get('threat_level', '?')}/10) — {c.get('ecological_niche', '')}"
        for c in creatures_list
    )


# ---------------------------------------------------------------------------
# DB + ChromaDB helpers  (synchronous — called via run_in_executor)
# ---------------------------------------------------------------------------


def _get_next_chapter_number(novel_id: int) -> int:
    """Return the next sequential chapter number for the given novel."""
    with Session(engine) as session:
        count = session.scalar(
            select(func.count()).select_from(Chapter).where(Chapter.novel_id == novel_id)
        ) or 0
    return count + 1


def _fetch_context_for_plot(novel_id: int) -> str:
    """
    Query ChromaDB for the full character dossiers and relevant world lore.

    Returns a formatted string ready for injection into the plot prompt.
    This gives the Plot Agent richer detail than the state summary alone.
    """
    char_hits = search_lore(
        COLLECTION_CHARACTERS,
        query="character backstory motivation arc personality abilities fatal flaw",
        k=8,
        where={"novel_id": novel_id},
    )
    world_hits = search_lore(
        COLLECTION_WORLD_LORE,
        query="locations geography kingdoms conflict history tension unresolved",
        k=4,
        where={"novel_id": novel_id},
    )

    sections: list[str] = []
    if char_hits:
        sections.append(
            "--- CHARACTER DOSSIERS (from knowledge base) ---\n\n"
            + "\n\n---\n\n".join(h["text"] for h in char_hits)
        )
    if world_hits:
        sections.append(
            "--- WORLD DETAILS (from knowledge base) ---\n\n"
            + "\n\n".join(h["text"] for h in world_hits)
        )

    return "\n\n".join(sections) if sections else "No context retrieved from knowledge base."


def _persist_outline_to_chroma(novel_id: int, outline: ChapterOutline) -> str:
    """Store the chapter outline as a plot-beat document; return its doc_id."""
    text = (
        f"CHAPTER {outline.chapter_number} OUTLINE: {outline.chapter_title}\n"
        f"Theme: {outline.chapter_theme}\n\n"
        + "\n".join(
            f"Beat {b.beat_number} [{b.pov_character} @ {b.location}]: {b.summary}"
            for b in outline.beats
        )
        + f"\n\nEnding: {outline.chapter_ending}"
    )
    return add_lore(
        COLLECTION_PLOT_BEATS,
        text=text,
        metadata={
            "novel_id": novel_id,
            "type": "chapter_outline",
            "chapter_number": outline.chapter_number,
            "chapter_title": outline.chapter_title,
        },
    )


def _log_plot_completed(novel_id: int, outline: ChapterOutline) -> None:
    summary = json.dumps(
        {
            "chapter_number": outline.chapter_number,
            "chapter_title": outline.chapter_title,
            "beats": len(outline.beats),
            "pov_characters": list({b.pov_character for b in outline.beats}),
            "word_count_target": outline.word_count_target,
        },
        indent=2,
    )
    with Session(engine) as session:
        session.add(
            AgentLog(
                novel_id=novel_id,
                agent_name="plot_agent",
                status="completed",
                output_summary=summary,
            )
        )
        session.commit()


def _log_plot_error(novel_id: int, error_message: str) -> None:
    try:
        with Session(engine) as session:
            session.add(
                AgentLog(
                    novel_id=novel_id,
                    agent_name="plot_agent",
                    status="error",
                    error_message=error_message,
                )
            )
            session.commit()
    except Exception:
        logger.exception("Failed to write plot_agent error log")


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


async def plot_agent(state: NovelState) -> dict[str, Any]:
    """
    LangGraph node — Plot Agent  (Story 3.3).

    Determines the next chapter number, retrieves full character dossiers and
    world lore from ChromaDB, prompts the LLM for a structured
    ``ChapterOutline``, persists the outline to ChromaDB, and returns the
    formatted ``current_outline`` string for the state.
    """
    novel_id: int = state["novel_id"]
    world_context: str = state.get("world_context", "")
    creatures_list: list[dict[str, Any]] = state.get("creatures_list", [])
    logger.info("[plot_agent] starting for novel_id=%s", novel_id)

    # Fetch novel brief
    with Session(engine) as session:
        novel = session.get(Novel, novel_id)
        genre = (novel.genre or "Epic Fantasy") if novel else "Epic Fantasy"
        tone  = (novel.tone  or "dark, mythic") if novel else "dark, mythic"

    loop = asyncio.get_event_loop()

    chapter_number: int = await loop.run_in_executor(
        None, _get_next_chapter_number, novel_id
    )

    # Retrieve enriched character + world context from ChromaDB
    character_context: str = await loop.run_in_executor(
        None, _fetch_context_for_plot, novel_id
    )

    # Fall back to state creature list summary if ChromaDB has nothing
    creatures_summary = _summarise_creatures(creatures_list)

    try:
        chain = _PLOT_PROMPT | _build_llm().with_structured_output(ChapterOutline)
        result: ChapterOutline = await chain.ainvoke(
            {
                "genre": genre,
                "tone": tone,
                "chapter_number": chapter_number,
                "world_context": world_context or "No world context in state.",
                "character_context": character_context,
                "creatures_summary": creatures_summary,
            }
        )
    except Exception as exc:
        logger.exception("[plot_agent] LLM chain failed")
        await loop.run_in_executor(None, _log_plot_error, novel_id, str(exc))
        raise

    logger.info(
        "[plot_agent] outlined Chapter %d '%s' with %d beats across POVs: %s",
        result.chapter_number,
        result.chapter_title,
        len(result.beats),
        list({b.pov_character for b in result.beats}),
    )

    current_outline = _format_chapter_outline(result)

    await loop.run_in_executor(None, _persist_outline_to_chroma, novel_id, result)
    await loop.run_in_executor(None, _log_plot_completed, novel_id, result)

    return {"current_outline": current_outline}
