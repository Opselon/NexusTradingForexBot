# ============================================================
# Nexus Scalp Engine — Multi-stage Docker image
# ============================================================
# Stage 1  builder : deps wheel build (cached independently of src/)
# Stage 2  runtime : minimal image, non-root user, no build toolchain

# ============================================================
# Stage 1: build dependencies (cached via pyproject+requirements)
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System build deps for torch/polars wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer: copy only the manifests so this layer reuses the
# Docker cache unless requirements actually change.
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/

RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir=/wheels --no-cache-dir . \
    && mkdir -p /install \
    && pip install --no-index --find-links=/wheels --prefix=/install .

# ============================================================
# Stage 2: lightweight runtime (non-root)
# ============================================================
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH="/app/src" \
    PATH="/install/bin:$PATH" \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

COPY --from=builder /install /install
COPY configs/ configs/
COPY docker/ docker/
COPY src/ src/
COPY Web/ Web/

RUN chmod +x /app/docker/*.sh \
    && mkdir -p /app/artifacts/models /app/artifacts/logs /app/data /app/Web \
    && chown -R appuser:appgroup /app \
    && chmod -R u+w /app/artifacts /app/data

USER appuser

EXPOSE 9090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=6 \
    CMD ["/app/docker/healthcheck.sh"]

# The entrypoint handles env validation, dir bootstrap, migrations and
# startup summary; the CMD is the default engine command (PAPER mode).
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "nexus_scalp.cli.main", "start", "--mode", "paper", "--config", "configs/live.yaml", "--port", "9090"]