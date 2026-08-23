"""
src/ui/utils.py — Background execution utilities for the NovelEngine Streamlit UI.

Streamlit re-runs the entire script on every user interaction, so long-running
async work must be offloaded to a dedicated OS thread.  This module owns that
concern so ``app.py`` stays clean.

Public API
----------
launch_novel_graph(novel_id, initial_prompt) -> threading.Thread
    Starts the full generation pipeline in a daemon thread and returns it so
    the caller (app.py) can store it in ``st.session_state`` and poll
    ``thread.is_alive()`` to track status.

ThreadStatus (NamedTuple)
    Convenience return type for ``get_thread_status(thread)``.

get_thread_status(thread) -> ThreadStatus
    Returns a structured status snapshot without touching session state.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Literal, NamedTuple, Optional

from sqlalchemy.orm import Session

from src.agents.graph import execute_graph
from src.db.models import AgentLog, engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RunState = Literal["idle", "running", "complete", "error"]


class ThreadStatus(NamedTuple):
    """Snapshot of a graph-execution thread's current state."""

    state: RunState
    """One of: 'idle' | 'running' | 'complete' | 'error'."""

    novel_id: Optional[int]
    """The novel being generated, or None if no thread has been launched."""

    last_error: Optional[str]
    """The most recent orchestrator-level error message, if any."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _persist_orchestrator_error(novel_id: int, exc: Exception) -> None:
    """
    Write a top-level pipeline failure to ``AgentLog`` as an
    ``orchestrator / error`` entry so the Live Monitor can surface it.

    This is a best-effort write; any secondary failure is logged and swallowed
    so the original exception is not masked.
    """
    try:
        with Session(engine) as session:
            session.add(
                AgentLog(
                    novel_id=novel_id,
                    agent_name="orchestrator",
                    status="error",
                    error_message=str(exc),
                )
            )
            session.commit()
        logger.debug(
            "[utils] persisted orchestrator error for novel_id=%s", novel_id
        )
    except Exception:
        logger.exception(
            "[utils] failed to persist orchestrator error for novel_id=%s", novel_id
        )


def _graph_thread_target(novel_id: int, initial_prompt: str) -> None:
    """
    Thread target function.

    ``asyncio.run()`` creates a *fresh* event loop inside this thread so there
    is no contention with Streamlit's main-thread loop.  Any unhandled
    exception is persisted to ``AgentLog`` before the thread exits, keeping
    the Live Monitor in sync.
    """
    logger.info(
        "[bg_thread] starting execute_graph for novel_id=%s", novel_id
    )
    try:
        asyncio.run(execute_graph(novel_id=novel_id, initial_prompt=initial_prompt))
        logger.info(
            "[bg_thread] execute_graph completed for novel_id=%s", novel_id
        )
    except Exception as exc:
        logger.exception(
            "[bg_thread] unhandled error in pipeline for novel_id=%s", novel_id
        )
        _persist_orchestrator_error(novel_id, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def launch_novel_graph(
    novel_id: int,
    initial_prompt: str = "",
) -> threading.Thread:
    """
    Run the NovelEngine generation pipeline in a background daemon thread.

    The thread calls ``execute_graph(novel_id, initial_prompt)`` via
    ``asyncio.run()``, which is safe to call from a non-async context.

    Parameters
    ----------
    novel_id:
        Primary key of an existing ``Novel`` row.  Genre, tone, and themes
        must already be set on the row; ``execute_graph`` reads them.
    initial_prompt:
        Optional free-text creative brief stored as ``Novel.synopsis`` before
        the pipeline begins.  Visible to every agent that queries the Novel row.

    Returns
    -------
    threading.Thread
        The started daemon thread.  Store in ``st.session_state["graph_thread"]``
        and poll ``thread.is_alive()`` to track run status across Streamlit reruns.

    Notes
    -----
    - The thread is a **daemon**, so it is killed automatically if the
      Streamlit worker process exits — no zombie threads on crash.
    - Thread-to-UI communication goes through SQLite (``AgentLog`` rows),
      never through ``st.session_state``, which is not thread-safe for writes
      from non-main threads.
    """
    thread = threading.Thread(
        target=_graph_thread_target,
        args=(novel_id, initial_prompt),
        name=f"novel-graph-{novel_id}",
        daemon=True,
    )
    thread.start()
    logger.info(
        "[utils] launched background thread '%s' for novel_id=%s",
        thread.name,
        novel_id,
    )
    return thread


def get_thread_status(
    thread: Optional[threading.Thread],
    novel_id: Optional[int] = None,
) -> ThreadStatus:
    """
    Return a structured status snapshot for the given thread.

    Queries ``AgentLog`` for the most recent orchestrator error so the caller
    does not have to duplicate that logic.

    Parameters
    ----------
    thread:
        The thread returned by ``launch_novel_graph``, or ``None`` if no run
        has been started this session.
    novel_id:
        Used to look up the latest error in ``AgentLog``.  Pass ``None`` to
        skip the DB query (error will always be ``None`` in the result).

    Returns
    -------
    ThreadStatus
        ``.state``      — "idle" | "running" | "complete" | "error"
        ``.novel_id``   — the novel being generated
        ``.last_error`` — most recent error message, or ``None``
    """
    if thread is None:
        return ThreadStatus(state="idle", novel_id=novel_id, last_error=None)

    if thread.is_alive():
        return ThreadStatus(state="running", novel_id=novel_id, last_error=None)

    # Thread has finished — check AgentLog for errors
    last_error: Optional[str] = None
    if novel_id is not None:
        try:
            from sqlalchemy import desc, select

            with Session(engine) as session:
                row = session.scalars(
                    select(AgentLog)
                    .where(
                        AgentLog.novel_id == novel_id,
                        AgentLog.status == "error",
                    )
                    .order_by(desc(AgentLog.run_at))
                    .limit(1)
                ).first()
                if row:
                    last_error = row.error_message
        except Exception:
            logger.exception(
                "[utils] failed to fetch last error for novel_id=%s", novel_id
            )

    state: RunState = "error" if last_error else "complete"
    return ThreadStatus(state=state, novel_id=novel_id, last_error=last_error)
