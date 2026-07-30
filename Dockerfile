# ==========================================
# Stage 1: Build Dependencies Environment
# ==========================================
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefix=/install .

# ==========================================
# Stage 2: Final Light Runtime Image
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/app/src:$PYTHONPATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

COPY --from=builder /install /install
COPY configs/ configs/
COPY docker/ docker/
COPY src/ src/

RUN chmod +x /app/docker/entrypoint.sh /app/docker/healthcheck.sh && \
    mkdir -p data/raw data/validated artifacts/models artifacts/logs && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000 9090

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD /app/docker/healthcheck.sh

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "nexus_scalp.cli.main", "run", "--config", "configs/live.yaml"]
