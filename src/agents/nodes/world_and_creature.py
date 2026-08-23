"""
Stories 3.1 & 3.2 — World Architect and Creature Architect agent nodes.

Both nodes are co-located here because they form the first two tiers of the
generation pipeline (world → creatures) and share LLM and ChromaDB infrastructure.

Each node:
  1. Calls the LLM with structured output (Pydantic via ``with_structured_output``).
  2. Persists results to ChromaDB for semantic retrieval by downstream agents.
  3. Writes an ``AgentLog`` entry to SQLite for the Live Monitor UI.
  4. Returns a partial ``NovelState`` dict consumed by LangGraph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.agents.state import NovelState
from src.db.models import AgentLog, Creature, Novel, engine
from src.db.vector_store import (
    COLLECTION_CREATURES,
    COLLECTION_WORLD_LORE,
    add_lore,
    build_creature_document,
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
# WORLD ARCHITECT  (Story 3.1)
# ===========================================================================


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class Kingdom(BaseModel):
    """A major political or cultural faction in the world."""

    name: str = Field(description="Name of the kingdom or faction.")
    capital: str = Field(description="Name of the capital city or seat of power.")
    government_type: str = Field(
        description="E.g. 'Theocratic Monarchy', 'Mercantile Republic', 'Warlord Hegemony'."
    )
    culture_summary: str = Field(
        description="Dominant values, customs, social hierarchy, and defining character."
    )
    geography: str = Field(
        description="Where the kingdom sits in the world and its defining terrain."
    )
    notable_conflicts: str = Field(
        description="Current wars, ancient grievances, or prophesied threats."
    )
    military_strength: int = Field(
        ge=1,
        le=10,
        description="Relative military power: 1 = village militia, 10 = world-conquering empire.",
    )


class MagicRule(BaseModel):
    """A distinct school, law, or system of magic in the world."""

    name: str = Field(description="Name of this branch or law of magic.")
    category: str = Field(
        description="Broad category, e.g. 'Elemental', 'Blood', 'Runic', 'Necromantic', 'Pact'."
    )
    mechanism: str = Field(
        description="How it physically or metaphysically functions — the internal logic."
    )
    limitations: str = Field(
        description="Hard constraints: what it cannot do, who cannot use it, or what it destroys."
    )
    cost: str = Field(
        description=(
            "What the practitioner pays: stamina, years of life, moral corruption, "
            "a physical component, etc."
        )
    )


class WorldOutput(BaseModel):
    """Complete world-building output from the World Architect."""

    geography_overview: str = Field(
        description=(
            "2–3 paragraph overview of continents, seas, climate zones, "
            "and the most dramatic landmarks."
        )
    )
    kingdoms: list[Kingdom] = Field(
        description="3–5 major kingdoms or factions spanning different cultural archetypes."
    )
    magic_rules: list[MagicRule] = Field(
        description="2–4 distinct schools or immutable laws of magic."
    )
    key_historical_events: list[str] = Field(
        description=(
            "5–8 pivotal past events (wars, cataclysms, betrayals, covenants) "
            "that actively shape present-day politics."
        )
    )
    cosmology: str = Field(
        description=(
            "The nature of the universe: the gods (or their absence), planes of existence, "
            "the afterlife, and the world's creation myth."
        )
    )


# ---------------------------------------------------------------------------
# System + human prompts
# ---------------------------------------------------------------------------

_WORLD_SYSTEM_PROMPT = """\
You are the World Architect for a multi-agent fantasy novel generation system.

Your task is to construct a rich, internally consistent fantasy world that will \
serve as the immovable foundation for every character, creature, conflict, and \
chapter in the novel. Downstream agents will embed your output into a vector \
database and retrieve it when writing scenes — so specificity and vivid detail \
are far more valuable than generic tropes.

Guidelines:
- Geography must drive culture and conflict. Landlocked kingdoms hoard \
  mountain passes; seafarers grow wealthy and decadent; desert peoples \
  become ruthless survivalists.
- Each kingdom needs a believable reason to exist in tension with at least \
  one other.
- Magic must be constrained by hard rules and meaningful costs — unconstrained \
  magic flattens narrative stakes.
- History should contain unresolved seeds (stolen relics, broken treaties, \
  murdered heirs) that a plot agent can grow into story beats.
- Cosmology should be lived-in — echoed in oaths, architecture, and burial rites.
- Return ONLY valid structured JSON matching the required schema. No prose \
  outside the JSON object.
"""

_WORLD_HUMAN_PROMPT = """\
Novel brief:
  Genre:  {genre}
  Tone:   {tone}
  Themes: {themes}

Build the complete world for this novel.
"""

_WORLD_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _WORLD_SYSTEM_PROMPT), ("human", _WORLD_HUMAN_PROMPT)]
)


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------


def _format_world_context(output: WorldOutput) -> str:
    """Render a ``WorldOutput`` as structured plain text for ``NovelState``."""
    lines: list[str] = [
        "=== GEOGRAPHY ===",
        output.geography_overview,
        "",
        "=== KINGDOMS ===",
    ]
    for k in output.kingdoms:
        lines += [
            f"• {k.name}  |  Capital: {k.capital}  |  Gov: {k.government_type}  |  Military: {k.military_strength}/10",
            f"  Culture:   {k.culture_summary}",
            f"  Geography: {k.geography}",
            f"  Conflicts: {k.notable_conflicts}",
            "",
        ]
    lines += ["=== MAGIC SYSTEM ==="]
    for m in output.magic_rules:
        lines += [
            f"• {m.name}  [{m.category}]",
            f"  How it works: {m.mechanism}",
            f"  Limitations:  {m.limitations}",
            f"  Cost:         {m.cost}",
            "",
        ]
    lines += ["=== KEY HISTORY ==="]
    for event in output.key_historical_events:
        lines.append(f"• {event}")
    lines += ["", "=== COSMOLOGY ===", output.cosmology]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers  (synchronous — run via asyncio.run_in_executor)
# ---------------------------------------------------------------------------


def _persist_world_to_chroma(novel_id: int, output: WorldOutput) -> None:
    """
    Store world lore in ChromaDB as separate, fine-grained documents so that
    downstream agents can retrieve the specific fragments they need (e.g.
    "which kingdoms border the ice fields?").
    """
    # Full geography + cosmology in one document
    add_lore(
        COLLECTION_WORLD_LORE,
        text=(
            f"WORLD OVERVIEW (novel_id={novel_id})\n\n"
            f"{output.geography_overview}\n\n"
            f"COSMOLOGY\n{output.cosmology}"
        ),
        metadata={"novel_id": novel_id, "type": "world_overview"},
    )

    # One document per kingdom — enables spatial/cultural retrieval
    for kingdom in output.kingdoms:
        add_lore(
            COLLECTION_WORLD_LORE,
            text=(
                f"KINGDOM: {kingdom.name}\n"
                f"Capital: {kingdom.capital}\n"
                f"Government: {kingdom.government_type}\n"
                f"Culture: {kingdom.culture_summary}\n"
                f"Geography: {kingdom.geography}\n"
                f"Conflicts: {kingdom.notable_conflicts}"
            ),
            metadata={
                "novel_id": novel_id,
                "type": "kingdom",
                "name": kingdom.name,
                "military_strength": kingdom.military_strength,
            },
        )

    # One document per magic rule — enables ability-specific retrieval
    for rule in output.magic_rules:
        add_lore(
            COLLECTION_WORLD_LORE,
            text=(
                f"MAGIC RULE: {rule.name}  [{rule.category}]\n"
                f"Mechanism: {rule.mechanism}\n"
                f"Limitations: {rule.limitations}\n"
                f"Cost: {rule.cost}"
            ),
            metadata={
                "novel_id": novel_id,
                "type": "magic_rule",
                "name": rule.name,
                "category": rule.category,
            },
        )

    # Full history as one document
    add_lore(
        COLLECTION_WORLD_LORE,
        text="KEY HISTORICAL EVENTS\n" + "\n".join(f"• {e}" for e in output.key_historical_events),
        metadata={"novel_id": novel_id, "type": "history"},
    )


def _log_world_completed(novel_id: int, output: WorldOutput) -> None:
    summary = json.dumps(
        {
            "kingdoms": [k.name for k in output.kingdoms],
            "magic_rules": [m.name for m in output.magic_rules],
            "history_events": len(output.key_historical_events),
        },
        indent=2,
    )
    with Session(engine) as session:
        session.add(
            AgentLog(
                novel_id=novel_id,
                agent_name="world_architect",
                status="completed",
                output_summary=summary,
            )
        )
        session.commit()


def _log_world_error(novel_id: int, error_message: str) -> None:
    try:
        with Session(engine) as session:
            session.add(
                AgentLog(
                    novel_id=novel_id,
                    agent_name="world_architect",
                    status="error",
                    error_message=error_message,
                )
            )
            session.commit()
    except Exception:
        logger.exception("Failed to write world_architect error log")


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


async def world_architect(state: NovelState) -> dict[str, Any]:
    """
    LangGraph node — World Architect  (Story 3.1).

    Reads the novel brief (genre, tone, themes) from SQLite, prompts the LLM
    for a structured ``WorldOutput``, persists every kingdom and magic rule to
    ChromaDB as individual documents, logs completion to ``AgentLog``, and
    returns the formatted ``world_context`` string for the state.
    """
    novel_id: int = state["novel_id"]
    logger.info("[world_architect] starting for novel_id=%s", novel_id)

    # Fetch novel brief — all values fall back gracefully if the row is missing
    with Session(engine) as session:
        novel = session.get(Novel, novel_id)
        genre  = (novel.genre  or "Epic Fantasy")              if novel else "Epic Fantasy"
        tone   = (novel.tone   or "dark, mythic, and lyrical") if novel else "dark, mythic, and lyrical"
        themes = (novel.themes or "power, sacrifice, fate")    if novel else "power, sacrifice, fate"

    try:
        chain = _WORLD_PROMPT | _build_llm().with_structured_output(WorldOutput)
        result: WorldOutput = await chain.ainvoke(
            {"genre": genre, "tone": tone, "themes": themes}
        )
    except Exception as exc:
        logger.exception("[world_architect] LLM chain failed")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _log_world_error, novel_id, str(exc))
        raise

    logger.info(
        "[world_architect] generated %d kingdoms, %d magic rules, %d history events",
        len(result.kingdoms),
        len(result.magic_rules),
        len(result.key_historical_events),
    )

    world_context = _format_world_context(result)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _persist_world_to_chroma, novel_id, result)
    await loop.run_in_executor(None, _log_world_completed, novel_id, result)

    return {"world_context": world_context}


# ===========================================================================
# CREATURE ARCHITECT  (Story 3.2)
# ===========================================================================


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class CreatureDossier(BaseModel):
    """Structured description of a single mythical creature."""

    name: str = Field(description="Unique name of the creature.")
    species_type: str = Field(
        description="Broad taxonomic category (e.g. 'Draconic', 'Fae', 'Eldritch Aberration')."
    )
    anatomy: str = Field(
        description=(
            "Detailed physical description: body plan, size, distinguishing features, "
            "sensory organs, and any unusual biological adaptations."
        )
    )
    magical_properties: str = Field(
        description=(
            "Active and passive magical abilities referencing the world's established "
            "magic rules.  Include elemental affinities, known weaknesses, and resistances."
        )
    )
    ecological_niche: str = Field(
        description=(
            "Habitat, diet, reproductive strategy, social structure, and relationships "
            "with other species — including whether it is prey, predator, or symbiont."
        )
    )
    threat_level: int = Field(
        ge=1,
        le=10,
        description=(
            "Danger rating: 1 = harmless curiosity, 10 = civilization-ending apex predator. "
            "Factor in raw power, intelligence, and territorial aggression."
        ),
    )
    lore_description: str = Field(
        description=(
            "One evocative paragraph written for an in-world bestiary: second-person "
            "present tense, slightly ominous, suitable for a scholar who has never seen it."
        )
    )


class CreatureOutput(BaseModel):
    """Top-level wrapper returned by the LLM for a single generation run."""

    creatures: list[CreatureDossier] = Field(
        description="List of creature dossiers generated for this world."
    )


# ---------------------------------------------------------------------------
# System + human prompts
# ---------------------------------------------------------------------------

_CREATURE_SYSTEM_PROMPT = """\
You are the Creature Architect for a fantasy novel generation system.

Design a cohesive bestiary of mythical creatures that are deeply embedded in \
the world described below. Every creature must feel like it evolved inside \
that specific world — its anatomy, magic, and ecology must be a direct \
consequence of the world's geography, magic system, and history.

Guidelines:
- Create between 3 and 6 distinct creatures per invocation.
- Each creature must occupy a unique ecological niche — no redundant roles.
- Anatomy should be vivid and internally consistent; avoid generic tropes \
  unless given a radical, specific twist.
- Magical properties must reference and respect the magic rules already \
  established in the world context.
- Threat levels should span the full range so the world feels layered \
  (at least one tier-1 and one tier-8+ creature).
- The lore_description must read like a passage from an ancient bestiary: \
  atmospheric, slightly ominous, written as if the author has only heard \
  second-hand accounts.
- Return ONLY valid structured JSON matching the required schema.
"""

_CREATURE_HUMAN_PROMPT = """\
World context:
{world_context}

Design a bestiary of mythical creatures for this world.
"""

_CREATURE_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _CREATURE_SYSTEM_PROMPT), ("human", _CREATURE_HUMAN_PROMPT)]
)


# ---------------------------------------------------------------------------
# Persistence helpers  (synchronous — run via asyncio.run_in_executor)
# ---------------------------------------------------------------------------


def _persist_creatures_to_chroma(
    novel_id: int, dossiers: list[CreatureDossier]
) -> dict[str, str]:
    """
    Embed and store each creature dossier in ChromaDB.

    Returns ``{creature_name: chroma_doc_id}`` so the SQLite write can
    back-fill the ``chroma_doc_id`` column in the same call.
    """
    doc_ids: dict[str, str] = {}
    for dossier in dossiers:
        doc_id = add_lore(
            COLLECTION_CREATURES,
            text=build_creature_document(dossier.model_dump()),
            metadata={
                "novel_id": novel_id,
                "type": "creature",
                "name": dossier.name,
                "species_type": dossier.species_type,
                "threat_level": dossier.threat_level,
            },
        )
        doc_ids[dossier.name] = doc_id
        logger.debug(
            "[creature_architect] stored '%s' in ChromaDB (doc_id=%s)",
            dossier.name,
            doc_id,
        )
    return doc_ids


def _persist_creatures_to_db(
    novel_id: int,
    dossiers: list[CreatureDossier],
    world_context_len: int,
    chroma_doc_ids: dict[str, str],
) -> None:
    """Write one ``Creature`` row per dossier plus a single ``AgentLog`` entry."""
    with Session(engine) as session:
        for dossier in dossiers:
            session.add(
                Creature(
                    novel_id=novel_id,
                    name=dossier.name,
                    species_type=dossier.species_type,
                    ecology=dossier.ecological_niche,
                    magical_traits=dossier.magical_properties,
                    lore_description=dossier.lore_description,
                    chroma_doc_id=chroma_doc_ids.get(dossier.name),
                )
            )
        session.add(
            AgentLog(
                novel_id=novel_id,
                agent_name="creature_architect",
                status="completed",
                input_summary=f"world_context length={world_context_len} chars",
                output_summary=json.dumps(
                    [{"name": d.name, "threat_level": d.threat_level} for d in dossiers],
                    indent=2,
                ),
            )
        )
        session.commit()


def _log_creature_error(novel_id: int, error_message: str) -> None:
    try:
        with Session(engine) as session:
            session.add(
                AgentLog(
                    novel_id=novel_id,
                    agent_name="creature_architect",
                    status="error",
                    error_message=error_message,
                )
            )
            session.commit()
    except Exception:
        logger.exception("Failed to write creature_architect error log")


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


async def creature_architect(state: NovelState) -> dict[str, Any]:
    """
    LangGraph node — Creature Architect  (Story 3.2).

    Reads ``state['world_context']``, prompts the LLM for a structured
    ``CreatureOutput``, stores each dossier as a ChromaDB document, back-fills
    the ``chroma_doc_id`` column in SQLite, and returns ``creatures_list``.
    """
    novel_id: int = state["novel_id"]
    world_context: str = state.get("world_context", "")

    logger.info("[creature_architect] starting for novel_id=%s", novel_id)

    if not world_context:
        logger.warning(
            "[creature_architect] world_context is empty — creatures will lack world grounding."
        )

    try:
        chain = _CREATURE_PROMPT | _build_llm().with_structured_output(CreatureOutput)
        result: CreatureOutput = await chain.ainvoke({"world_context": world_context})
    except Exception as exc:
        logger.exception("[creature_architect] LLM chain failed")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _log_creature_error, novel_id, str(exc))
        raise

    dossiers = result.creatures
    logger.info(
        "[creature_architect] generated %d creatures: %s",
        len(dossiers),
        [d.name for d in dossiers],
    )

    creatures_as_dicts: list[dict[str, Any]] = [d.model_dump() for d in dossiers]

    # Persist to ChromaDB first, then to SQLite (chroma IDs flow into the DB write)
    loop = asyncio.get_event_loop()
    chroma_doc_ids: dict[str, str] = await loop.run_in_executor(
        None, _persist_creatures_to_chroma, novel_id, dossiers
    )
    await loop.run_in_executor(
        None,
        _persist_creatures_to_db,
        novel_id,
        dossiers,
        len(world_context),
        chroma_doc_ids,
    )

    # Attach ChromaDB doc IDs to the state dicts so downstream nodes can do
    # point-lookups without re-running a semantic search.
    for creature_dict in creatures_as_dicts:
        creature_dict["chroma_doc_id"] = chroma_doc_ids.get(creature_dict["name"])

    return {"creatures_list": creatures_as_dicts}
