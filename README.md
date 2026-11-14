# NovelEngine

A multi-agent generative AI system that autonomously writes publication-grade
fantasy novels. Powered by **LangGraph** for agent orchestration and
**LangChain** for LLM calls.

> **Architecture migration in progress.** This repo is moving from a
> single-container Streamlit + SQLite + ChromaDB app to a monorepo of a
> **FastAPI backend** + **Next.js frontend** on **Supabase** (Postgres +
> pgvector + Auth + Storage). The full proposal is tracked in
> `docs/architecture-redesign.md` (see PR #3 — docs-only, not yet merged).
> This document describes the **new** architecture as of **Phase 1** of that
> migration (scaffold, auth, persistence). Phase 2 (async job pipeline) and
> Phase 3 (export + polish) land in follow-up PRs — see
> [Migration status](#migration-status) below.

---

## Architecture

```
┌────────────────────────────┐        ┌──────────────────────────────────────┐
│   Next.js (Vercel)         │  HTTPS │   FastAPI (Render / Fly.io)           │
│   App Router + TypeScript  │ ─────▶ │   backend/app/main.py                 │
│   Supabase Auth (Google)   │  Bearer│                                        │
│   /login, /novels,         │  JWT   │   LangGraph pipeline (unchanged):     │
│   /novels/[id]             │ ◀───── │   WorldArchitect → CreatureArchitect  │
└────────────┬───────────────┘        │   → CharacterAgent → PlotAgent →      │
             │                        │   SceneWriter ⇄ RedTeamAuditor →      │
             │ Supabase session       │   ProseStylist                        │
             ▼                        └───────────────┬───────────────────────┘
┌────────────────────────────────────────────────────────────────────────────┐
│                                Supabase                                    │
│  Auth (Google OAuth)   │   Postgres (Novel/Chapter/Character/Creature)     │
│                        │   pgvector (lore embeddings, replaces ChromaDB)   │
│                        │   Storage (future: generated exports)            │
└────────────────────────────────────────────────────────────────────────────┘
```

The LangGraph agent pipeline itself is **unchanged** by this migration — only
how it's invoked (FastAPI instead of Streamlit callbacks) and where it
persists data (Postgres/pgvector via Supabase instead of SQLite/ChromaDB)
have changed.

**Phase 1 (this PR):** the `/api/novels/{id}/generate` endpoint calls
`execute_graph()` **synchronously** — the HTTP request blocks until
generation finishes. **Phase 2** replaces this with an async job queued via
Upstash Redis + RQ, with the frontend polling for status instead of blocking
on the request.

---

## Project Structure

```
fantasy-novel-generator/
├── backend/                      # FastAPI application
│   ├── app/
│   │   ├── agents/                # LangGraph pipeline (moved from src/agents/, logic unchanged)
│   │   │   ├── state.py           # NovelState TypedDict
│   │   │   ├── graph.py           # StateGraph, conditional routing, execute_graph()
│   │   │   └── nodes/
│   │   │       ├── world_and_creature.py
│   │   │       ├── planning.py
│   │   │       └── creature.py
│   │   ├── db/
│   │   │   ├── models.py          # SQLAlchemy 2.0 ORM models (Postgres)
│   │   │   ├── init_db.py         # Schema bootstrap (idempotent, incl. pgvector extension)
│   │   │   └── vector_store.py    # pgvector-backed lore store (replaces ChromaDB)
│   │   ├── core/
│   │   │   ├── config.py          # Env-driven settings
│   │   │   └── auth.py            # Supabase JWT verification dependency
│   │   ├── api/
│   │   │   └── novels.py          # REST endpoints: list/detail/generate
│   │   └── main.py                # FastAPI app, CORS, routing, startup
│   ├── tests/
│   │   └── test_api_auth.py       # Route wiring + auth-enforcement smoke tests
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/                     # Next.js (App Router, TypeScript) application
│   ├── app/
│   │   ├── login/                 # Google sign-in via Supabase Auth
│   │   ├── auth/callback/         # OAuth code exchange route
│   │   ├── novels/                # Protected "My Novels" library page
│   │   └── novels/[id]/           # Novel detail: chapters/characters/creatures
│   ├── lib/
│   │   ├── supabaseClient.ts      # Browser Supabase client
│   │   ├── supabaseServer.ts      # Server Supabase client (cookies)
│   │   └── api.ts                 # Typed fetch wrapper → backend API
│   ├── middleware.ts              # Session refresh + route protection
│   ├── package.json
│   └── .env.local.example
├── infra/
│   └── legacy-aws-terraform/      # DEPRECATED — old ECS/RDS/EFS Terraform, kept for reference only
├── docs/
│   └── architecture-redesign.md   # Full migration proposal
└── .github/workflows/
    ├── backend.yml                # Install deps, pytest, Docker build
    └── frontend.yml                # npm ci, lint, next build
```

---

## Migration status

| Area | Status |
|---|---|
| Monorepo layout (`backend/`, `frontend/`) | ✅ Phase 1 |
| FastAPI REST API (list/detail novels, sync generate) | ✅ Phase 1 |
| Postgres models + `owner_id` scoping | ✅ Phase 1 |
| pgvector lore store (replaces ChromaDB) | ✅ Phase 1 |
| Supabase Auth (Google OAuth) + JWT verification | ✅ Phase 1 |
| Next.js frontend (library + detail pages) | ✅ Phase 1 |
| Streamlit UI | ❌ Removed in Phase 1 |
| Async job queue (Upstash Redis + RQ) | ⏳ Phase 2 |
| Markdown / PDF export (WeasyPrint) | ⏳ Phase 3 |
| AWS Terraform (ECS/RDS/EFS) | ❌ Removed, see `infra/legacy-aws-terraform/` |

---

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+
- A [Supabase](https://supabase.com) project (free tier works) — or the
  [Supabase CLI](https://supabase.com/docs/guides/cli) for a local stack
  (`supabase start`)
- An OpenAI API key

### 1 — Backend (FastAPI)

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_JWT_SECRET, SUPABASE_SERVICE_ROLE_KEY,
# DATABASE_URL (Postgres connection string from your Supabase project),
# and OPENAI_API_KEY.

uvicorn app.main:app --reload
```

The backend starts on [http://localhost:8000](http://localhost:8000)
(`/health` for a liveness check, `/docs` for the OpenAPI UI). On startup it
creates the `vector` extension and any missing tables/columns.

### 2 — Frontend (Next.js)

```bash
cd frontend
npm install
cp .env.local.example .env.local
# Fill in NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY,
# NEXT_PUBLIC_API_URL (http://localhost:8000 for local dev).

npm run dev
```

Open [http://localhost:3000](http://localhost:3000). In your Supabase
project, enable the Google OAuth provider and add
`http://localhost:3000/auth/callback` as a redirect URL.

### 3 — Running backend tests

```bash
cd backend
pip install pytest httpx
pytest -v
```

Phase 1 ships route-wiring and auth-enforcement smoke tests only — no
pre-existing test suite was found to migrate (the README previously
referenced a `tests/` directory that did not exist in the repository).
Tests run against a mocked JWT secret; no live Postgres/Supabase instance is
required.

---

## Deployment

- **Frontend**: Vercel, connected via its own GitHub integration pointed at
  the `frontend/` directory as the project root (automatic preview + prod
  deploys — no custom GitHub Actions deploy step).
- **Backend**: an always-on container on **Render** or **Fly.io** (not
  Vercel serverless functions, since novel generation can take several
  minutes per request — longer than typical serverless function limits).
  `backend/Dockerfile` builds the image; `.github/workflows/backend.yml`
  verifies it builds in CI.
- **Database / Auth / Storage**: Supabase (hosted Postgres + pgvector +
  Auth + Storage).
- **Jobs** (Phase 2): Upstash Redis + RQ.

Target budget: roughly $20–50/month overall (Supabase free/pro tier, a small
Render/Fly.io instance, Vercel free tier, Upstash free tier).

---

## Environment Variables

### `backend/.env`

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key (server-side only, never exposed to the frontend) |
| `SUPABASE_JWT_SECRET` | Shared secret used to verify Supabase Auth JWTs |
| `DATABASE_URL` | Postgres connection string (Supabase connection pooler recommended) |
| `OPENAI_API_KEY` | API key for OpenAI |
| `LLM_MODEL` | Model name passed to LangChain, e.g. `gpt-4o` |
| `LLM_TEMPERATURE` | Generation temperature |
| `MAX_REVISION_LOOPS` | Max scene-writer / red-team cycles before forced exit |
| `CORS_ORIGINS` | Comma-separated list of allowed frontend origins |

### `frontend/.env.local`

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/public key |
| `NEXT_PUBLIC_API_URL` | Base URL of the FastAPI backend |

---

## Agent Overview

| Agent | Node | Responsibility |
|---|---|---|
| World Architect | `world_architect` | Generates geography, kingdoms, and magic systems; persists lore to pgvector |
| Creature Architect | `creature_architect` | Creates mythical beasts with ecology and magical traits linked to world lore |
| Character Agent | `character_agent` | Builds character dossiers (backstory, motivation, fatal flaw, arc) |
| Plot Agent | `plot_agent` | Produces multi-POV chapter outlines with scene beats |
| Scene Writer | `scene_writer` | Writes chapter prose from the beat outline |
| Red Team Auditor | `red_team` | Critiques the draft against lore; returns a 1-10 score |
| Prose Stylist | `prose_stylist` | Final prose polish pass before output |

Routing rule: if `feedback_score < 8` **and** `revision_count < MAX_REVISION_LOOPS`, the graph loops back to Scene Writer. Otherwise it exits to Prose Stylist.
