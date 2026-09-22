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
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture(scope="module", autouse=True)
def _web_auth_disabled() -> Generator[None, None, None]:
    # WEB-AUTH gates every route; the test client must reach the handlers.
    # Save/restore around the whole module: a bare os.environ write left
    # DISABLE=1 set forever, leaking into the worker's later tests and
    # silently de-arming the 401 auth-contract probes.
    prev = os.environ.get("NSE_WEB_AUTH_DISABLE")
    os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
        else:
            os.environ["NSE_WEB_AUTH_DISABLE"] = prev


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


class TestSafeModelId:
    """A request-supplied ``model_id`` is joined into artifact filenames.

    It must not be able to carry a path component, or a ``../../`` id would
    make the trainer write outside ``output_dir``.
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "..",
            "a/b",
            "C:evil",
            "sub/dir/model",
            "",
            "x" * 200,
        ],
    )
    def test_unsafe_ids_are_refused(self, bad: str) -> None:
        from nexus_scalp.position_adviser.trainer import _SAFE_MODEL_ID

        assert _SAFE_MODEL_ID.fullmatch(bad) is None

    @pytest.mark.parametrize(
        "good",
        [
            "pos_adviser_1789000000",
            "pos_adviser_tune_123_42_9999",
            "adviser_v2",
        ],
    )
    def test_legitimate_ids_are_accepted(self, good: str) -> None:
        from nexus_scalp.position_adviser.trainer import _SAFE_MODEL_ID

        assert _SAFE_MODEL_ID.fullmatch(good) is not None


class TestSanitizers:
    """The positive sanitizers that break the CodeQL taint chain.

    A request-supplied string is reduced to a whitelist-only value into a NEW
    object, and only that object is used to build paths downstream. These pin
    both halves of that contract: the reduction itself, and the fact that the
    sanitized value cannot carry a path component.
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "..",
            "a/../../b",
            "C:evil",
            "sub\\..\\dir",
            "",
            "   ",
            "a\x00b",
            "a;b",
            "a|b",
            "a'b",
            'a"b',
            "$HOME/x",
            "a\nb",
        ],
    )
    def test_rel_path_refuses_adversarial_input(self, bad: str) -> None:
        from nexus_scalp.position_adviser.paths import AdviserPathError, sanitize_rel_path

        with pytest.raises(AdviserPathError) as exc_info:
            sanitize_rel_path(bad, label="probe")
        # The exception message must not echo the payload back at a client.
        assert (bad.strip() or "x") not in str(exc_info.value)

    @pytest.mark.parametrize(
        "good",
        [
            "artifacts/position_adviser/model.pt",
            r"artifacts\position_adviser\model.pt",
            "pos_adviser_1789000000.pt",
            "data/positions/pos_ds_438479f6cdcd7668.parquet",
        ],
    )
    def test_rel_path_accepts_legitimate_input(self, good: str) -> None:
        from nexus_scalp.position_adviser.paths import sanitize_rel_path

        out = sanitize_rel_path(good, label="probe")
        # The sanitized value is relative: it cannot name a root of its own.
        assert not out.is_absolute()
        assert not out.is_absolute()
        # No component can be a parent-directory reference.
        assert ".." not in out.parts

    def test_rel_path_normalizes_redundant_separators(self) -> None:
        from nexus_scalp.position_adviser.paths import sanitize_rel_path

        out = sanitize_rel_path("artifacts//position_adviser", label="probe")
        assert out == Path("artifacts/position_adviser")

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "..",
            "...",
            ".",
            "a/../../b",
            "/etc/passwd",
            "C:evil",
            "",
            "   ",
        ],
    )
    def test_repo_relative_refuses_traversal(self, bad: str, repo_root: Path) -> None:
        from nexus_scalp.position_adviser.paths import (
            AdviserPathError,
            sanitize_repo_relative,
        )

        with pytest.raises(AdviserPathError):
            sanitize_repo_relative(bad, root=repo_root, label="probe")

    @pytest.mark.parametrize("bad", ["/etc/passwd", "/artifacts/position_adviser"])
    def test_root_anchored_path_is_refused(self, bad: str, repo_root: Path) -> None:
        from nexus_scalp.position_adviser.paths import (
            AdviserPathError,
            sanitize_repo_relative,
        )

        # A leading separator is refused on every platform: on POSIX it escapes
        # the root, and on Windows "/etc/passwd" is relative (no drive) yet
        # unanchored. Callers pass repo-relative names, never root-anchored ones.
        with pytest.raises(AdviserPathError):
            sanitize_repo_relative(bad, root=repo_root, label="probe")

    def test_repo_relative_narrows_an_absolute_in_repo_path(
        self, repo_root: Path, in_repo_artifact: Path
    ) -> None:
        from nexus_scalp.position_adviser.paths import sanitize_repo_relative

        out = sanitize_repo_relative(in_repo_artifact, root=repo_root, label="probe")
        assert out == in_repo_artifact.relative_to(repo_root)
        assert not out.is_absolute()

    def test_repo_relative_rejects_an_absolute_outside_path(
        self, tmp_path: Path, repo_root: Path
    ) -> None:
        from nexus_scalp.position_adviser.paths import (
            AdviserPathError,
            sanitize_repo_relative,
        )

        with pytest.raises(AdviserPathError):
            sanitize_repo_relative(tmp_path / "foreign.pt", root=repo_root, label="probe")

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "..",
            "a/b",
            "C:evil",
            "",
            "x" * 200,
            "a;b",
            "a b/root",
        ],
    )
    def test_name_refuses_adversarial_input(self, bad: str) -> None:
        from nexus_scalp.position_adviser.paths import sanitize_name

        # A name substitutes a fallback rather than raising: it is cosmetic.
        assert sanitize_name(bad, fallback="fallback") == "fallback"

    @pytest.mark.parametrize(
        "good",
        [
            "pos_adviser_1789000000",
            "pos_adviser_tune_123_42_9999",
            "adviser_v2",
        ],
    )
    def test_name_accepts_legitimate_input(self, good: str) -> None:
        from nexus_scalp.position_adviser.paths import sanitize_name

        assert sanitize_name(good, fallback="fallback") == good


class TestTrainerWritesOnlySanitizedPaths:
    """An end-to-end run of the trainer must keep every write inside the root.

    Exercises the real sink CodeQL flags (``mkdir`` + ``torch.save``) with a
    ``model_id`` and an ``output_dir`` that would both escape if the sanitizer
    were removed.
    """

    @pytest.fixture()
    def small_dataset(self, repo_root: Path) -> Path:
        """A real generator-produced position dataset, copied under the repo.

        The trainer requires >= 20 OOS rows and >= 50 trainable rows, so the
        small 216-row dataset is used (train 130 / val 26 / oos 26).
        """
        src = Path(__file__).resolve().parents[3] / (
            "artifacts/datasets/pos_ds_711e478443e88642.parquet"
        )
        if not src.is_file():
            pytest.skip(f"position dataset fixture absent: {src.name}")
        dst = repo_root / "artifacts" / "position_adviser_tests" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        yield dst
        if dst.is_file():
            dst.unlink()

    def test_adversarial_model_id_cannot_escape_output_dir(
        self, small_dataset: Path, repo_root: Path
    ) -> None:
        from nexus_scalp.position_adviser.trainer import train_position_adviser

        out_dir = repo_root / "artifacts" / "position_adviser_tests"
        before = {p.name for p in out_dir.glob("*")} if out_dir.is_dir() else set()

        res = train_position_adviser(
            small_dataset,
            output_dir=out_dir,
            epochs=1,
            batch_size=32,
            model_id="../../pwned",
        )

        try:
            # The adversarial id was replaced, and the artifacts landed inside
            # the output directory — nothing was written outside it.
            assert res.model_id != "../../pwned"
            assert ".." not in Path(res.model_id).parts
            wp = out_dir / f"{res.model_id}.pt"
            assert wp.is_file()
            # Nothing escaped to the parent of the artifact tests dir.
            escaped = (out_dir.parent).glob("pwned*")
            assert list(escaped) == []
            after = {p.name for p in out_dir.glob("*")}
            new = after - before
            # Every new file lives under the sanitized model id.
            assert new, "no artifacts were written"
            for name in new:
                assert name.startswith(res.model_id), name
        finally:
            for p in out_dir.glob(f"{res.model_id}.*"):
                p.unlink(missing_ok=True)

    def test_adversarial_output_dir_is_refused(self, small_dataset: Path) -> None:
        from nexus_scalp.position_adviser.paths import AdviserPathError
        from nexus_scalp.position_adviser.trainer import train_position_adviser

        with pytest.raises(AdviserPathError):
            train_position_adviser(
                small_dataset,
                output_dir="../../evil_out",
                epochs=1,
                batch_size=32,
            )
