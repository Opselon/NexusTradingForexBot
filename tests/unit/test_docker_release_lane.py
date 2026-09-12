"""Finding 5 — Docker release lane integrity (release wave).

Intent verdict (from docs/docker.md §1, docs/70D_PRODUCTION_DEPLOYMENT.md §1,
agents/skill.md §Docker, README): Docker is the container-safe PAPER
evaluation/development environment — NOT the production path (production =
Windows release bundle + signed updater). The lane is MAINTAINED
(TASK-DOCKER-REPAIR, nightly-e2e uses compose) but was unreachable: it
triggered only on a `docker` branch that does not exist on origin.

Pinned here:
  * docker.yml triggers on the docker branch AND v* release tags
  * the image carries org.opencontainers.image.revision = FULL 40-hex SHA
  * a full-SHA format guard hard-fails the workflow otherwise
  * the built digest is emitted as the artifact identity (never a tag)
  * no required check depends on a dead lane (docker.yml is not in the
    main-CI required list — verified live via branch protection)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DOCKER_WF = REPO / ".github" / "workflows" / "docker.yml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _doc() -> dict:
    return yaml.safe_load(DOCKER_WF.read_text(encoding="utf-8"))


def test_docker_workflow_yaml_valid_and_triggers_alive() -> None:
    doc = _doc()
    on = doc.get(True, doc.get("on"))
    assert isinstance(on, dict), "docker.yml must declare `on:` triggers"
    push = on.get("push", {})
    branches = push.get("branches", [])
    tags = push.get("tags", [])
    assert "docker" in branches, "docker branch trigger must remain for container staging"
    assert any(t.startswith("v*") for t in tags), (
        "v* tag trigger must exist so release SHAs produce bound images "
        "(the docker branch alone made the lane unreachable)"
    )
    assert "workflow_dispatch" in on


def test_image_revision_is_full_sha_with_guard() -> None:
    text = DOCKER_WF.read_text(encoding="utf-8")
    assert "git rev-parse HEAD" in text
    assert "DOCKER_IDENTITY_FULL_SHA_REQUIRED" in text, (
        "workflow must hard-fail when the revision is not a full SHA"
    )
    assert "org.opencontainers.image.revision=" in text
    assert "type=sha,format=long" in text, "sha tag (long form) must be part of image tags"


def test_image_digest_is_the_artifact_identity() -> None:
    text = DOCKER_WF.read_text(encoding="utf-8")
    assert "steps.build.outputs.digest" in text, (
        "the immutable image digest must be surfaced as the artifact identity"
    )


def test_docker_not_a_required_main_ci_check() -> None:
    """The docker lane must never be load-bearing for main merges (it is a
    secondary environment; branch protection was verified live 2026-09-11)."""
    live = (
        (REPO / "artifacts" / "forensics" / "branch_protection_required_checks.json")
        .read_text(encoding="utf-8")
    )
    assert "docker" not in live.lower()


def test_dockerfile_pins_python_base_by_digest_not_required_but_version_pinned() -> None:
    """The Dockerfile must not float on `python:latest` (it uses 3.11-slim,
    an immutable-digest candidate; digest pinning is a follow-up with the
    Docker owner — pinned BY VERSION today)."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.11-slim" in text
    assert "python:latest" not in text
