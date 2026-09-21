"""Adviser artifact-path containment probes (ML-PHASE1, PR #338 CodeQL fixes).

These are the behavioural counterparts of the CodeQL alerts on the Layer-2
position adviser. Each one drives the REAL validators with an adversarial
payload and asserts the fail-closed contract:

  * ``_safe_under_repo``  (web layer, request -> service boundary)
  * ``_contained_artifact_path`` (service layer, immediately before the sinks)

Both must reject a traversal payload, an absolute outside-repo path and a
symlink payload that points outside the root, while still ACCEPTING a
legitimate in-repo artifact.

The TOCTOU regression these tests pin: ``_safe_under_repo`` used to validate
``q.resolve()`` but return the UNRESOLVED ``q``. That meant the object handed
to ``is_file``/``torch.load`` was not the object that had been checked. The
returned value is now asserted to be resolved and contained.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module", autouse=True)
def _web_auth_disabled() -> None:
    # WEB-AUTH gates every route; the test client must reach the handlers.
    os.environ["NSE_WEB_AUTH_DISABLE"] = "1"


@pytest.fixture()
def repo_root() -> Path:
    from nexus_scalp.web.model_studio_routes import _repo_root

    return _repo_root().resolve()


@pytest.fixture()
def in_repo_artifact(tmp_path: Path, repo_root: Path) -> Path:
    """A legitimate in-repo artifact the validators MUST accept."""
    p = repo_root / "artifacts" / "adviser_containment_probe.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"probe-payload")
    yield p
    if p.is_file():
        p.unlink()


class TestSafeUnderRepo:
    """``_safe_under_repo`` — the web-layer boundary."""

    def test_traversal_is_rejected(self, repo_root: Path) -> None:
        from fastapi import HTTPException

        from nexus_scalp.web.position_adviser_routes import _safe_under_repo

        with pytest.raises(HTTPException) as exc_info:
            _safe_under_repo(Path("../../etc/passwd"))
        assert exc_info.value.status_code == 400
        # The rejection detail must not echo the payload back.
        assert "etc/passwd" not in exc_info.value.detail

    def test_absolute_outside_repo_is_rejected(self, tmp_path: Path) -> None:
        from fastapi import HTTPException

        from nexus_scalp.web.position_adviser_routes import _safe_under_repo

        foreign = tmp_path / "outside_repo.pt"
        with pytest.raises(HTTPException) as exc_info:
            _safe_under_repo(foreign)
        assert exc_info.value.status_code == 400

    def test_symlink_payload_is_rejected(self, tmp_path: Path, repo_root: Path) -> None:
        from fastapi import HTTPException

        from nexus_scalp.web.position_adviser_routes import _safe_under_repo

        # A symlink INSIDE the repo whose target lives OUTSIDE it. The link
        # name looks in-repo; resolve() must follow it and reject.
        foreign = tmp_path / "foreign_weights.pt"
        foreign.write_bytes(b"stolen")
        link = repo_root / "artifacts" / "probe_link.pt"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        try:
            os.symlink(foreign, link)
        except OSError as exc:
            # Windows refuses os.symlink without developer mode / privileges
            # (WinError 1314). Skip rather than silently pass: the probe could
            # not actually run.
            pytest.skip(f"symlinks unavailable on this host: {exc}")
        try:
            with pytest.raises(HTTPException):
                _safe_under_repo(link)
        finally:
            if link.is_symlink() or link.exists():
                link.unlink()

    def test_in_repo_artifact_is_accepted_and_resolved(
        self, in_repo_artifact: Path, repo_root: Path
    ) -> None:
        from nexus_scalp.web.position_adviser_routes import _safe_under_repo

        out = _safe_under_repo(in_repo_artifact)
        # The returned object is the object that was validated (no TOCTOU):
        # resolved, absolute, and inside the root.
        assert out.is_absolute()
        assert out == in_repo_artifact.resolve()
        assert out.is_relative_to(repo_root)
        assert out.is_file()

    def test_relative_in_repo_is_resolved_to_the_root(
        self, in_repo_artifact: Path, repo_root: Path
    ) -> None:
        from nexus_scalp.web.position_adviser_routes import _safe_under_repo

        rel = in_repo_artifact.relative_to(repo_root)
        out = _safe_under_repo(rel)
        assert out == in_repo_artifact.resolve()
        assert out.is_relative_to(repo_root)


class TestContainedArtifactPath:
    """``_contained_artifact_path`` — the barrier immediately before the sinks."""

    def test_traversal_yields_none(self) -> None:
        from nexus_scalp.position_adviser.service import _contained_artifact_path

        assert _contained_artifact_path(Path("../../etc/passwd")) is None

    def test_absolute_outside_repo_yields_none(self, tmp_path: Path) -> None:
        from nexus_scalp.position_adviser.service import _contained_artifact_path

        assert _contained_artifact_path(tmp_path / "outside.pt") is None

    def test_symlink_payload_yields_none(self, tmp_path: Path, repo_root: Path) -> None:
        from nexus_scalp.position_adviser.service import (
            _ADVISER_ROOT,
            _contained_artifact_path,
        )

        foreign = tmp_path / "foreign_weights.pt"
        foreign.write_bytes(b"stolen")
        link = _ADVISER_ROOT / "artifacts" / "probe_link_contained.pt"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        try:
            os.symlink(foreign, link)
        except OSError as exc:
            # Windows refuses os.symlink without developer mode / privileges.
            pytest.skip(f"symlinks unavailable on this host: {exc}")
        try:
            assert _contained_artifact_path(link) is None
        finally:
            if link.is_symlink() or link.exists():
                link.unlink()

    def test_in_repo_artifact_is_accepted_and_resolved(self, in_repo_artifact: Path) -> None:
        from nexus_scalp.position_adviser.service import (
            _ADVISER_ROOT,
            _contained_artifact_path,
        )

        out = _contained_artifact_path(in_repo_artifact)
        assert out is not None
        # The resolved value is what the sinks must receive.
        assert out == in_repo_artifact.resolve()
        assert out.is_absolute()
        assert out.is_relative_to(_ADVISER_ROOT.resolve())
        assert out.is_file()

    def test_repo_root_itself_is_accepted(self, repo_root: Path) -> None:
        # The root directory is the containment boundary, not outside it.
        from nexus_scalp.position_adviser.service import _contained_artifact_path

        assert _contained_artifact_path(repo_root) is not None


class TestServiceLoadRejectsAdversarialPaths:
    """The service-level load() barrier must fail closed, never load."""

    def test_traversal_payload_is_rejected(self, repo_root: Path) -> None:
        from nexus_scalp.position_adviser.service import PositionAdviserService

        svc = PositionAdviserService()
        out = svc.load(
            Path("../../etc/passwd"),
            Path("../../etc/passwd"),
        )
        assert out["status"] == "REJECTED"
        # Never loads: the containment barrier fires before any sink.
        assert svc.status().get("model_id") in ("", None)

    def test_absolute_outside_payload_is_rejected(self, tmp_path: Path) -> None:
        from nexus_scalp.position_adviser.service import PositionAdviserService

        foreign = tmp_path / "foreign.pt"
        foreign.write_bytes(b"not-an-adviser")
        svc = PositionAdviserService()
        out = svc.load(foreign, foreign)
        assert out["status"] == "REJECTED"
