"""
Story 3.2 — Creature Architect node.

Designs mythical beasts for the novel's world by prompting the LLM with
structured output via LangChain's `with_structured_output`. Each invocation
produces a list of `CreatureDossier` objects that are:
  - returned as dicts into NovelState["creatures_list"]
  - persisted to the SQLite `creature` table
  - recorded in the `agent_log` table

LLM provider is resolved at runtime from the LLM_PROVIDER env var
(default: "openai").  Swap to "anthropic" etc. by changing that variable
and ensuring the matching `langchain_<provider>` package is installed.
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

from src.db.models import AgentLog, Creature, engine
from src.db.vector_store import (
    COLLECTION_CREATURES,
    add_lore,
    build_creature_document,
)
from src.agents.state import NovelState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic output schema
# ---------------------------------------------------------------------------


class CreatureDossier(BaseModel):
    """Structured description of a single mythical creature."""

    name: str = Field(description="Unique name of the creature.")
    species_type: str = Field(
        description="Broad taxonomic category (e.g. 'Draconic', 'Fae', 'Eldritch')."
    )
    anatomy: str = Field(
        description=(
            "Detailed physical description: body plan, size, distinguishing features, "
            "sensory organs, and any unusual biological adaptations."
        )
    )
    magical_properties: str = Field(
        description=(
            "Active and passive magical abilities, elemental affinities, "
            "known weaknesses or resistances, and any lore-breaking exceptions."
        )
    )
    ecological_niche: str = Field(
        description=(
            "Habitat, diet, reproductive strategy, social structure, "
            "and relationship with other species in the world."
        )
    )
    threat_level: int = Field(
        ge=1,
        le=10,
        description=(
            "Danger rating from 1 (harmless) to 10 (civilization-ending). "
            "Consider raw power, intelligence, and territorial aggression."
        ),
    )
    lore_description: str = Field(
        description=(
            "One evocative paragraph suitable for an in-world bestiary entry, "
            "written in second-person present tense."
        )
    )


class CreatureOutput(BaseModel):
    """Top-level wrapper returned by the LLM for a single generation run."""

    creatures: list[CreatureDossier] = Field(
        description="List of creature dossiers generated for this world."
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the Creature Architect for a fantasy novel generation system.

Your task is to design a cohesive bestiary of mythical creatures that are \
deeply embedded in the world described below. Every creature must feel like \
it evolved inside that specific world — its magic, ecology, and threat level \
should be a natural consequence of the world's geography, magic systems, and \
history.

Guidelines:
- Create between 3 and 6 distinct creatures per invocation.
- Each creature must occupy a unique ecological niche (avoid redundancy).
- Anatomy should be vivid and internally consistent — no generic dragons or \
  wolves unless given a radical twist.
- Magical properties must reference and respect the magic system described in \
  the world context.
- Threat levels should span the full range so the world feels layered.
- The lore_description field should read like a passage from an ancient \
  bestiary: atmospheric, slightly ominous, written for a scholar who has \
  never seen the creature.
- Return ONLY valid structured JSON matching the required schema.
"""

_HUMAN_PROMPT = """\
World context:
{world_context}

Design a bestiary of mythical creatures for this world.
"""

_PROMPT = ChatPromptTemplate.from_messages(
    [("system", _SYSTEM_PROMPT), ("human", _HUMAN_PROMPT)]
)


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_llm() -> BaseChatModel:
    """
    Instantiate the chat model from environment variables.

    LLM_PROVIDER  — "openai" (default) | "anthropic" | "google"
    LLM_MODEL     — model name passed to the provider (e.g. "gpt-4o")
    LLM_TEMPERATURE — float, default 0.9
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    model = os.getenv("LLM_MODEL", "gpt-4o")
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

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. "
        "Choose from: openai, anthropic, google."
    )


def _build_chain() -> Any:
    """Return the prompt | LLM chain with structured output bound to CreatureOutput."""
    llm = _build_llm()
    structured_llm = llm.with_structured_output(CreatureOutput)
    return _PROMPT | structured_llm


# ---------------------------------------------------------------------------
# Database helpers (synchronous — called via run_in_executor)
# ---------------------------------------------------------------------------


def _persist_to_chroma(
    novel_id: int,
    dossiers: list[CreatureDossier],
) -> dict[str, str]:
    """
    Embed and store each creature dossier in ChromaDB.

    Returns a mapping of ``creature.name -> chroma_doc_id`` so the SQLite
    write can back-fill the ``chroma_doc_id`` column in the same transaction.
    """
    doc_ids: dict[str, str] = {}
    for dossier in dossiers:
        document_text = build_creature_document(dossier.model_dump())
        metadata = {
            "novel_id": novel_id,
            "type": "creature",
            "name": dossier.name,
            "species_type": dossier.species_type,
            "threat_level": dossier.threat_level,
        }
        doc_id = add_lore(
            collection_name=COLLECTION_CREATURES,
            text=document_text,
            metadata=metadata,
        )
        doc_ids[dossier.name] = doc_id
        logger.debug(
            "[creature_architect] stored '%s' in ChromaDB (doc_id=%s)",
            dossier.name,
            doc_id,
        )
    return doc_ids


def _persist_to_db(
    novel_id: int,
    dossiers: list[CreatureDossier],
    output_summary: str,
    chroma_doc_ids: dict[str, str],
) -> None:
    """Write Creature rows (with chroma_doc_id) and an AgentLog entry."""
    with Session(engine) as session:
        with session.begin():
            for dossier in dossiers:
                creature = Creature(
                    novel_id=novel_id,
                    name=dossier.name,
                    species_type=dossier.species_type,
                    ecology=dossier.ecological_niche,
                    magical_traits=dossier.magical_properties,
                    lore_description=dossier.lore_description,
                    chroma_doc_id=chroma_doc_ids.get(dossier.name),
                )
                session.add(creature)

            log = AgentLog(
                novel_id=novel_id,
                agent_name="creature_architect",
                status="completed",
                input_summary=f"world_context length={len(output_summary)} chars",
                output_summary=output_summary,
            )
            session.add(log)


def _log_agent_error(novel_id: int, error_message: str) -> None:
    """Write a failed AgentLog entry."""
    try:
        with Session(engine) as session:
            with session.begin():
                log = AgentLog(
                    novel_id=novel_id,
                    agent_name="creature_architect",
                    status="error",
                    error_message=error_message,
                )
                session.add(log)
    except Exception:
        logger.exception("Failed to write error log for creature_architect")


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


async def creature_architect(state: NovelState) -> dict[str, Any]:
    """
    LangGraph node — Creature Architect.

    Reads `state['world_context']`, calls the LLM chain with structured output,
    persists results to SQLite, and returns the updated `creatures_list`.
    """
    novel_id: int = state["novel_id"]
    world_context: str = state.get("world_context", "")

    logger.info("[creature_architect] starting for novel_id=%s", novel_id)

    if not world_context:
        logger.warning(
            "[creature_architect] world_context is empty — creatures may lack grounding."
        )

    try:
        chain = _build_chain()
        result: CreatureOutput = await chain.ainvoke({"world_context": world_context})
    except Exception as exc:
        logger.exception("[creature_architect] LLM chain failed")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _log_agent_error, novel_id, str(exc))
        raise

    dossiers: list[CreatureDossier] = result.creatures
    creatures_as_dicts: list[dict[str, Any]] = [d.model_dump() for d in dossiers]

    output_summary = json.dumps(
        [{"name": d.name, "threat_level": d.threat_level} for d in dossiers],
        indent=2,
    )
    logger.info(
        "[creature_architect] generated %d creatures: %s",
        len(dossiers),
        [d.name for d in dossiers],
    )

    # Persist to ChromaDB then SQLite — both run in a thread pool so the
    # async event loop is never blocked by synchronous I/O.
    loop = asyncio.get_event_loop()
    chroma_doc_ids: dict[str, str] = await loop.run_in_executor(
        None, _persist_to_chroma, novel_id, dossiers
    )
    await loop.run_in_executor(
        None, _persist_to_db, novel_id, dossiers, output_summary, chroma_doc_ids
    )

    # Attach doc_ids to the state dicts for downstream nodes that may want
    # to retrieve context by ID without a semantic search round-trip.
    for creature_dict in creatures_as_dicts:
        creature_dict["chroma_doc_id"] = chroma_doc_ids.get(creature_dict["name"])

    return {"creatures_list": creatures_as_dicts}
