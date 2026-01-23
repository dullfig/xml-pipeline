# BloxServer API Dockerfile
# Multi-stage build for smaller production image

# =============================================================================
# Build stage
# =============================================================================
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# =============================================================================
# Production stage
# =============================================================================
FROM python:3.12-slim as production

WORKDIR /app

# Create non-root user
RUN groupadd --gid 1000 bloxserver \
    && useradd --uid 1000 --gid bloxserver --shell /bin/bash --create-home bloxserver

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder and install
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code
COPY --chown=bloxserver:bloxserver . /app/bloxserver

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER bloxserver

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "bloxserver.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
