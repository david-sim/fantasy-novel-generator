# NovelEngine

A multi-agent generative AI system that autonomously writes publication-grade fantasy novels. Powered by **LangGraph** for agent orchestration, **LangChain** for LLM calls, **ChromaDB** for lore retrieval, and **Streamlit** for a real-time dashboard.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        LangGraph Pipeline                        │
│                                                                  │
│  WorldArchitect → CreatureArchitect → CharacterAgent → PlotAgent │
│                                                                  │
│       ┌─────────────────────────────────┐                        │
│       ▼                                 │ score < 8              │
│  SceneWriter → RedTeamAuditor ──────────┘                        │
│                    │                                             │
│                    │ score ≥ 8 (or loop cap reached)             │
│                    ▼                                             │
│              ProseStylist → END                                  │
└──────────────────────────────────────────────────────────────────┘
         │                          │
    SQLite / RDS               ChromaDB (EFS)
    (AgentLog, Novel,          (world lore, creatures,
     Chapter, Character,        characters, plot beats)
     Creature)
```

Each agent writes to both a **SQLite database** (structured data) and a **ChromaDB vector store** (semantic search for lore retrieval). The Streamlit UI polls the `AgentLog` table every 2 seconds via `@st.fragment(run_every=2)` to display live agent activity without full-page reruns.

---

## Project Structure

```
fantansy-novel-generator/
├── src/
│   ├── agents/
│   │   ├── state.py              # NovelState TypedDict (single source of truth)
│   │   ├── graph.py              # StateGraph, conditional routing, execute_graph()
│   │   └── nodes/
│   │       ├── world_and_creature.py   # WorldArchitect + CreatureArchitect nodes
│   │       └── planning.py             # CharacterAgent + PlotAgent nodes
│   ├── db/
│   │   ├── models.py             # SQLAlchemy 2.0 ORM models
│   │   ├── init_db.py            # Schema bootstrap (idempotent)
│   │   └── vector_store.py       # ChromaDB client wrapper
│   └── ui/
│       ├── app.py                # Streamlit application
│       └── utils.py              # Background thread execution helpers
├── tests/
│   ├── conftest.py               # Stubs for chromadb / langchain providers
│   └── test_graph.py             # 25 routing tests (unit + integration)
├── infra/                        # Terraform – ECS Fargate + RDS + EFS
│   ├── main.tf
│   ├── variables.tf
│   ├── networking.tf             # VPC, subnets, ALB, security groups
│   ├── ecr.tf                    # ECR repository
│   ├── efs.tf                    # EFS for ChromaDB persistence
│   ├── rds.tf                    # RDS Postgres + SSM SecureString params
│   ├── iam.tf                    # ECS roles + GitHub Actions OIDC role
│   ├── ecs.tf                    # Fargate cluster, task definition, service
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── .github/workflows/deploy.yml  # CI/CD: build → ECR → ECS Fargate
├── Dockerfile                    # Two-stage build (builder + runtime)
├── .env                          # Local secrets (git-ignored)
└── requirements.txt
```

---

## Quickstart (local)

### Prerequisites

- Python 3.11+
- An OpenAI API key (or Anthropic / Google)

### 1 — Clone and create a virtual environment

```bash
git clone https://github.com/your-org/fantansy-novel-generator.git
cd fantansy-novel-generator
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2 — Install dependencies

```bash
pip install -r requirements.txt
# OpenAI provider (default)
pip install langchain-openai
# Or Anthropic:  pip install langchain-anthropic
# Or Google:     pip install langchain-google-genai
```

### 3 — Configure environment variables

Copy `.env` and fill in your key (the file is already git-ignored):

```bash
# .env is already present – just open it and add your key
```

Required variables (see `.env` for the full list):

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `LLM_PROVIDER` | `openai` (default), `anthropic`, or `google` |
| `LLM_MODEL` | Model name, e.g. `gpt-4o` |

### 4 — Initialise the database

```bash
python -m src.db.init_db
```

### 5 — Run the Streamlit app

```bash
streamlit run src/ui/app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Running with Docker

```bash
# Build
docker build -t novelengine:latest .

# Run (mount a local data directory for SQLite + ChromaDB persistence)
docker run -p 8501:8501 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  novelengine:latest
```

The container initialises the database automatically before starting Streamlit.

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

Tests use `unittest.mock` stubs for all external services (chromadb, LangChain providers) — no API keys or running services required.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | API key for OpenAI (required when `LLM_PROVIDER=openai`) |
| `ANTHROPIC_API_KEY` | — | API key for Anthropic |
| `GOOGLE_API_KEY` | — | API key for Google |
| `LLM_PROVIDER` | `openai` | Active LLM provider |
| `LLM_MODEL` | `gpt-4o` | Model name passed to LangChain |
| `LLM_TEMPERATURE` | `0.85` | Generation temperature |
| `MAX_REVISION_LOOPS` | `5` | Max scene-writer / red-team cycles before forced exit |
| `DATABASE_URL` | `sqlite:///./novelengine.db` | SQLAlchemy connection string |
| `CHROMA_PATH` | `./chroma_db` | Directory for ChromaDB persistence |

---

## Cloud Deployment (AWS ECS Fargate)

Full Terraform configuration lives in `infra/`. It provisions:

- **VPC** with public (ALB) and private (ECS + RDS) subnets across 2 AZs
- **ECR** repository with scan-on-push and lifecycle policy
- **EFS** file system for ChromaDB persistence across task restarts
- **RDS Postgres 16** (replaces SQLite in production)
- **ECS Fargate** cluster + service + task definition
- **ALB** exposing port 8501
- **IAM** roles for ECS execution, the task, and a GitHub Actions OIDC role (no static credentials)

### Bootstrap (one-time)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: set github_repo, llm_api_key, db_password

terraform init
terraform apply
```

Copy the `github_actions_role_arn` output value and add it as the `AWS_ROLE_ARN` secret in your GitHub repository. Subsequent pushes to `main` trigger automatic deploys via `.github/workflows/deploy.yml`.

---

## Agent Overview

| Agent | Node | Responsibility |
|---|---|---|
| World Architect | `world_architect` | Generates geography, kingdoms, and magic systems; persists to ChromaDB |
| Creature Architect | `creature_architect` | Creates mythical beasts with ecology and magical traits linked to world lore |
| Character Agent | `character_agent` | Builds character dossiers (backstory, motivation, fatal flaw, arc) |
| Plot Agent | `plot_agent` | Produces multi-POV chapter outlines with scene beats |
| Scene Writer | `scene_writer` | Writes chapter prose from the beat outline *(stub — Story 3.4)* |
| Red Team Auditor | `red_team` | Critiques the draft against lore; returns a 1-10 score *(stub — Story 3.5)* |
| Prose Stylist | `prose_stylist` | Final prose polish pass before output *(stub)* |

Routing rule: if `feedback_score < 8` **and** `revision_count < MAX_REVISION_LOOPS`, the graph loops back to Scene Writer. Otherwise it exits to Prose Stylist.
