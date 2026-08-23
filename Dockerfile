# ── Stage 1: dependency builder ──────────────────────────────────────────────
# Use a full image with build tools so native extensions (chromadb, etc.) compile.
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system build dependencies needed by chromadb / pydantic
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
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

# Copy pre-built packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY src/       ./src/
COPY requirements.txt .

# Persistent data directories – declared as volumes so SQLite and ChromaDB
# survive container restarts when mounted from the host.
RUN mkdir -p /app/data/chroma_db \
 && chown -R novelengine:novelengine /app

VOLUME ["/app/data"]

# Environment defaults (override at runtime via -e or docker-compose env_file)
ENV DATABASE_URL="sqlite:////app/data/novelengine.db" \
    CHROMA_PATH="/app/data/chroma_db" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER novelengine

# Initialise the SQLite schema, then launch Streamlit.
# The HEALTHCHECK below lets orchestrators (ECS, k8s) probe readiness.
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["sh", "-c", \
     "python -m src.db.init_db && \
      streamlit run src/ui/app.py \
        --server.port=8501 \
        --server.address=0.0.0.0 \
        --server.headless=true \
        --browser.gatherUsageStats=false"]
