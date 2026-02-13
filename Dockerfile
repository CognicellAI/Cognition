# Multi-stage Dockerfile for Cognition server
# Production-ready with security hardening

FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[all]"

# Production stage
FROM python:3.11-slim AS production

# Security: Run as non-root user
RUN groupadd -r cognition && useradd -r -g cognition -u 1000 cognition

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY server/ ./server/
COPY client/ ./client/
COPY shared/ ./shared/
COPY pyproject.toml .

# Create workspace directory
RUN mkdir -p /workspace && chown -R cognition:cognition /workspace /app

# Switch to non-root user
USER cognition

# Expose ports
# 8000 - FastAPI server
# 9090 - Prometheus metrics
EXPOSE 8000 9090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the server
CMD ["uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
