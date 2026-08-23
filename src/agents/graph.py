"""
LangGraph orchestration for NovelEngine.

Defines the NovelState, all agent node functions, and the compiled StateGraph
with conditional routing from the Red Team Auditor.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langgraph.graph import END, StateGraph

from src.agents.state import NovelState
from src.agents.nodes.world_and_creature import creature_architect, world_architect
from src.agents.nodes.planning import character_agent, plot_agent
from src.db.models import Novel, engine

logger = logging.getLogger(__name__)

# Maximum Scene Writer → Red Team revision loops before forcing prose_stylist.
# Prevents infinite cycles when the LLM consistently scores below the threshold.
MAX_REVISION_LOOPS: int = int(os.getenv("MAX_REVISION_LOOPS", "5"))


# ---------------------------------------------------------------------------
# Node functions (async placeholders)
# world_architect, creature_architect  → src.agents.nodes.world_and_creature
# character_agent, plot_agent          → src.agents.nodes.planning
# ---------------------------------------------------------------------------


# character_agent and plot_agent are imported from src.agents.nodes.planning


async def scene_writer(state: NovelState) -> dict[str, Any]:
    """Write chapter prose from the beat outline; increment revision counter."""
    logger.info(
        "[scene_writer] running for novel_id=%s (revision=%s)",
        state["novel_id"],
        state.get("revision_count", 0),
    )
    # TODO: build and invoke LLM chain using state["current_outline"]
    return {
        "current_draft": "",
        "revision_count": state.get("revision_count", 0) + 1,
    }


async def red_team(state: NovelState) -> dict[str, Any]:
    """Critique the scene draft against lore; produce a JSON score (1-10) and notes."""
    logger.info("[red_team] running for novel_id=%s", state["novel_id"])
    # TODO: build and invoke critique chain; parse score and notes from JSON output
    return {"feedback_score": 0, "feedback_notes": ""}


async def prose_stylist(state: NovelState) -> dict[str, Any]:
    """Apply final prose polish and voice consistency to the approved draft."""
    logger.info("[prose_stylist] running for novel_id=%s", state["novel_id"])
    # TODO: build and invoke LLM chain for stylistic refinement
    return {"current_draft": state.get("current_draft", "")}


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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def execute_graph(
    novel_id: int,
    initial_prompt: str = "",
) -> NovelState:
    """
    Execute the full NovelEngine generation pipeline for a given novel.

    Builds the canonical initial ``NovelState`` from the ``Novel`` SQLite row,
    optionally stores ``initial_prompt`` as the novel's synopsis so every
    agent node can read it, then awaits the compiled graph to completion.

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
    from sqlalchemy.orm import Session

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
