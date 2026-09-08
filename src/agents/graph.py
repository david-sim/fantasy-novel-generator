"""
LangGraph orchestration for NovelEngine.

Defines the NovelState, all agent node functions, and the compiled StateGraph
with conditional routing from the Red Team Auditor.

Two entry points are exposed:

``execute_graph(novel_id, initial_prompt)``
    Runs the full setup pipeline (World → Creature → Character → Plot →
    Scene/Red-Team loop → Prose Stylist) and stops after producing
    Chapter 1. Used once per novel, from the "Start Generation" action.

``execute_next_chapter(novel_id)``
    Runs only the chapter-writing tail (Plot → Scene/Red-Team loop → Prose
    Stylist) for an already-set-up novel. Used from the "Generate Next
    Chapter" button — never re-runs World/Creature/Character generation,
    which is both unnecessary (that lore doesn't change) and the single
    biggest token-efficiency win available: it avoids re-billing three full
    structured-output LLM calls for every subsequent chapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, NamedTuple

from langgraph.graph import END, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.agents.state import NovelState
from src.agents.nodes.world_and_creature import creature_architect, world_architect
from src.agents.nodes.planning import character_agent, plot_agent
from src.agents.nodes.writing import prose_stylist, red_team, scene_writer
from src.db.models import Chapter, Character, Novel, engine

logger = logging.getLogger(__name__)

# Maximum Scene Writer → Red Team revision loops before forcing prose_stylist.
# Prevents infinite cycles when the LLM consistently scores below the threshold.
MAX_REVISION_LOOPS: int = int(os.getenv("MAX_REVISION_LOOPS", "5"))


# ---------------------------------------------------------------------------
# Node functions (async placeholders)
# world_architect, creature_architect  → src.agents.nodes.world_and_creature
# character_agent, plot_agent          → src.agents.nodes.planning
# ---------------------------------------------------------------------------


# scene_writer, red_team, prose_stylist are imported from src.agents.nodes.writing


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _route_after_red_team(state: NovelState) -> str:
    """
    Conditional router out of the Red Team Auditor node.

    Routes back to scene_writer when ``feedback_score < 8`` and the revision
    budget has not been exhausted.  Forces prose_stylist once
    ``MAX_REVISION_LOOPS`` is reached so the pipeline always terminates.
    """
    score: int = state.get("feedback_score", 0)
    loops: int = state.get("revision_count", 0)

    if loops >= MAX_REVISION_LOOPS:
        logger.warning(
            "[router] max revisions (%d) reached for novel_id=%s — "
            "forcing prose_stylist (final score=%d)",
            MAX_REVISION_LOOPS,
            state.get("novel_id"),
            score,
        )
        return "prose_stylist"

    if score < 8:
        logger.info(
            "[router] score=%d < 8 (revision %d/%d) — routing back to scene_writer",
            score,
            loops,
            MAX_REVISION_LOOPS,
        )
        return "scene_writer"

    logger.info(
        "[router] score=%d >= 8 after %d revision(s) — routing to prose_stylist",
        score,
        loops,
    )
    return "prose_stylist"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> Any:
    """Construct, wire, and compile the NovelEngine StateGraph."""
    graph = StateGraph(NovelState)

    # ------------------------------------------------------------------ nodes
    graph.add_node("world_architect",   world_architect)
    graph.add_node("creature_architect", creature_architect)
    graph.add_node("character_agent",   character_agent)
    graph.add_node("plot_agent",        plot_agent)
    graph.add_node("scene_writer",      scene_writer)
    graph.add_node("red_team",          red_team)
    graph.add_node("prose_stylist",     prose_stylist)

    # --------------------------------------------------------- sequential edges
    graph.set_entry_point("world_architect")
    graph.add_edge("world_architect",   "creature_architect")
    graph.add_edge("creature_architect", "character_agent")
    graph.add_edge("character_agent",   "plot_agent")
    graph.add_edge("plot_agent",        "scene_writer")
    graph.add_edge("scene_writer",      "red_team")

    # ----------------------------------------- conditional edge out of red_team
    graph.add_conditional_edges(
        "red_team",
        _route_after_red_team,
        {
            "scene_writer":  "scene_writer",
            "prose_stylist": "prose_stylist",
        },
    )

    graph.add_edge("prose_stylist", END)

    return graph.compile()


# Module-level compiled graph — import and call execute_graph() or invoke directly.
novel_graph = build_graph()


def build_chapter_graph() -> Any:
    """
    Construct, wire, and compile the standalone chapter-writing StateGraph.

    Identical to the tail of ``build_graph()`` (Plot → Scene/Red-Team loop →
    Prose Stylist) but entered directly at ``plot_agent`` — it never touches
    World/Creature/Character generation. Used for every "Generate Next
    Chapter" run once a novel's setup is already complete.
    """
    graph = StateGraph(NovelState)

    graph.add_node("plot_agent",    plot_agent)
    graph.add_node("scene_writer",  scene_writer)
    graph.add_node("red_team",      red_team)
    graph.add_node("prose_stylist", prose_stylist)

    graph.set_entry_point("plot_agent")
    graph.add_edge("plot_agent",   "scene_writer")
    graph.add_edge("scene_writer", "red_team")

    graph.add_conditional_edges(
        "red_team",
        _route_after_red_team,
        {
            "scene_writer":  "scene_writer",
            "prose_stylist": "prose_stylist",
        },
    )

    graph.add_edge("prose_stylist", END)

    return graph.compile()


# Module-level compiled graph for chapter-by-chapter continuation.
chapter_graph = build_chapter_graph()


# ---------------------------------------------------------------------------
# Novel progress helper (used by the UI to decide which action is available)
# ---------------------------------------------------------------------------


class NovelProgress(NamedTuple):
    """Snapshot of how far a novel's generation has progressed."""

    setup_complete: bool
    """True once World/Creature/Character generation has produced at least
    one Character row — i.e. the chapter-only graph can now be used."""

    chapter_count: int
    """Number of Chapter rows already written for this novel."""

    next_chapter_number: int
    """``chapter_count + 1`` — the chapter a "Generate Next Chapter" run
    would produce."""


def get_novel_progress(novel_id: int) -> NovelProgress:
    """
    Return a cheap, read-only snapshot of a novel's generation progress.

    Used by the Streamlit UI to decide whether to show "Start Generation"
    (no setup yet) or "Generate Next Chapter" (setup complete), without
    duplicating pipeline-stage logic in the UI layer.
    """
    with Session(engine) as session:
        character_count = session.scalar(
            select(func.count()).select_from(Character).where(Character.novel_id == novel_id)
        ) or 0
        chapter_count = session.scalar(
            select(func.count()).select_from(Chapter).where(Chapter.novel_id == novel_id)
        ) or 0

    return NovelProgress(
        setup_complete=character_count > 0,
        chapter_count=chapter_count,
        next_chapter_number=chapter_count + 1,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def execute_graph(
    novel_id: int,
    initial_prompt: str = "",
) -> NovelState:
    """
    Execute the full NovelEngine setup + Chapter 1 pipeline for a given novel.

    Builds the canonical initial ``NovelState`` from the ``Novel`` SQLite row,
    optionally stores ``initial_prompt`` as the novel's synopsis so every
    agent node can read it, then awaits the compiled graph to completion.
    The graph always stops after Chapter 1 — call ``execute_next_chapter``
    to continue the story one chapter at a time thereafter.

    Parameters
    ----------
    novel_id:
        Primary key of an existing ``Novel`` row.  The row's ``genre``,
        ``tone``, and ``themes`` columns are read by each agent node — set
        them before calling this function (e.g. via the Streamlit sidebar).
    initial_prompt:
        Optional free-text description or creative brief.  Persisted to
        ``Novel.synopsis`` so it is available to all downstream agents.

    Returns
    -------
    NovelState
        Final accumulated state after all nodes have executed.

    Raises
    ------
    Exception
        Any unhandled exception raised by an agent node propagates here.
        The caller (background thread or CLI) is responsible for top-level
        error handling and logging.

    Example
    -------
    >>> import asyncio
    >>> final = asyncio.run(execute_graph(novel_id=1, initial_prompt="A story about..."))
    """
    # Persist the initial prompt as the novel synopsis when provided
    if initial_prompt:
        with Session(engine) as session:
            novel = session.get(Novel, novel_id)
            if novel:
                novel.synopsis = initial_prompt
                session.commit()
                logger.info(
                    "[execute_graph] persisted initial_prompt as synopsis for novel_id=%s",
                    novel_id,
                )

    initial_state: NovelState = {
        "novel_id":       novel_id,
        "world_context":  "",
        "creatures_list": [],
        "character_list": [],
        "current_outline": "",
        "current_draft":   "",
        "feedback_score":  0,
        "feedback_notes":  "",
        "revision_count":  0,
        "chapter_id":      0,
    }

    logger.info(
        "[execute_graph] starting pipeline for novel_id=%s (MAX_REVISION_LOOPS=%d)",
        novel_id,
        MAX_REVISION_LOOPS,
    )

    final_state: NovelState = await novel_graph.ainvoke(initial_state)  # type: ignore[assignment]

    logger.info(
        "[execute_graph] pipeline complete — novel_id=%s | revisions=%s | final_score=%s",
        novel_id,
        final_state.get("revision_count", 0),
        final_state.get("feedback_score", 0),
    )

    return final_state


async def execute_next_chapter(novel_id: int) -> NovelState:
    """
    Execute exactly one additional chapter for a novel whose setup (World,
    Creature, Character generation) is already complete.

    Starts from a fresh ``NovelState`` — ``world_context`` / ``creatures_list``
    /``character_list`` are intentionally left empty; ``plot_agent`` and the
    writing nodes fall back to targeted SQLite/ChromaDB reads for that
    context instead. This is the token-efficient path: it never re-runs
    World/Creature/Character generation, and pulls continuity from a compact
    "story so far" recap (``Chapter.summary`` rows) rather than replaying
    full prior chapters.

    Parameters
    ----------
    novel_id:
        Primary key of an existing ``Novel`` row that already has at least
        one ``Character`` row (see ``get_novel_progress``).

    Returns
    -------
    NovelState
        Final accumulated state after this chapter's nodes have executed.
    """
    initial_state: NovelState = {
        "novel_id":       novel_id,
        "world_context":  "",
        "creatures_list": [],
        "character_list": [],
        "current_outline": "",
        "current_draft":   "",
        "feedback_score":  0,
        "feedback_notes":  "",
        "revision_count":  0,
        "chapter_id":      0,
    }

    logger.info(
        "[execute_next_chapter] starting next-chapter run for novel_id=%s "
        "(MAX_REVISION_LOOPS=%d)",
        novel_id,
        MAX_REVISION_LOOPS,
    )

    final_state: NovelState = await chapter_graph.ainvoke(initial_state)  # type: ignore[assignment]

    logger.info(
        "[execute_next_chapter] chapter complete — novel_id=%s | revisions=%s | final_score=%s",
        novel_id,
        final_state.get("revision_count", 0),
        final_state.get("feedback_score", 0),
    )

    return final_state
