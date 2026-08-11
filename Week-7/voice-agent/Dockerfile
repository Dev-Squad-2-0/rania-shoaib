# ---------------------------------------------------------------
# Dockerfile — RealEstate Hub Voice Agent (Ayesha)
# Multi-stage: builder installs deps, runtime is lean.
# ---------------------------------------------------------------

# ---- Stage 1: builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile some Python wheels (psycopg2, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ---- Stage 2: runtime ----
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system deps only (libpq for psycopg2 at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Non-root user — don't run as root in production
RUN useradd -m -u 1000 ayesha && chown -R ayesha:ayesha /app
USER ayesha

# ChromaDB persists to a volume — make sure the directory exists and is writable
RUN mkdir -p /app/chroma_store

# Expose FastAPI port
EXPOSE 8000

# Health check — hits /health every 30s; container is "unhealthy" after
# 3 consecutive failures (90s), giving the app time to load the embedding
# model on first start before the load balancer sends it real traffic.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api_server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log"]
