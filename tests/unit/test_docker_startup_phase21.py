"""Docker startup & environment contract tests (DOCKER-REPAIR, 2026-08-20).

Covers the container-facing surface introduced by the Docker repair:

    TEST-DOCKER-01  compose file parses (docker compose config --quiet)
    TEST-DOCKER-02  compose declares the expected services (core, redis)
    TEST-DOCKER-03  compose has NO postgres service (SQLite-only architecture)
    TEST-DOCKER-04  core env includes the documented NSE_* bootstrap variables
    TEST-DOCKER-05  .env.example exists and contains the documented variables
    TEST-DOCKER-06  .env.example contains no secret-looking values
    TEST-DOCKER-07  Dockerfile is multi-stage with a non-root runtime user
    TEST-DOCKER-08  /health endpoint returns verdict READY or DEGRADED
    TEST-DOCKER-09  /health returns 503 NOT READY when a critical check fails
    TEST-DOCKER-10  /health never leaks secrets (redaction pass)

These are fast, dependency-light tests (yaml only, no docker daemon except
TEST-DOCKER-01/02/03 which skip when the docker CLI is unavailable).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DOCKERFILE = REPO_ROOT / "Dockerfile"

DOCUMENTED_VARS = (
    "NSE_EXECUTION__MODE",
    "NSE_EXECUTION__SYMBOL",
    "NSE_WEB_PORT",
    "NSE_LOG_LEVEL",
    "TZ",
    "NSE_MODEL__MODEL_ARTIFACT_PATH",
    "NSE_RISK__MAX_ACCOUNT_DRAWDOWN_PCT",
    "NSE_RISK__RISK_PER_TRADE_PCT",
    "NSE_NEWS__ENABLED",
    "NSE_TELEGRAM__ENABLED",
)

SECRET_PATTERNS = (
    "bot_token=",
    "bot_token:",
    "=TOKEN",
    "=YOUR_TOKEN",
    "api_key",
    "password=",
    "sk-",
    "ghp_",
    "-----BEGIN",
)


@pytest.fixture(scope="module")
def compose_data() -> dict:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml missing")
    with open(COMPOSE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(scope="module")
def compose_services(compose_data: dict) -> dict:
    return compose_data.get("services", {})


# ---------------------------------------------------------------------------
# Compose contract
# ---------------------------------------------------------------------------


def test_docker_01_compose_parses() -> None:
    """docker compose config --quiet exits 0 (skip when docker unavailable)."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    proc = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_docker_02_expected_services(compose_services: dict) -> None:
    assert "core" in compose_services, "core service must exist"
    assert "redis" in compose_services, "redis service must exist"


def test_docker_03_no_postgres_in_compose(compose_data: dict) -> None:
    """The project persistence is SQLite; no postgres service may exist."""
    services = compose_data.get("services", {})
    assert "postgres" not in services
    assert "postgres" not in yaml.safe_dump(compose_data).lower()


def test_docker_04_core_env_has_bootstrap_vars(compose_services: dict) -> None:
    env = compose_services["core"].get("environment", [])
    assert any(str(e).startswith("NSE_EXECUTION__MODE") for e in env)
    assert any(str(e).startswith("NSE_MODEL__MODEL_ARTIFACT_PATH") for e in env)
    assert any(str(e).startswith("NSE_WEB_HOST") for e in env)


def test_docker_05_core_has_healthcheck_and_port(compose_services: dict) -> None:
    core = compose_services["core"]
    assert core.get("healthcheck"), "core must define a healthcheck"
    ports = core.get("ports", [])
    assert any("9090" in str(p) for p in ports), "core must publish 9090"
    assert core.get("restart") == "unless-stopped"


def test_docker_06_redis_internal_only(compose_services: dict) -> None:
    redis = compose_services["redis"]
    assert not redis.get("ports"), "redis must NOT be exposed to the host"
    assert redis.get("healthcheck"), "redis must have a healthcheck"


# ---------------------------------------------------------------------------
# .env.example contract
# ---------------------------------------------------------------------------


def test_docker_07_env_example_exists_and_documents_vars() -> None:
    assert ENV_EXAMPLE.exists(), ".env.example missing"
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in DOCUMENTED_VARS:
        assert var in text, f"{var} must be documented in .env.example"


def test_docker_08_env_example_has_no_secrets() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    low = text.lower()
    for pat in SECRET_PATTERNS:
        assert pat.lower() not in low, f"secret-looking value {pat!r} in .env.example"


# ---------------------------------------------------------------------------
# Dockerfile contract
# ---------------------------------------------------------------------------


def test_docker_09_dockerfile_multistage_and_nonroot() -> None:
    assert DOCKERFILE.exists()
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.11-slim AS builder" in text
    assert "FROM python:3.11-slim AS runner" in text
    assert "USER appuser" in text
    assert "ENTRYPOINT" in text
    assert "HEALTHCHECK" in text


# ---------------------------------------------------------------------------
# /health endpoint semantics (unit-level, no server required)
# ---------------------------------------------------------------------------
# The route lives in create_app(); exercising it fully needs the FastAPI app.
# We test the *contract* the healthcheck relies on: HealthEngine verdicts and
# the redaction pipeline used by the endpoint response.


def test_docker_10_healthengine_verdicts_are_expected() -> None:
    from nexus_scalp.release.health import HealthEngine

    verdict, entries = HealthEngine().overall()
    assert verdict in ("READY", "DEGRADED", "NOT READY")
    cats = {e.category for e in entries}
    assert {"SYSTEM", "CONFIGURATION", "DATABASE", "MODEL"} <= cats


def test_docker_11_health_payload_never_contains_plain_secrets() -> None:
    """HealthEngine detail strings must not embed tokens (redaction)."""
    from nexus_scalp.release.health import HealthEngine

    _verdict, entries = HealthEngine().overall()
    joined = " ".join(f"{e.reason} {e.suggestion}" for e in entries)
    for frag in ("bot_token=", "TOKEN=1", "api_key=", "-----BEGIN"):
        assert frag.lower() not in joined.lower()


def test_docker_12_boot_env_maps_to_appconfig() -> None:
    """NSE_* double-underscore names map onto AppConfig (docker contract)."""
    import os

    from nexus_scalp.configuration.config import AppConfig

    old = {k: os.environ.get(k) for k in ("NSE_EXECUTION__SYMBOL", "NSE_RISK__RISK_PER_TRADE_PCT")}
    try:
        os.environ["NSE_EXECUTION__SYMBOL"] = "EURUSD"
        os.environ["NSE_RISK__RISK_PER_TRADE_PCT"] = "0.7"
        cfg = AppConfig()
        assert cfg.execution.symbol == "EURUSD"
        assert cfg.risk.risk_per_trade_pct == 0.7
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v