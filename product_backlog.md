# NovelEngine - Agile Product Backlog

## Vision
A multi-agent generative AI system powered by LangGraph and Streamlit, designed to autonomously write publication-grade fantasy novels. The system utilizes specialized agents (World, Creature, Character, Plot, Scene, and Red Team) with a human-in-the-loop dashboard.

## Epic 1: Foundation & Infrastructure (MVP)
- **Story 1.1:** As a developer, I need a Python monorepo setup with `pyproject.toml` (or `requirements.txt`) including Streamlit, LangGraph, LangChain, SQLAlchemy, and ChromaDB.
- **Story 1.2:** As a system, I need a SQLite database schema to store `Novel`, `Chapter`, `Character`, `Creature`, and `AgentLog` tables.
- **Story 1.3:** As a system, I need a local ChromaDB vector store initialized to save and retrieve generated lore and world-building context.

## Epic 2: Core Agent State & Graph Orchestration
- **Story 2.1:** As an orchestrator, I need a `NovelState` TypedDict defined in LangGraph that holds the current novel context, manuscript drafts, lore context, and feedback flags.
- **Story 2.2:** As an orchestrator, I need the LangGraph StateGraph initialized with empty node functions for all planned agents (World, Creature, Character, Plot, Scene, Red Team, Prose).
- **Story 2.3:** As an orchestrator, I need conditional routing logic defined (e.g., if Red Team score < 8, route back to Scene Writer; else route to Prose Stylist).

## Epic 3: Agent Implementation
- **Story 3.1 (World Architect):** Implement prompt and LLM chain to generate geography, kingdoms, and magic systems. Store outputs in ChromaDB.
- **Story 3.2 (Creature Architect):** Implement prompt to generate mythical beasts, ecology, and magical traits. Link outputs to World Architect context.
- **Story 3.3 (Character & Plot):** Implement agents to generate character dossiers and multi-POV chapter outlines based on world/creature lore.
- **Story 3.4 (Scene Writer):** Implement the writing agent that takes the beat outline and generates markdown chapter text.
- **Story 3.5 (Red Team Auditor):** Implement the critique agent that reads the scene, checks against the lore DB, and outputs a JSON score (1-10) and revision notes.

## Epic 4: Streamlit Frontend Integration
- **Story 4.1:** As a user, I need a "Novel Setup" sidebar in Streamlit to input my global prompts (genre, tone, themes) and start the LangGraph execution in a background thread.
- **Story 4.2:** As a user, I need an "Agent Live Monitor" page that queries the SQLite `AgentLog` table and streams the thoughts and status of currently active agents.
- **Story 4.3:** As a user, I need a "Bestiary & Lore" tab to browse generated creatures, characters, and world rules.
- **Story 4.4:** As a user, I need a "Manuscript Reader" tab to read generated chapters alongside Red Team critique notes.

## Epic 5: Cloud Deployment & CI/CD
- **Story 5.1:** As a developer, I need a Dockerfile that packages the Streamlit app and its dependencies.
- **Story 5.2:** As a developer, I need GitHub Actions to build the Docker image and push it to AWS ECR.
- **Story 5.3:** As a developer, I need Terraform/AWS CLI scripts to deploy the container to AWS ECS and migrate SQLite to AWS RDS Postgres.