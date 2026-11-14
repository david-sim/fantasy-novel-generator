"""
pgvector-backed lore retrieval store.

Phase 1 of the architecture migration replaces ChromaDB (+ its EFS volume)
with a ``lore_embedding`` table in the same Supabase/Postgres database used
for structured data, via the ``pgvector`` extension. This module keeps the
exact same public function signatures the ChromaDB version exposed
(``add_lore``, ``search_lore``, ``get_lore_by_id``, ``delete_lore``,
``collection_count``, ``build_creature_document``, ``build_world_document``)
so agent node code (world_and_creature.py, planning.py, writing.py,
creature.py) did not need to change.

Usage
-----
    from app.db.vector_store import add_lore, search_lore, COLLECTION_CREATURES

    doc_id = add_lore(
        COLLECTION_CREATURES,
        text="The Vorath is a six-winged predator …",
        metadata={"novel_id": 1, "name": "Vorath", "threat_level": 8},
    )

    hits = search_lore(COLLECTION_CREATURES, "apex aerial predator", k=3, where={"novel_id": 1})
    for hit in hits:
        print(hit["text"], hit["distance"])

Configuration
-------------
    DATABASE_URL        — Postgres connection string (shared with app.db.models)
    OPENAI_API_KEY       — required to generate embeddings via OpenAIEmbeddings
    EMBEDDING_MODEL      — defaults to "text-embedding-3-small" (1536 dims)
"""

from __future__ import annotations

import logging
import os
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LoreEmbedding, engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ---------------------------------------------------------------------------
# Well-known collection names (unchanged from the ChromaDB version)
# Import these constants in agent nodes to avoid raw string typos.
# ---------------------------------------------------------------------------

COLLECTION_WORLD_LORE: str = "world_lore"       # geography, kingdoms, magic systems
COLLECTION_CREATURES: str  = "creatures"         # creature dossiers
COLLECTION_CHARACTERS: str = "characters"        # character dossiers
COLLECTION_PLOT_BEATS: str = "plot_beats"        # chapter outlines and scene beats


# ---------------------------------------------------------------------------
# Embeddings client
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_embeddings() -> Any:
    """Return a cached OpenAIEmbeddings client."""
    from langchain_openai import OpenAIEmbeddings  # type: ignore[import]

    return OpenAIEmbeddings(model=EMBEDDING_MODEL)


def _embed(text: str) -> list[float]:
    return _get_embeddings().embed_query(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_lore(
    collection_name: str,
    text: str,
    metadata: dict[str, Any],
    doc_id: str | None = None,
) -> str:
    """
    Embed and upsert a lore document into the specified collection.

    Parameters
    ----------
    collection_name:
        Target collection — use the module-level ``COLLECTION_*`` constants.
    text:
        The full document text that will be embedded.
    metadata:
        Key-value pairs stored alongside the embedding for filtering.
        Recommended keys: ``novel_id``, ``type``, ``name``.  ``novel_id`` is
        required so the row can be scoped/cascaded with its owning Novel.
    doc_id:
        Explicit document ID (UUIDv4 string).  If omitted a new UUID is
        generated automatically.  Pass an existing ID to *overwrite* a
        document in place (upsert semantics).

    Returns
    -------
    str
        The document ID.  Persist this in the ``chroma_doc_id`` column
        (SQLAlchemy model) so the document can be retrieved directly later.

    Raises
    ------
    ValueError
        If ``text`` is empty, or ``metadata`` is missing ``novel_id``.
    """
    if not text or not text.strip():
        raise ValueError("Cannot store an empty lore document.")

    novel_id = metadata.get("novel_id")
    if novel_id is None:
        raise ValueError("metadata must include 'novel_id' for pgvector storage.")

    if doc_id is None:
        doc_id = str(uuid.uuid4())

    embedding = _embed(text)

    with Session(engine) as session:
        with session.begin():
            existing = session.get(LoreEmbedding, doc_id)
            if existing is not None:
                existing.text = text
                existing.doc_metadata = metadata
                existing.embedding = embedding
                existing.collection_name = collection_name
            else:
                session.add(
                    LoreEmbedding(
                        id=doc_id,
                        novel_id=novel_id,
                        collection_name=collection_name,
                        text=text,
                        doc_metadata=metadata,
                        embedding=embedding,
                    )
                )

    logger.debug(
        "[pgvector] upserted doc_id=%s into collection='%s'", doc_id, collection_name
    )
    return doc_id


def search_lore(
    collection_name: str,
    query: str,
    k: int = 3,
    where: dict[str, Any] | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """
    Return the *k* most semantically similar lore documents.

    Parameters
    ----------
    collection_name:
        Collection to query.
    query:
        Free-text query embedded at search time.
    k:
        Maximum number of results.
    where:
        Optional metadata filter applied before ranking, e.g.
        ``{"novel_id": 1}``.  Only simple equality filters on top-level
        ``doc_metadata`` keys are supported (sufficient for agent node usage).
    max_chars:
        Token-efficiency guard.  When set, each returned ``text`` is hard-capped
        at this many characters (with a ``"… [truncated]"`` suffix when cut).
        Leave ``None`` (default) to return full document text unchanged — used
        by UI read paths that display lore to a human.  Agent nodes building
        LLM prompts should pass an explicit cap (e.g. 800) to bound worst-case
        prompt size regardless of how verbose a stored document is.

    Returns
    -------
    list[dict]
        Each result dict contains:

        ``id``       (str)   — document ID (matches ``chroma_doc_id`` in SQL)
        ``text``     (str)   — original document text
        ``metadata`` (dict)  — metadata dict stored with the document
        ``distance`` (float) — cosine distance; lower = more similar (range 0–2)

    Raises
    ------
    ValueError
        If ``query`` is empty.
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")

    query_embedding = _embed(query)

    with Session(engine) as session:
        stmt = (
            select(
                LoreEmbedding,
                LoreEmbedding.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .where(LoreEmbedding.collection_name == collection_name)
            .order_by("distance")
            .limit(k)
        )
        if where:
            for key, value in where.items():
                stmt = stmt.where(LoreEmbedding.doc_metadata[key].as_string() == str(value))

        rows = session.execute(stmt).all()

    results: list[dict[str, Any]] = []
    for row, distance in rows:
        text = row.text
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "… [truncated]"
        results.append(
            {
                "id": row.id,
                "text": text,
                "metadata": row.doc_metadata,
                "distance": float(distance),
            }
        )

    logger.debug(
        "[pgvector] search on collection='%s' returned %d/%d hits",
        collection_name,
        len(results),
        k,
    )
    return results


def get_lore_by_id(
    collection_name: str,
    doc_id: str,
) -> dict[str, Any] | None:
    """
    Fetch a single document by its ID.

    Returns ``None`` when the document is not found.
    Useful for hydrating SQL rows that carry a ``chroma_doc_id``.
    """
    with Session(engine) as session:
        row = session.get(LoreEmbedding, doc_id)
        if row is None or row.collection_name != collection_name:
            return None
        return {
            "id": row.id,
            "text": row.text,
            "metadata": row.doc_metadata,
        }


def delete_lore(collection_name: str, doc_id: str) -> None:
    """Remove a single document from the collection by its ID."""
    with Session(engine) as session:
        with session.begin():
            row = session.get(LoreEmbedding, doc_id)
            if row is not None and row.collection_name == collection_name:
                session.delete(row)
    logger.debug(
        "[pgvector] deleted doc_id=%s from collection='%s'", doc_id, collection_name
    )


def collection_count(collection_name: str) -> int:
    """Return the total number of documents stored in a collection."""
    with Session(engine) as session:
        return (
            session.query(LoreEmbedding)
            .filter(LoreEmbedding.collection_name == collection_name)
            .count()
        )


def build_creature_document(dossier: dict[str, Any]) -> str:
    """
    Assemble a rich plain-text document from a creature dossier dict.

    Used by the Creature Architect node to produce a single string that
    captures all facets of the creature for high-quality semantic retrieval.
    The format mirrors what a downstream agent (e.g. Scene Writer) would want
    to read back when asking "what dangerous beasts live in the northern wastes?".
    """
    return (
        f"CREATURE: {dossier.get('name', 'Unknown')}\n"
        f"Species Type: {dossier.get('species_type', '')}\n\n"
        f"ANATOMY\n{dossier.get('anatomy', '')}\n\n"
        f"MAGICAL PROPERTIES\n{dossier.get('magical_properties', '')}\n\n"
        f"ECOLOGICAL NICHE\n{dossier.get('ecological_niche', '')}\n\n"
        f"THREAT LEVEL: {dossier.get('threat_level', '')}/10\n\n"
        f"LORE\n{dossier.get('lore_description', '')}"
    ).strip()


def build_world_document(world_context: str, novel_id: int) -> str:
    """
    Wrap raw world-context prose in a structured header for storage.

    Used by the World Architect node.
    """
    return f"WORLD LORE (novel_id={novel_id})\n\n{world_context}".strip()
