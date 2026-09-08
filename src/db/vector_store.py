"""
Epic 1.3 — ChromaDB vector store wrapper.

Provides a thin, thread-safe interface that every agent node can import to
persist and retrieve lore context without managing ChromaDB internals.

Usage
-----
    from src.db.vector_store import add_lore, search_lore, COLLECTION_CREATURES

    doc_id = add_lore(
        COLLECTION_CREATURES,
        text="The Vorath is a six-winged predator …",
        metadata={"novel_id": 1, "name": "Vorath", "threat_level": 8},
    )

    hits = search_lore(COLLECTION_CREATURES, "apex aerial predator", k=3)
    for hit in hits:
        print(hit["text"], hit["distance"])

Configuration
-------------
    CHROMA_PATH        — filesystem path for the persistent store
                         (default: ./chroma_db)
    ANONYMIZED_TELEMETRY=False — set in the environment to disable ChromaDB
                                  telemetry (ChromaDB also respects this natively)
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHROMA_PATH: str = os.getenv("CHROMA_PATH", "./chroma_db")

# ---------------------------------------------------------------------------
# Well-known collection names
# Import these constants in agent nodes to avoid raw string typos.
# ---------------------------------------------------------------------------

COLLECTION_WORLD_LORE: str = "world_lore"       # geography, kingdoms, magic systems
COLLECTION_CREATURES: str  = "creatures"         # creature dossiers
COLLECTION_CHARACTERS: str = "characters"        # character dossiers
COLLECTION_PLOT_BEATS: str = "plot_beats"        # chapter outlines and scene beats

# ---------------------------------------------------------------------------
# Module-level singleton client  (double-checked locking for thread safety)
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client: chromadb.PersistentClient | None = None  # type: ignore[type-arg]


def _get_client() -> chromadb.PersistentClient:  # type: ignore[type-arg]
    """Return the shared PersistentClient, creating it on first call."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # second check under the lock
                logger.info("[chroma] initialising PersistentClient at '%s'", CHROMA_PATH)
                os.makedirs(CHROMA_PATH, exist_ok=True)
                _client = chromadb.PersistentClient(
                    path=CHROMA_PATH,
                    settings=Settings(anonymized_telemetry=False),
                )
    return _client


def _get_collection(name: str) -> chromadb.Collection:
    """Return the named collection, creating it if it does not exist."""
    return _get_client().get_or_create_collection(
        name=name,
        # Cosine similarity is better than L2 for comparing textual embeddings
        metadata={"hnsw:space": "cosine"},
    )


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
        All values must be ``str``, ``int``, ``float``, or ``bool``.
        Recommended keys: ``novel_id``, ``type``, ``name``.
    doc_id:
        Explicit document ID (UUIDv4 string).  If omitted a new UUID is
        generated automatically.  Pass an existing ID to *overwrite* a
        document in place (upsert semantics).

    Returns
    -------
    str
        The document ID.  Persist this in the SQLite ``chroma_doc_id``
        column so the document can be retrieved directly later.

    Raises
    ------
    ValueError
        If ``text`` is empty.
    """
    if not text or not text.strip():
        raise ValueError("Cannot store an empty lore document.")

    if doc_id is None:
        doc_id = str(uuid.uuid4())

    _get_collection(collection_name).upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata],
    )
    logger.debug(
        "[chroma] upserted doc_id=%s into collection='%s'", doc_id, collection_name
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
        Maximum number of results.  Automatically clamped to the collection
        size so an empty-collection query never raises.
    where:
        Optional ChromaDB metadata filter applied before ranking, e.g.
        ``{"novel_id": 1}`` or ``{"$and": [{"novel_id": 1}, {"type": "creature"}]}``.
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

        ``id``       (str)   — document ID (matches ``chroma_doc_id`` in SQLite)
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

    collection = _get_collection(collection_name)
    count = collection.count()
    if count == 0:
        logger.debug("[chroma] collection='%s' is empty — returning []", collection_name)
        return []

    n_results = min(k, count)
    kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    raw = collection.query(**kwargs)

    ids:       list[str]            = (raw.get("ids")       or [[]])[0]
    documents: list[str]            = (raw.get("documents") or [[]])[0]
    metadatas: list[dict[str, Any]] = (raw.get("metadatas") or [[]])[0]
    distances: list[float]          = (raw.get("distances") or [[]])[0]

    if max_chars is not None:
        documents = [
            doc if len(doc) <= max_chars else doc[:max_chars].rstrip() + "… [truncated]"
            for doc in documents
        ]

    results = [
        {
            "id":       doc_id,
            "text":     text,
            "metadata": meta,
            "distance": dist,
        }
        for doc_id, text, meta, dist in zip(ids, documents, metadatas, distances)
    ]

    logger.debug(
        "[chroma] search on collection='%s' returned %d/%d hits",
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
    Useful for hydrating SQLite rows that carry a ``chroma_doc_id``.
    """
    raw = _get_collection(collection_name).get(
        ids=[doc_id],
        include=["documents", "metadatas"],
    )
    ids:       list[str]            = raw.get("ids")       or []
    documents: list[str]            = raw.get("documents") or []
    metadatas: list[dict[str, Any]] = raw.get("metadatas") or []

    if not ids:
        return None

    return {
        "id":       ids[0],
        "text":     documents[0] if documents else "",
        "metadata": metadatas[0] if metadatas else {},
    }


def delete_lore(collection_name: str, doc_id: str) -> None:
    """Remove a single document from the collection by its ID."""
    _get_collection(collection_name).delete(ids=[doc_id])
    logger.debug(
        "[chroma] deleted doc_id=%s from collection='%s'", doc_id, collection_name
    )


def collection_count(collection_name: str) -> int:
    """Return the total number of documents stored in a collection."""
    return _get_collection(collection_name).count()


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
