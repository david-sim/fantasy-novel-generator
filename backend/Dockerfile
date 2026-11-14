# ── Stage 1: dependency builder ──────────────────────────────────────────────
# Use a full image with build tools so native extensions (psycopg, etc.) compile.
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system build dependencies needed by psycopg / pydantic, plus the
# Pango/Cairo headers WeasyPrint's cffi bindings need to build (Phase 3 PDF
# export).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN groupadd --gid 1001 novelengine \
 && useradd  --uid 1001 --gid novelengine --no-create-home novelengine

WORKDIR /app

# Runtime dependency for psycopg (built against it in the builder stage),
# plus the shared libraries WeasyPrint needs at runtime to render PDFs
# (Phase 3 export): Pango/Cairo/GDK-Pixbuf for layout + rasterization,
# shared-mime-info for MIME detection, and DejaVu as a default font so PDF
# text doesn't fall back to tofu boxes in a minimal container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY app/         ./app/
COPY requirements.txt .

RUN chown -R novelengine:novelengine /app

# Environment defaults (override at runtime via -e or your hosting platform's
# env var settings — e.g. Render/Fly.io). DATABASE_URL and Supabase secrets
# MUST be overridden in every real deployment; these are local-dev fallbacks.
ENV DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/novelengine" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER novelengine

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Launch the FastAPI app. Schema init (incl. pgvector extension) runs on
# startup via app.main's startup event, so no separate init step is needed.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
