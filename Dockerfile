# ============================================================
# Nexus Scalp Engine — Multi-stage Docker image
# ============================================================
# Stage 1  builder : deps wheel build (cached independently of src/)
# Stage 2  runtime : minimal image, non-root user, no build toolchain
#
# FIXLOG (2026-09-09, docker-repair):
#   1. Builder previously re-resolved the project via PEP 517 with
#      --find-links=/wheels as the ONLY index ("--no-index"); the isolated
#      build env then tried to fetch "wheel" from /wheels and died with
#      "No matching distribution found for wheel". Fixed with
#      --no-build-isolation (setuptools+wheel are preinstalled in the same
#      RUN, so no isolation is needed and nothing is re-fetched).
#   2. torch 2.14.0 on Linux pulls the full CUDA-13 wheel family
#      (~5.5 GB installed). The engine's live serving path is CPU-only
#      (torch.load(map_location="cpu"), torch.set_num_threads(1), no
#      .cuda()/device usage anywhere in src/nexus_scalp), so the image now
#      installs torch from the official CPU index. Image drops ~5.5 GB ->
#      ~1.2 GB and build time drops proportionally. Windows/training hosts
#      are unaffected: requirements.txt (CUDA build) remains the source of
#      truth outside Docker.
# ============================================================

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

# CPU-only torch index (see FIXLOG note 2).
ARG PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}

# Dependency layer: copy only the manifests so this layer reuses the
# Docker cache unless requirements actually change.
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/

RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir=/wheels --no-cache-dir . \
    && mkdir -p /install \
    && pip install --no-index --find-links=/wheels --prefix=/install --no-build-isolation . \
    # The runtime stage does NOT mount /install into sys.path (PYTHONPATH only
    # carries /app/src), so the dependency tree must live where the interpreter
    # looks: /usr/local/lib/python3.11/site-packages. A --prefix=/install copy
    # is only reachable via PYTHONPATH, which caused ModuleNotFoundError: typer
    # in the entrypoint's `db migrate` gate (docker-repair, 2026-09-09).
    && mkdir -p /layerdeps \
    && cp -a /install/lib/python3.11/site-packages/. /layerdeps/

# ============================================================
# Stage 2: lightweight runtime (non-root)
# ============================================================
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH="/app/src:/install/lib/python3.11/site-packages" \
    PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin:/install/bin" \
    TZ=UTC \
    NSE_WEB_HOST=0.0.0.0
# NSE_WEB_HOST=0.0.0.0: containers must bind the API on all interfaces or the
# `docker -p` port mapping reaches nothing (engine default is 127.0.0.1 inside
# the container -> host curl gets connection reset; docker-repair 2026-09-09).

# libgomp1: required at runtime by torch/polars native libs (CPU wheels do
# not vendor it, debian-slim does not ship it) — the engine failed to boot
# with OSError "libgomp.so.1: cannot open shared object file" without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

COPY --from=builder /layerdeps/ /usr/local/lib/python3.11/site-packages/
COPY configs/ configs/
COPY docker/ docker/
COPY src/ src/
COPY Web/ Web/

RUN chmod +x /app/docker/*.sh \
    && mkdir -p /app/artifacts/models /app/artifacts/logs /app/data /app/Web \
    # live.yaml is operator-local (gitignored, never baked): the container CMD
    # and docs/docker.md both reference configs/live.yaml, so ship the example
    # under the canonical name (env vars remain the real bootstrap knobs —
    # docker-repair 2026-09-09: image previously shipped no live.yaml at all
    # and the engine died with "Config missing: configs/live.yaml").
    && cp /app/configs/live.yaml.example /app/configs/live.yaml \
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
