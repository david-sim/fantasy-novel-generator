# NovelEngine

A multi-agent generative AI system that autonomously writes publication-grade
fantasy novels. Powered by **LangGraph** for agent orchestration and
**LangChain** for LLM calls.

> **Architecture migration complete.** This repo has moved from a
> single-container Streamlit + SQLite + ChromaDB app to a monorepo of a
> **FastAPI backend** + **Next.js frontend** on **Supabase** (Postgres +
> pgvector + Auth + Storage). The full proposal is tracked in
> `docs/architecture-redesign.md` (see PR #3 — docs-only). This document
> describes the architecture as of **Phase 3** of that migration (export +
> polish, the final layer — see [Migration status](#migration-status)
> below).

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
│                        │   Storage (unused — exports stream directly)      │
└────────────────────────────────────────────────────────────────────────────┘
```

The LangGraph agent pipeline itself is **unchanged** by this migration — only
how it's invoked (FastAPI instead of Streamlit callbacks), where it persists
data (Postgres/pgvector via Supabase instead of SQLite/ChromaDB), and how
results are retrieved (async job polling, Markdown/PDF export) have changed.

**Phase 1:** the `/api/novels/{id}/generate` endpoint called `execute_graph()`
**synchronously** — the HTTP request blocked until generation finished.
**Phase 2:** `/generate` enqueues an async job on a Redis queue
(RQ) and returns a `job_id` immediately (`202 Accepted`); a separate
**worker** process pulls jobs off the queue and runs `execute_graph()`. The
frontend polls `GET /api/novels/{id}/jobs/{job_id}` on a simple interval
(no websockets/SSE) to show live progress and the final result.
**Phase 3 (this PR):** once a novel has completed chapters, it can be
exported as a single Markdown document or a PDF (rendered via WeasyPrint)
from the novel detail page, and the frontend got a lightweight polish pass
(library/detail page layout, empty/loading states, basic responsive tweaks).

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
│   │   │   ├── auth.py            # Supabase JWT verification dependency
│   │   │   └── queue.py           # Shared Redis connection + RQ queue accessor
│   │   ├── services/
│   │   │   └── export.py          # Markdown/PDF export rendering (Phase 3, shared by both formats)
│   │   ├── templates/
│   │   │   └── novel_export.html  # Jinja2 template used as WeasyPrint's HTML input
│   │   ├── api/
│   │   │   └── novels.py          # REST endpoints: list/detail/generate/job-status/export
│   │   ├── worker.py               # RQ worker entrypoint (Phase 2 async jobs)
│   │   └── main.py                # FastAPI app, CORS, routing, startup
│   ├── tests/
│   │   ├── test_api_auth.py       # Route wiring + auth-enforcement smoke tests
│   │   └── test_export.py         # Export endpoint: auth, no-content error, markdown/pdf content-types
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/                     # Next.js (App Router, TypeScript) application
│   ├── app/
│   │   ├── login/                 # Google sign-in via Supabase Auth
│   │   ├── auth/callback/         # OAuth code exchange route
│   │   ├── novels/                # Protected "My Novels" library page
│   │   └── novels/[id]/           # Novel detail: chapters/characters/creatures,
│   │                              # GenerationPanel (trigger/poll jobs), ExportButtons
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
├── docker-compose.yml             # Optional local dev: redis + pgvector postgres + api + worker
└── .github/workflows/
    ├── backend.yml                # Install deps, pytest, worker import smoke test, Docker build
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
| Async job queue (Redis + RQ) + polling UI | ✅ Phase 2 |
| Markdown / PDF export (WeasyPrint) | ✅ Phase 3 |
| Frontend polish (library/detail layout, empty & loading states) | ✅ Phase 3 |
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

### 2 — Redis + worker (Phase 2 async jobs)

`POST /api/novels/{id}/generate` enqueues a job on Redis; a separate worker
process runs it. Either:

- **Local Redis directly:** run `redis-server` (or any local Redis), set
  `REDIS_URL=redis://localhost:6379/0` in `backend/.env`, then in a second
  terminal:

  ```bash
  cd backend
  python -m app.worker
  # equivalently: rq worker novel-generation --url redis://localhost:6379/0
  ```

- **Or via Docker Compose** (also spins up a local pgvector Postgres so you
  don't need a Supabase project for local iteration):

  ```bash
  cp backend/.env.example backend/.env   # fill in SUPABASE_*, OPENAI_API_KEY
  docker compose up --build
  ```

  This starts `redis`, `postgres`, `api` (port 8000), and `worker`. See
  `docker-compose.yml` for details — it's a local dev convenience only;
  production still targets Supabase Postgres + an Upstash-compatible Redis
  endpoint (see [Deployment](#deployment)).

### 3 — Frontend (Next.js)

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

### 4 — Export (Phase 3: Markdown / PDF)

`GET /api/novels/{id}/export?format=markdown` and
`GET /api/novels/{id}/export?format=pdf` return a completed novel (title,
ordered chapters, character/creature appendix) as a downloadable file — a
clickable button on the novel detail page, or directly:

```bash
curl -H "Authorization: Bearer <supabase-access-token>" \
  "http://localhost:8000/api/novels/1/export?format=markdown" -o novel.md
```

PDF export renders the same content to HTML (Jinja2 template in
`backend/app/templates/novel_export.html`) and converts it with
[WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/), which needs
system-level Pango/Cairo libraries. `backend/Dockerfile`'s runtime stage
installs them so PDF export works in the deployed container; for **local,
non-Docker** development on Linux, install your distro's WeasyPrint
dependencies ([install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation));
on Windows, install the [GTK3 runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases).
No new environment variables are required for export.

Both endpoints enforce the same owner-only authorization as every other
`/api/novels` route, and return `409 Conflict` if the novel has no completed
chapters yet.

### 5 — Running backend tests

```bash
cd backend
pip install pytest httpx
pytest -v
```

Phase 1 shipped route-wiring and auth-enforcement smoke tests. Phase 2 added
one for the job-status route, plus a CI step that import-smoke-tests
`app.worker`. Phase 3 adds `tests/test_export.py` (auth enforcement, the
no-completed-chapters error, and markdown/pdf content-type checks) —
WeasyPrint's own PDF rendering is stubbed in these tests so they don't need
its native libraries installed locally; CI installs them (see
`.github/workflows/backend.yml`) so the Docker build stage still gets full
coverage of the real dependency chain. Tests run against a mocked JWT
secret; no live Postgres/Redis instance is required.

---

## Deployment

- **Frontend**: Vercel, connected via its own GitHub integration pointed at
  the `frontend/` directory as the project root (automatic preview + prod
  deploys — no custom GitHub Actions deploy step).
- **Backend**: **two always-on processes** from the same `backend/Dockerfile`
  image, run as separate services:
  - **web** — `uvicorn app.main:app --host 0.0.0.0 --port 8000` (the API).
  - **worker** — `python -m app.worker` (processes queued generation jobs).

  Both deployed on **Render** or **Fly.io** rather than Vercel: novel
  generation can take several minutes, longer than typical serverless
  function limits, and Vercel has no first-class way to run a persistent
  background worker alongside a web service from the same repo/image —
  Render and Fly.io both support a web service + a separate background
  worker service from one repo, which is why they were chosen for the
  backend. `.github/workflows/backend.yml` verifies the image builds and
  that `app.worker` imports cleanly in CI.
- **Database / Auth / Storage**: Supabase (hosted Postgres + pgvector +
  Auth + Storage).
- **Jobs**: an Upstash-compatible Redis endpoint (or any Redis reachable via
  `REDIS_URL`) + RQ.

Target budget: roughly $20–50/month overall (Supabase free/pro tier, a small
Render/Fly.io instance for each of web + worker, Vercel free tier, Upstash
free tier).

---

## Environment Variables

### `backend/.env`

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key (server-side only, never exposed to the frontend) |
| `SUPABASE_JWT_SECRET` | Shared secret used to verify Supabase Auth JWTs |
| `DATABASE_URL` | Postgres connection string (Supabase connection pooler recommended) |
| `REDIS_URL` | Redis connection string for the async job queue (Upstash-compatible endpoint in production, `redis://localhost:6379/0` for local dev) — read by both the API (`web`) and `app.worker` |
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
