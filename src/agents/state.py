"""
Canonical LangGraph state definition for NovelEngine.

Import NovelState from here — never from graph.py — so that agent nodes,
the Streamlit UI, and the graph wiring all share a single source of truth.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class NovelState(TypedDict):
    """
    Shared mutable state threaded through every node in the LangGraph pipeline.

    Fields
    ------
    novel_id:
        Primary key of the active ``Novel`` row in SQLite.  All agent nodes
        use this to scope their DB writes.
    world_context:
        Formatted plain-text summary produced by the World Architect node.
        Contains geography, kingdoms, magic rules, history, and cosmology.
    creatures_list:
        List of creature dossier dicts produced by the Creature Architect.
        Each dict matches the ``CreatureDossier`` Pydantic schema and
        carries a ``chroma_doc_id`` key after ChromaDB persistence.
    character_list:
        List of character dossier dicts produced by the Character Agent.
        Each dict carries name, role, backstory, abilities, and arc_summary.
    current_outline:
        Beat-level chapter outline produced by the Plot Agent.
        Consumed by the Scene Writer to generate prose.
    current_draft:
        Generated markdown prose for the current chapter.
        Produced by the Scene Writer; revised on Red Team feedback loops.
    feedback_score:
        Integer quality score (1–10) assigned by the Red Team Auditor.
        The conditional router sends the draft back to Scene Writer if < 8.
    feedback_notes:
        Prose revision notes from the Red Team Auditor.
    revision_count:
        Number of Scene Writer → Red Team loops completed for the current
        chapter.  Useful for imposing a maximum revision cap.
    """

    novel_id: int
    world_context: str
    creatures_list: list[dict[str, Any]]
    character_list: list[dict[str, Any]]
    current_outline: str
    current_draft: str
    feedback_score: int
    feedback_notes: str
    revision_count: int
