"""
NovelEngine — Streamlit UI  (Epic 4)

Run with:
    streamlit run src/ui/app.py

Environment variables:
    DATABASE_URL   — SQLAlchemy connection string (default: sqlite:///./novelengine.db)
    LLM_PROVIDER   — openai | anthropic | google   (default: openai)
    LLM_MODEL      — model name                    (default: gpt-4o)
    LLM_TEMPERATURE — float                        (default: 0.9)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv

# Load .env before any src.* module is imported so that os.getenv() calls
# inside LangChain / SQLAlchemy constructors see the correct values.
load_dotenv()

import streamlit as st
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.db.models import AgentLog, Chapter, Character, Creature, Novel, engine
from src.db.init_db import init_db
from src.agents.graph import get_novel_progress
from src.ui.utils import (
    ThreadStatus,
    get_thread_status,
    launch_next_chapter,
    launch_novel_graph,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# One-time DB bootstrap (idempotent — safe to call on every cold start)
# ---------------------------------------------------------------------------

init_db()

# ---------------------------------------------------------------------------
# Page configuration  (must be the first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="NovelEngine",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session-state initialisation
# ---------------------------------------------------------------------------

_SESSION_DEFAULTS: dict[str, Any] = {
    "novel_id": None,        # int | None  — novel currently being generated
    "graph_thread": None,    # threading.Thread | None
    "graph_error": None,     # str | None  — top-level uncaught thread error
}

for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ---------------------------------------------------------------------------
# Background execution
# All threading / asyncio logic lives in src/ui/utils.py.
# app.py only stores the thread reference and polls thread.is_alive().
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Database helpers  (return plain dicts — avoids detached-instance errors)
# ---------------------------------------------------------------------------

def _create_novel(title: str, genre: str, tone: str, themes: str) -> int:
    """Insert a Novel row and return its auto-generated primary key."""
    with Session(engine) as session:
        novel = Novel(title=title, genre=genre, tone=tone, themes=themes)
        session.add(novel)
        session.commit()
        session.refresh(novel)
        return novel.id  # type: ignore[return-value]


def _fetch_logs(novel_id: int, limit: int = 60) -> list[dict[str, Any]]:
    """
    Return the most recent AgentLog rows for *novel_id* as plain dicts.

    All attribute access happens inside the session so there are no
    detached-instance issues after the session closes.
    """
    with Session(engine) as session:
        stmt = (
            select(AgentLog)
            .where(AgentLog.novel_id == novel_id)
            .order_by(desc(AgentLog.run_at))
            .limit(limit)
        )
        rows = session.scalars(stmt).all()
        return [
            {
                "id": row.id,
                "agent_name": row.agent_name,
                "status": row.status,
                "thoughts": row.thoughts,
                "input_summary": row.input_summary,
                "output_summary": row.output_summary,
                "error_message": row.error_message,
                "run_at": row.run_at,
            }
            for row in rows
        ]


def _fetch_recent_novels(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recently created novels as plain dicts."""
    with Session(engine) as session:
        stmt = (
            select(Novel)
            .order_by(desc(Novel.created_at))
            .limit(limit)
        )
        rows = session.scalars(stmt).all()
        return [
            {"id": row.id, "title": row.title, "genre": row.genre}
            for row in rows
        ]


def _fetch_creatures(novel_id: int) -> list[dict[str, Any]]:
    """Return Creature rows for *novel_id* as plain dicts."""
    with Session(engine) as session:
        stmt = (
            select(Creature)
            .where(Creature.novel_id == novel_id)
            .order_by(Creature.name)
        )
        rows = session.scalars(stmt).all()
        return [
            {
                "id":               row.id,
                "name":             row.name,
                "species_type":     row.species_type or "",
                "ecology":          row.ecology or "",
                "magical_traits":   row.magical_traits or "",
                "lore_description": row.lore_description or "",
            }
            for row in rows
        ]


def _fetch_characters(novel_id: int) -> list[dict[str, Any]]:
    """Return Character rows for *novel_id* as plain dicts."""
    with Session(engine) as session:
        stmt = (
            select(Character)
            .where(Character.novel_id == novel_id)
            .order_by(Character.name)
        )
        rows = session.scalars(stmt).all()
        return [
            {
                "id":          row.id,
                "name":        row.name,
                "role":        row.role or "",
                "backstory":   row.backstory or "",
                "personality": row.personality or "",
                "abilities":   row.abilities or "",
                "arc_summary": row.arc_summary or "",
            }
            for row in rows
        ]


def _fetch_chapters(novel_id: int) -> list[dict[str, Any]]:
    """Return Chapter rows for *novel_id* ordered by chapter_number, as plain dicts."""
    with Session(engine) as session:
        stmt = (
            select(Chapter)
            .where(Chapter.novel_id == novel_id)
            .order_by(Chapter.chapter_number)
        )
        rows = session.scalars(stmt).all()
        return [
            {
                "id":             row.id,
                "chapter_number": row.chapter_number,
                "title":          row.title or "",
                "beat_outline":   row.beat_outline or "",
                "content":        row.content or "",
                "red_team_score": row.red_team_score,   # int | None
                "red_team_notes": row.red_team_notes or "",
            }
            for row in rows
        ]


def _fetch_world_lore(novel_id: int, k: int = 50) -> list[dict[str, Any]]:
    """
    Query ChromaDB for world-lore documents attached to *novel_id*.

    Returns an empty list on any error so the UI degrades gracefully when
    ChromaDB is unavailable or the collection is empty.
    """
    try:
        from src.db.vector_store import COLLECTION_WORLD_LORE, search_lore  # lazy import

        return search_lore(
            COLLECTION_WORLD_LORE,
            "geography kingdoms magic systems history world overview",
            k=k,
            where={"novel_id": novel_id},
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "started":   "🔵",
    "running":   "🟡",
    "completed": "🟢",
    "error":     "🔴",
}

# Maps our DB status values to st.status() 'state' argument
_ST_STATUS_STATE: dict[str, str] = {
    "started":   "running",
    "running":   "running",
    "completed": "complete",
    "error":     "error",
}

# Per-agent accent colour (foreground) and background tint used inside cards.
# Keys match the agent_name values stored in AgentLog.
_AGENT_FG: dict[str, str] = {
    "world_architect":    "#1B5E20",   # deep green
    "creature_architect": "#BF360C",   # deep orange
    "character_agent":    "#0D47A1",   # deep blue
    "plot_agent":         "#4A148C",   # deep purple
    "scene_writer":       "#01579B",   # light-blue darken-4
    "red_team":           "#B71C1C",   # deep red
    "red_team_auditor":   "#B71C1C",   # deep red (alternate name)
    "prose_stylist":      "#004D40",   # teal darken-4
    "orchestrator":       "#263238",   # blue-grey darken-4
}

_AGENT_BG: dict[str, str] = {
    "world_architect":    "#F1F8E9",
    "creature_architect": "#FBE9E7",
    "character_agent":    "#E3F2FD",
    "plot_agent":         "#F3E5F5",
    "scene_writer":       "#E1F5FE",
    "red_team":           "#FFEBEE",
    "red_team_auditor":   "#FFEBEE",
    "prose_stylist":      "#E0F2F1",
    "orchestrator":       "#ECEFF1",
}

_AGENT_EMOJI: dict[str, str] = {
    "world_architect":    "🌍",
    "creature_architect": "🐉",
    "character_agent":    "🧙",
    "plot_agent":         "📋",
    "scene_writer":       "✍️",
    "red_team":           "🔴",
    "red_team_auditor":   "🔴",
    "prose_stylist":      "✨",
    "orchestrator":       "⚙️",
}


# ---------------------------------------------------------------------------
# Sidebar — "Novel Setup"
# ---------------------------------------------------------------------------

def _render_sidebar() -> None:
    with st.sidebar:
        st.title("📖 NovelEngine")
        st.caption("Multi-agent fantasy novel generator")
        st.divider()

        # ---- Novel setup form ----
        st.subheader("Create New Novel")
        with st.form("novel_setup_form", clear_on_submit=False):
            title = st.text_input(
                "Novel Title *",
                placeholder="The Shattered Throne",
            )
            genre = st.text_input("Genre", value="Epic Fantasy")
            tone = st.text_input(
                "Tone",
                placeholder="Dark, mythic, lyrical",
            )
            themes = st.text_area(
                "Themes",
                placeholder="Power and sacrifice, the cost of immortality, …",
                height=80,
            )
            initial_prompt = st.text_area(
                "Initial Creative Brief",
                placeholder=(
                    "Describe the story you want to tell. This is stored as the "
                    "novel's synopsis and read by every agent during generation.\n\n"
                    "Example: A disgraced general must unite three warring kingdoms "
                    "before an immortal tyrant reclaims the throne he was meant to destroy."
                ),
                height=120,
                help="Optional free-text brief. Agents will use this to ground their decisions.",
            )
            submitted = st.form_submit_button(
                "🚀 Start Generation",
                use_container_width=True,
            )

        if submitted:
            if not title.strip():
                st.error("Please enter a novel title.")
            elif get_thread_status(st.session_state.get("graph_thread")).state == "running":
                st.warning("A generation is already running. Wait for it to complete.")
            else:
                with st.spinner("Creating novel record…"):
                    novel_id = _create_novel(
                        title=title.strip(),
                        genre=genre.strip(),
                        tone=tone.strip(),
                        themes=themes.strip(),
                    )

                thread = launch_novel_graph(
                    novel_id=novel_id,
                    initial_prompt=initial_prompt.strip(),
                )
                st.session_state["novel_id"] = novel_id
                st.session_state["graph_thread"] = thread
                st.success(f"**{title}** queued! (ID: {novel_id})")
                st.rerun()

        # ---- Status badge ----
        st.divider()
        status: ThreadStatus = get_thread_status(
            thread=st.session_state.get("graph_thread"),
            novel_id=st.session_state.get("novel_id"),
        )
        if status.state == "running":
            st.warning(f"⚙️ Generating novel ID {status.novel_id}…")
        elif status.state == "error":
            st.error(f"Generation failed:\n\n{status.last_error}")
        elif status.state == "complete":
            st.success("✅ Generation complete.")
        else:
            st.info("No active generation.")

        # ---- Past novels ----
        st.divider()
        st.subheader("Past Novels")
        past = _fetch_recent_novels()
        if not past:
            st.caption("None yet.")
        else:
            for novel in past:
                label = f"#{novel['id']} — {novel['title']}"
                if st.button(label, key=f"load_novel_{novel['id']}", use_container_width=True):
                    st.session_state["novel_id"] = novel["id"]
                    st.session_state["graph_thread"] = None
                    st.rerun()


# ---------------------------------------------------------------------------
# Tab: Agent Live Monitor
# ---------------------------------------------------------------------------


@st.fragment(run_every=2)
def _render_live_monitor() -> None:
    """
    Agent Live Monitor tab — Story 4.2.

    Decorated with ``@st.fragment(run_every=2)`` so only this component
    re-renders every 2 seconds; the rest of the page (sidebar, other tabs)
    is untouched.  When generation is idle the fragment still polls but the
    DB query is a trivial indexed read.

    Layout
    ------
    1. Header row with novel ID, LIVE / IDLE badge, and last-refresh time.
    2. Summary metrics strip: total events, completed, errors, active.
    3. Agent log cards — one ``st.status()`` card per AgentLog row, ordered
       newest-first, each colour-coded by agent name.
    """
    novel_id: Optional[int] = st.session_state.get("novel_id")
    status: ThreadStatus = get_thread_status(
        thread=st.session_state.get("graph_thread"),
        novel_id=novel_id,
    )
    is_running = status.state == "running"

    # --- Header -----------------------------------------------------------
    hcol, badge_col, ts_col = st.columns([3, 1, 2])
    hcol.subheader("🔍 Agent Live Monitor")

    if is_running:
        badge_col.markdown(
            '<p style="color:#E65100;font-weight:700;margin-top:0.9rem;">● LIVE</p>',
            unsafe_allow_html=True,
        )
    elif novel_id is not None:
        badge_col.markdown(
            '<p style="color:#78909C;margin-top:0.9rem;">● IDLE</p>',
            unsafe_allow_html=True,
        )

    ts_col.caption(
        f"Last refresh: {datetime.now().strftime('%H:%M:%S')}"
        + (f"  |  Novel ID: **{novel_id}**" if novel_id else "")
    )

    if novel_id is None:
        st.info("No novel selected. Use the sidebar to start or load a generation.")
        return

    # --- Chapter control ----------------------------------------------------
    # Chapter-by-chapter generation: the initial run (sidebar "Start
    # Generation") always stops after Chapter 1. Every subsequent chapter is
    # produced one at a time from this button, which skips World/Creature/
    # Character generation entirely (see execute_next_chapter).
    progress = get_novel_progress(novel_id)
    cc1, cc2 = st.columns([3, 1])
    if not progress.setup_complete:
        cc1.warning(
            "📖 World, creatures, and characters haven't been generated yet "
            "for this novel — likely because a previous run was interrupted "
            "before finishing setup."
        )
        if is_running:
            cc2.button(
                "⏳ Generating…",
                disabled=True,
                use_container_width=True,
                key="resume_setup_btn_running",
            )
        else:
            if cc2.button(
                "▶️ Generate Chapter 1",
                use_container_width=True,
                key="resume_setup_btn",
            ):
                thread = launch_novel_graph(novel_id=novel_id, initial_prompt="")
                st.session_state["graph_thread"] = thread
                st.rerun()
    else:
        cc1.markdown(
            f"📚 **{progress.chapter_count}** chapter"
            f"{'s' if progress.chapter_count != 1 else ''} written so far."
        )
        if is_running:
            cc2.button(
                f"⏳ Writing Chapter {progress.next_chapter_number}…",
                disabled=True,
                use_container_width=True,
            )
        else:
            if cc2.button(
                f"▶️ Generate Chapter {progress.next_chapter_number}",
                use_container_width=True,
                key="generate_next_chapter_btn",
            ):
                thread = launch_next_chapter(novel_id=novel_id)
                st.session_state["graph_thread"] = thread
                st.rerun()
    st.divider()

    logs = _fetch_logs(novel_id)

    # --- Summary metrics strip --------------------------------------------
    total     = len(logs)
    completed = sum(1 for l in logs if l["status"] == "completed")
    errors    = sum(1 for l in logs if l["status"] == "error")
    active    = sum(1 for l in logs if l["status"] in ("started", "running"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Events",  total)
    m2.metric("✅ Completed",  completed)
    m3.metric("🔴 Errors",     errors,  delta=errors  or None, delta_color="inverse")
    m4.metric("⚙️ Active",    active,  delta=active  or None)

    if not logs:
        st.info("Waiting for agent activity…")
        return

    st.divider()

    # --- Agent log cards --------------------------------------------------
    for log in logs:
        agent_name: str = log["agent_name"]
        agent_emoji = _AGENT_EMOJI.get(agent_name, "🤖")
        agent_display = agent_name.replace("_", " ").title()
        fg     = _AGENT_FG.get(agent_name, "#37474F")
        bg     = _AGENT_BG.get(agent_name, "#ECEFF1")
        icon   = _STATUS_ICON.get(log["status"], "⚪")
        st_state = _ST_STATUS_STATE.get(log["status"], "running")
        auto_expand = log["status"] == "error"

        # st.status() label: status icon + emoji + agent name
        label = f"{icon} {agent_emoji} {agent_display}"

        with st.status(label, state=st_state, expanded=auto_expand):

            # Coloured agent pill ----------------------------------------
            st.markdown(
                f'<div style="'
                f"display:inline-flex;align-items:center;gap:6px;"
                f"background:{bg};border-left:4px solid {fg};"
                f"padding:5px 14px;border-radius:6px;margin-bottom:10px;"
                f'">'  
                f'<span style="color:{fg};font-weight:700;font-size:0.875rem;">'  
                f"{agent_emoji} {agent_display}"
                f"</span>"
                f'<span style="color:{fg};opacity:0.7;font-size:0.8rem;">'
                f"({log['status'].upper()})"
                f"</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Timestamp + log ID -----------------------------------------
            meta_l, meta_r = st.columns(2)
            run_at = log["run_at"]
            run_at_str = (
                run_at.strftime("%Y-%m-%d  %H:%M:%S")
                if isinstance(run_at, datetime)
                else str(run_at)
            )
            meta_l.caption(f"🕐 {run_at_str}")
            meta_r.caption(f"Log ID: {log['id']}")

            # Content sections -------------------------------------------
            if log["thoughts"]:
                st.markdown(
                    f'<span style="color:{fg};font-weight:600;">💭 Thoughts / Scratchpad</span>',
                    unsafe_allow_html=True,
                )
                st.code(log["thoughts"], language=None)

            if log["input_summary"]:
                st.markdown(
                    f'<span style="color:{fg};font-weight:600;">📥 Input Summary</span>',
                    unsafe_allow_html=True,
                )
                st.text(log["input_summary"])

            if log["output_summary"]:
                st.markdown(
                    f'<span style="color:{fg};font-weight:600;">📤 Output Summary</span>',
                    unsafe_allow_html=True,
                )
                # Try to pretty-print if the summary is valid JSON
                try:
                    import json
                    parsed = json.loads(log["output_summary"])
                    st.json(parsed, expanded=False)
                except (ValueError, TypeError):
                    st.text(log["output_summary"])

            if log["error_message"]:
                st.error(f"**Error:** {log['error_message']}")


# ---------------------------------------------------------------------------
# Tab: Bestiary & Lore
# ---------------------------------------------------------------------------


def _render_bestiary() -> None:
    """
    Bestiary & Lore tab — Story 4.3.

    Sections
    --------
    1. Creatures  — SQLite Creature rows as an expandable card grid (3 columns).
    2. Characters — SQLite Character rows as expandable dual-column cards.
    3. World Lore — ChromaDB world_lore documents grouped by metadata ``type``.
    """
    novel_id: Optional[int] = st.session_state.get("novel_id")

    if novel_id is None:
        st.info("No novel selected. Use the sidebar to start or load a generation.")
        return

    creatures  = _fetch_creatures(novel_id)
    characters = _fetch_characters(novel_id)
    world_lore = _fetch_world_lore(novel_id)

    # ── Creatures ─────────────────────────────────────────────────────────────
    st.subheader(f"🐉 Creatures  ({len(creatures)})")
    if not creatures:
        st.info("No creatures generated yet — the Creature Architect has not run.")
    else:
        num_cols = min(3, len(creatures))
        cols = st.columns(num_cols)
        for i, c in enumerate(creatures):
            with cols[i % num_cols]:
                species_badge = f"  `{c['species_type']}`" if c["species_type"] else ""
                with st.expander(f"**{c['name']}**{species_badge}", expanded=False):
                    if c["ecology"]:
                        st.markdown("**🌿 Ecology**")
                        st.write(c["ecology"])
                    if c["magical_traits"]:
                        st.markdown("**✨ Magical Traits**")
                        st.write(c["magical_traits"])
                    if c["lore_description"]:
                        st.markdown("**📜 Lore**")
                        st.write(c["lore_description"])

    st.divider()

    # ── Characters ────────────────────────────────────────────────────────────
    st.subheader(f"🧙 Characters  ({len(characters)})")
    if not characters:
        st.info("No characters generated yet — the Character Agent has not run.")
    else:
        for char in characters:
            role_badge = f"  `{char['role']}`" if char["role"] else ""
            with st.expander(f"**{char['name']}**{role_badge}", expanded=False):
                left, right = st.columns(2)
                with left:
                    if char["backstory"]:
                        st.markdown("**📖 Backstory**")
                        st.write(char["backstory"])
                    if char["personality"]:
                        st.markdown("**🎭 Personality**")
                        st.write(char["personality"])
                with right:
                    if char["abilities"]:
                        st.markdown("**⚔️ Abilities**")
                        st.write(char["abilities"])
                    if char["arc_summary"]:
                        st.markdown("**🌀 Character Arc**")
                        st.write(char["arc_summary"])

    st.divider()

    # ── World Lore (ChromaDB) ─────────────────────────────────────────────────
    st.subheader("🌍 World Lore")
    if not world_lore:
        st.info(
            "No world lore in the vector store yet — "
            "the World Architect agent populates this section."
        )
    else:
        # Group documents by their metadata 'type' field so the reader can
        # navigate by category (kingdom, magic_rule, history, world_overview, …)
        lore_by_type: dict[str, list[dict[str, Any]]] = {}
        for doc in world_lore:
            doc_type = (doc.get("metadata") or {}).get("type", "general")
            lore_by_type.setdefault(doc_type, []).append(doc)

        _LORE_ICON: dict[str, str] = {
            "kingdom":        "🏰",
            "magic_rule":     "⚗️",
            "history":        "📜",
            "world_overview": "🌐",
            "general":        "📄",
        }
        for lore_type, docs in sorted(lore_by_type.items()):
            icon = _LORE_ICON.get(lore_type, "📄")
            type_label = lore_type.replace("_", " ").title()
            count_label = "entry" if len(docs) == 1 else "entries"
            with st.expander(
                f"{icon} **{type_label}** ({len(docs)} {count_label})",
                expanded=lore_type in ("world_overview", "kingdom"),
            ):
                for j, doc in enumerate(docs):
                    doc_name = (doc.get("metadata") or {}).get("name", f"Entry {j + 1}")
                    st.markdown(f"##### {doc_name}")
                    st.write(doc.get("text", ""))
                    if j < len(docs) - 1:
                        st.divider()


# ---------------------------------------------------------------------------
# Tab: Manuscript Reader
# ---------------------------------------------------------------------------


def _render_manuscript() -> None:
    """
    Manuscript Reader tab — Story 4.4.

    Renders one Chapter at a time (paginated) rather than the whole
    manuscript in a single scroll, so that:
    - Novels with many chapters stay fast to render (no giant DOM / text blob).
    - Every chapter remains individually reachable via Previous/Next
      buttons or the "jump to chapter" selector.

    For the selected Chapter row:
    - Header: chapter title + colour-coded Red Team score badge.
    - Left column (3/4): polished prose rendered as Markdown.
      Falls back to beat outline when Scene Writer has not yet run.
    - Right column (1/4): numeric score gauge + progress bar + critique notes.
    """
    novel_id: Optional[int] = st.session_state.get("novel_id")

    if novel_id is None:
        st.info("No novel selected. Use the sidebar to start or load a generation.")
        return

    chapters = _fetch_chapters(novel_id)

    if not chapters:
        st.info(
            "No chapters generated yet. "
            "Complete the full pipeline (World → Creature → Character → Plot → Scene) "
            "to produce manuscript content."
        )
        return

    total = len(chapters)

    # --- Pagination state -------------------------------------------------
    # ``page_key`` is the single logical "current chapter index". The
    # selectbox is bound to its own widget key and kept in sync via
    # on_change/on_click callbacks — callbacks are the only reliable way to
    # change a widget's displayed value in Streamlit; mutating
    # st.session_state directly in the script body (then calling st.rerun())
    # does NOT reliably update an already-instantiated widget with the same
    # key, which is why Previous/Next previously appeared to do nothing.
    page_key = f"manuscript_chapter_idx_{novel_id}"
    select_key = f"{page_key}_select"
    if (
        page_key not in st.session_state
        or select_key not in st.session_state
        or st.session_state[page_key] >= total
    ):
        st.session_state[page_key] = total - 1  # default: most recently written chapter
        st.session_state[select_key] = st.session_state[page_key]

    def _chapter_label(i: int) -> str:
        c = chapters[i]
        label = f"Chapter {c['chapter_number']}"
        if c["title"]:
            label += f": {c['title']}"
        return label

    def _go_prev() -> None:
        new_idx = max(0, st.session_state[page_key] - 1)
        st.session_state[page_key] = new_idx
        st.session_state[select_key] = new_idx

    def _go_next() -> None:
        new_idx = min(total - 1, st.session_state[page_key] + 1)
        st.session_state[page_key] = new_idx
        st.session_state[select_key] = new_idx

    def _on_select_change() -> None:
        st.session_state[page_key] = st.session_state[select_key]

    nav_prev, nav_select, nav_next = st.columns([1, 4, 1])

    with nav_prev:
        st.button(
            "⬅ Previous",
            use_container_width=True,
            disabled=st.session_state[page_key] == 0,
            key=f"manuscript_prev_{novel_id}",
            on_click=_go_prev,
        )

    with nav_next:
        st.button(
            "Next ➡",
            use_container_width=True,
            disabled=st.session_state[page_key] == total - 1,
            key=f"manuscript_next_{novel_id}",
            on_click=_go_next,
        )

    with nav_select:
        st.selectbox(
            "Jump to chapter",
            options=list(range(total)),
            format_func=_chapter_label,
            key=select_key,
            on_change=_on_select_change,
            label_visibility="collapsed",
        )

    st.caption(f"Chapter {st.session_state[page_key] + 1} of {total}")
    st.divider()

    ch = chapters[st.session_state[page_key]]
    score: Optional[int] = ch["red_team_score"]

    # Colour-coded score badge
    if score is None:
        badge_bg, badge_label = "#9E9E9E", "No score"
    elif score >= 8:
        badge_bg, badge_label = "#43A047", f"✅ {score}/10"
    elif score >= 5:
        badge_bg, badge_label = "#FB8C00", f"⚠️ {score}/10"
    else:
        badge_bg, badge_label = "#E53935", f"❌ {score}/10"

    chapter_heading = f"Chapter {ch['chapter_number']}"
    if ch["title"]:
        chapter_heading += f": {ch['title']}"

    st.markdown(
        f"### {chapter_heading}"
        f"&ensp;<span style=\""
        f"background:{badge_bg};color:#fff;"
        f"padding:2px 12px;border-radius:12px;"
        f"font-size:0.8rem;vertical-align:middle;\""
        f">{badge_label}</span>",
        unsafe_allow_html=True,
    )

    prose_col, notes_col = st.columns([3, 1])

    with prose_col:
        st.caption("📝 Manuscript")
        if ch["content"]:
            st.markdown(ch["content"])
        elif ch["beat_outline"]:
            st.caption("*(Scene Writer has not run — showing beat outline only)*")
            st.markdown(ch["beat_outline"])
        else:
            st.caption("*No content yet.*")

    with notes_col:
        st.caption("🔴 Red Team")
        if score is not None:
            st.markdown(
                f'<div style="text-align:center;padding:8px 0 4px;">'
                f'<span style="font-size:2.8rem;font-weight:800;color:{badge_bg};">'
                f"{score}</span>"
                f'<span style="font-size:1rem;color:#90A4AE;">/10</span>'
                f"</div>",
                unsafe_allow_html=True,
            )
            st.progress(score / 10)
        if ch["red_team_notes"]:
            st.write(ch["red_team_notes"])
        else:
            st.caption("*No critique notes.*")


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------

_render_sidebar()

tab_monitor, tab_bestiary, tab_manuscript = st.tabs(
    ["🔍 Live Monitor", "🐉 Bestiary & Lore", "📜 Manuscript"]
)

with tab_monitor:
    _render_live_monitor()

with tab_bestiary:
    _render_bestiary()

with tab_manuscript:
    _render_manuscript()
