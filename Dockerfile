# Multi-stage Dockerfile for Cognition server
# Production-ready with security hardening

ARG VERSION=dev
ARG BUILD_DATE
ARG VCS_REF

FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, deterministic dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy lockfile and project metadata first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Install production deps only using the frozen lockfile (no test extras)
# --no-dev: skip dev-only deps (ruff, mypy, pre-commit)
# --no-install-project: deps only — source code is copied in the next stage
# --extra openai,bedrock,deploy: include all production extras
RUN uv sync --frozen --no-dev --no-install-project --extra openai --extra bedrock --extra deploy

# Production stage
FROM python:3.11-slim AS production

# OCI Annotations / Labels
LABEL org.opencontainers.image.title="Cognition"
LABEL org.opencontainers.image.description="Batteries-included AI backend for coding agents"
LABEL org.opencontainers.image.url="https://github.com/CognicellAI/Cognition"
LABEL org.opencontainers.image.source="https://github.com/CognicellAI/Cognition"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.licenses="MIT"

# Security: Run as non-root user with home directory
RUN groupadd -r cognition && useradd -r -g cognition -u 1000 -m cognition

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
# Add app root to PYTHONPATH so server/client/shared packages are importable
ENV PYTHONPATH="/app"

# Set working directory
WORKDIR /app

# Copy application code
COPY server/ ./server/
COPY client/ ./client/
COPY shared/ ./shared/
COPY pyproject.toml .

# Create workspace directory and set permissions
RUN mkdir -p /workspace /home/cognition/.cache && \
    chown -R cognition:cognition /workspace /app /home/cognition

# Switch to non-root user
USER cognition

# Set cache directories to avoid permission issues
ENV PYTHONDONTWRITEBYTECODE=1

# Expose ports
# 8000 - FastAPI server
# 9090 - Prometheus metrics
EXPOSE 8000 9090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the server
CMD ["uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
