# Legacy AWS Terraform (deprecated)

This directory contains the **original** Terraform configuration that
provisioned the single-container Streamlit application on **AWS ECS Fargate**
(with RDS for Postgres-compatible storage and EFS for a ChromaDB volume).

**It is no longer used and is kept only for historical reference.**

As of the Phase 1 architecture migration (see `docs/architecture-redesign.md`
and the Phase 1 PR), the project has moved to:

- **Frontend**: Next.js on Vercel (Vercel's own GitHub integration handles
  deploys — no Terraform/GitHub Actions deploy step needed).
- **Backend**: FastAPI, deployed as an always-on container on
  Render or Fly.io (chosen for simplicity/cost over provisioning our own
  ECS cluster, load balancer, and VPC for a single service).
- **Database / Auth / Storage**: Supabase (managed Postgres + pgvector,
  Auth, Storage) — replaces the self-managed RDS instance and ChromaDB/EFS
  volume entirely.
- **Jobs** (Phase 2): Upstash Redis + RQ — a managed, serverless-friendly
  queue instead of a self-hosted broker.

None of the remaining infrastructure needs custom AWS provisioning, so this
Terraform is not being ported forward. Do not `terraform apply` anything in
this folder against a live environment — it describes infrastructure that
should be considered decommissioned.

If a future need for custom cloud infrastructure arises, prefer provisioning
it from scratch against the new architecture rather than resurrecting this
configuration, since the application topology (single container, ChromaDB
volume, SQLite-adjacent assumptions) no longer matches how the app is built.
