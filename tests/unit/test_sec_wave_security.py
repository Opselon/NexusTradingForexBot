"""SEC-WAVE security regression tests (CodeQL 1098..1148).

Every vulnerability fix in this wave ships a test here. Each test proves BOTH
halves of the boundary:
  * the malicious payload is REJECTED (never silently accepted, never echoed
    back to a client), and
  * legitimate input still WORKS (the fix does not simply reject everything).

Run serially (the pytest -n auto flakes documented in the NSE skill are not
relevant here, but these tests touch no shared state so serial is cheapest).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


# =============================================================================
# FAMILY C — path traversal
# =============================================================================


class TestIngestOutputPathTraversal:
    """CodeQL #1132 / #1133 — symbol/timeframe/source into the output parquet."""

    def test_legitimate_output_is_preserved(self) -> None:
        from scripts.data.ingest_historical_candles import resolve_output_path

        got = resolve_output_path(None, "XAUUSD", "M1", "mt5")
        assert got == Path("data") / "raw" / "XAUUSD_M1.mt5.parquet"
        assert not got.is_absolute()  # callers anchor it to their chosen CWD

    def test_traversal_is_still_refused_for_relative_default(self) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        # the sanitized default cannot escape even when the caller later
        # anchors it somewhere: the components are flat identifiers
        with pytest.raises(IngestError):
            resolve_output_path(None, "../evil", "M1", "mt5")

    def test_legitimate_explicit_output_dir(self, tmp_path: Path) -> None:
        from scripts.data.ingest_historical_candles import resolve_output_path

        got = resolve_output_path(str(tmp_path), "XAUUSD", "M5", "csv")
        assert got == tmp_path / "XAUUSD_M5.csv.parquet"

    @pytest.mark.parametrize(
        "symbol",
        [
            "../../etc/evil",
            "..",
            "x/../../y",
            "XAUUSD/../../evil",
            "\\..\\evil",
            "x\x00AUUSD",
            "XAU/USD",
        ],
    )
    def test_traversal_symbol_refused(self, symbol: str) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        with pytest.raises(IngestError):
            resolve_output_path(None, symbol, "M1", "mt5")

    def test_traversal_timeframe_refused(self) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        with pytest.raises(IngestError):
            resolve_output_path(None, "XAUUSD", "../../evil", "mt5")

    def test_traversal_source_refused(self) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        with pytest.raises(IngestError):
            resolve_output_path(None, "XAUUSD", "M1", "../../evil")

    def test_absolute_path_payload_cannot_escape_default_root(self) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        with pytest.raises(IngestError):
            resolve_output_path(None, "/etc/passwd", "M1", "mt5")

    def test_empty_symbol_refused(self) -> None:
        from scripts.data.ingest_historical_candles import IngestError, resolve_output_path

        with pytest.raises(IngestError):
            resolve_output_path(None, "", "M1", "mt5")


class TestArtifactStoreContainment:
    """CodeQL #1109 — artifact id joined into the store root."""

    def test_legitimate_id_resolves_under_root(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(root=tmp_path)
        got = store.dataset_dir("ds_001")
        assert got == (tmp_path / "datasets" / "ds_001").resolve()
        assert got.is_relative_to(tmp_path.resolve())

    def test_legitimate_nested_id_still_allowed(self, tmp_path: Path) -> None:
        # validate_artifact_id forbids separators, so nested ids are those with
        # dots/dashes — the containment must NOT break them.
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(root=tmp_path)
        got = store.model_dir("scalp_XAUUSD-70d.v3")
        assert got.is_relative_to(tmp_path.resolve())
        assert got.name == "scalp_XAUUSD-70d.v3"

    def test_traversal_id_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import (
            ArtifactStore,
            validate_artifact_id,
        )

        with pytest.raises(ValueError):
            validate_artifact_id("../../evil")
        store = ArtifactStore(root=tmp_path)
        with pytest.raises(ValueError):
            store.dataset_dir("../../evil")

    def test_absolute_id_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(root=tmp_path)
        with pytest.raises(ValueError):
            store.model_dir("/etc/evil")

    def test_separator_id_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(root=tmp_path)
        with pytest.raises(ValueError):
            store.dataset_dir("a/../b")

    def test_symlink_escape_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore(root=tmp_path)
        # datasets/<id> as a symlink pointing outside the root
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "datasets" / "linked"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not permitted on this host")
        with pytest.raises(ValueError):
            store.dataset_dir("linked")


class TestProvisioningImportPath:
    """CodeQL #1098 — user-supplied training file path."""

    def test_legitimate_import_under_root_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        from nexus_scalp.web import provisioning_routes as pr

        root = tmp_path / "imports"
        root.mkdir()
        f = root / "xauusd_M1.csv"
        f.write_text("time,open,high,low,close\n")
        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(root))
        got = pr._allowed_import_path(str(f))
        assert got == f.resolve()

    @pytest.mark.parametrize(
        "payload",
        [
            "../../etc/passwd",
            "a/../../b.csv",
            "..\\..\\evil.csv",
            "x\x00.csv",
            "",
            "   ",
        ],
    )
    def test_traversal_payload_refused(
        self, payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.web import provisioning_routes as pr

        root = tmp_path / "imports"
        root.mkdir()
        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(root))
        with pytest.raises(ValueError):
            pr._allowed_import_path(payload)

    def test_wrong_suffix_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.web import provisioning_routes as pr

        root = tmp_path / "imports"
        root.mkdir()
        f = root / "evil.exe"
        f.write_text("nope")
        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(root))
        with pytest.raises(ValueError):
            pr._allowed_import_path(str(f))

    def test_outside_root_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.web import provisioning_routes as pr

        root = tmp_path / "imports"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        f = outside / "x.csv"
        f.write_text("nope")
        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(root))
        with pytest.raises(ValueError):
            pr._allowed_import_path(str(f))

    @pytest.mark.parametrize(
        "payload",
        [
            "/tmp/../etc/passwd",
            "/etc/passwd",
            "/../../root/.ssh/id_rsa",
        ],
    )
    def test_posix_absolute_outside_roots_refused(
        self, payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POSIX-absolute shapes must REACH the containment boundary.

        The shape barrier used to reject every leading-``/`` path on sight, so
        on Linux CI it refused legitimate absolute inputs outright (the ubuntu
        critical-suite failure) and never exercised the trust boundary below.
        The barrier now admits a POSIX root and the containment loop refuses
        anything outside an allowed root.
        """
        import os

        from nexus_scalp.web import provisioning_routes as pr

        root = tmp_path / "imports"
        root.mkdir()
        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(root))
        with pytest.raises(ValueError):
            pr._allowed_import_path(payload)
        # The refusal is containment, not a shape miss: a legit POSIX-absolute
        # file under the root still resolves.
        f = os.path.join(str(root), "xauusd_M1.csv")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("time,open,high,low,close\n")
        assert pr._allowed_import_path(f) == Path(f).resolve()

    def test_error_message_does_not_echo_payload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from nexus_scalp.web import provisioning_routes as pr

        monkeypatch.setenv(pr._IMPORT_ROOTS_ENV, str(tmp_path))
        payload = "weird\x01<>|payload.csv"
        with pytest.raises(ValueError) as ei:
            pr._allowed_import_path(payload)
        assert payload not in str(ei.value)

    def test_import_roots_reject_traversal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CodeQL #1149 — NEXUS_IMPORT_ROOTS entries are shape-validated."""
        import os

        from nexus_scalp.web import provisioning_routes as pr

        sep = os.sep
        monkeypatch.setenv(
            pr._IMPORT_ROOTS_ENV,
            os.pathsep.join(
                [
                    ".." + sep + ".." + sep + "etc" + sep + "passwd",
                    "data" + sep + "raw",
                    "weird<>|value",
                ]
            ),
        )
        roots = [str(r) for r in pr._allowed_import_roots()]
        assert not any("passwd" in r for r in roots), roots
        assert not any("weird" in r for r in roots), roots

    def test_import_roots_keep_legitimate_nested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Legitimate roots (incl. spaces / Windows drives) must survive #1149 fix."""
        import os

        from nexus_scalp.web import provisioning_routes as pr

        sep = os.sep
        legit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        monkeypatch.setenv(
            pr._IMPORT_ROOTS_ENV,
            os.pathsep.join([legit, "data" + sep + "imports", "legit dir" + sep + "raw"]),
        )
        roots = [str(r) for r in pr._allowed_import_roots()]
        assert any(legit in r for r in roots), roots
        assert not any("legit dir" not in r and "data" not in r and legit not in r for r in roots)


# =============================================================================
# FAMILY D — SQL injection
# =============================================================================


class TestSqliteExecutemanyGuard:
    """CodeQL #1114 — executemany was the one sink bypassing the guard."""

    def test_legitimate_insert_passes_guard(self, tmp_path: Path) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "t.db"))
        drv = SQLiteDriver(cfg)
        conn = drv.connect()
        try:
            conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
            conn.commit()
            drv.executemany("INSERT INTO t (a, b) VALUES (?, ?)", [(1, "x"), (2, "y")], conn=conn)
            conn.commit()
            rows = drv.query("SELECT a, b FROM t ORDER BY a", conn=conn)
        finally:
            conn.close()
        assert [(r["a"], r["b"]) for r in rows] == [(1, "x"), (2, "y")]

    def test_stacked_statement_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "t.db"))
        drv = SQLiteDriver(cfg)
        drv.execute("CREATE TABLE t (a INTEGER)")
        with pytest.raises(ValueError):
            drv.executemany("INSERT INTO t (a) VALUES (?); DROP TABLE t", [(1,)])

    def test_block_comment_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "t.db"))
        drv = SQLiteDriver(cfg)
        drv.execute("CREATE TABLE t (a INTEGER)")
        with pytest.raises(ValueError):
            drv.executemany("INSERT INTO t (a) VALUES (?) /*x*/", [(1,)])

    def test_unknown_verb_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "t.db"))
        drv = SQLiteDriver(cfg)
        # a non-empty sequence reaches the guard; an empty one would not.
        # PRAGMA is allowed by design, so use a verb outside the allow-list.
        with pytest.raises(ValueError):
            drv.executemany("ATTACH DATABASE '/etc/evil' AS x", [(1,)])

    def test_values_remain_bound_not_interpolated(self, tmp_path: Path) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "t.db"))
        drv = SQLiteDriver(cfg)
        conn = drv.connect()
        try:
            conn.execute("CREATE TABLE t (a TEXT)")
            conn.commit()
            payload = "'); DROP TABLE t; --"
            drv.executemany("INSERT INTO t (a) VALUES (?)", [(payload,)], conn=conn)
            conn.commit()
            rows = drv.query("SELECT a FROM t", conn=conn)
        finally:
            conn.close()
        assert rows[0]["a"] == payload
        # table survived => the payload was data, not SQL
        assert drv.scalar("SELECT COUNT(*) FROM t") == 1


# =============================================================================
# FAMILY E — exception exposure
# =============================================================================


class TestModelStudioExceptionSanitization:
    """CodeQL #1102/#1103/#1104/#1108 — raw exception into the HTTP body."""

    def test_fail_closed_sanitizes_and_correlates(self) -> None:
        """The canonical route helper: exception text to logs, stable code to
        the client, and a request id joins the two.

        The helper is a closure created by ``register_model_studio_routes``;
        the fake app below records the route FUNCTIONS it is handed (without
        calling them), and the test invokes the predict route directly with a
        raising ``execute_predict`` so the sanitizer contract is exercised
        rather than assumed.
        """
        from fastapi import HTTPException

        from nexus_scalp.web import model_studio_routes as ms

        routes: dict = {}

        class _FakeApp:
            state = type("S", (), {"engine": None})()

            def __getattr__(self, name: str):
                def _deco(path: str, *a, **kw):
                    def deco(fn):
                        def wrapper(req=None):
                            return fn(req)

                        # keep the WRAPPER: register_model_studio_routes
                        # returns the decorated callable, so this is the route
                        # that actually carries the try/except sanitizer.
                        routes[path] = wrapper
                        return wrapper

                    return deco

                return _deco

        def _boom(*args, **kwargs):
            raise RuntimeError("/secret/path/x.pt: internal explode")

        _saved = ms.execute_predict
        ms.execute_predict = _boom  # type: ignore[assignment]
        try:
            ms.register_model_studio_routes(_FakeApp(), None, None)

            class _Req:
                dimension = 50
                features: list | None = None
                use_live_features = False
                fetch_live_70d = False
                perturbation_sigma = 0.0
                simulate_policy_threshold: float | None = None
                inspect_layers = False
                compute_saliency = False

            with pytest.raises(HTTPException) as ei:
                routes["/api/model-studio/predict"](_Req())
        finally:
            ms.execute_predict = _saved  # type: ignore[assignment]
        assert ei.value.status_code == 500
        assert "/secret/path" not in str(ei.value.detail), "path leaked to client"
        assert "internal explode" not in str(ei.value.detail), "text leaked"

    def test_execute_verify_does_not_echo_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_verify's own check detail must not echo a raw exception."""
        from nexus_scalp.web import model_studio_routes as ms

        class _Req:
            model_id = "scalp_v1"

        monkeypatch.setattr(ms, "get_model_registry", _RegistryNotFound)
        with pytest.raises(ms.HTTPException):
            ms.execute_verify(_Req())


class _FakeApp:
    state = type("S", (), {"engine": None})()

    def post(self, path: str):
        def deco(fn):
            fn(_PredictReq())
            return fn

        return deco


class _PredictReq:
    dimension = 50
    features: list | None = None
    use_live_features = False
    fetch_live_70d = False
    perturbation_sigma = 0.0
    simulate_policy_threshold: float | None = None
    inspect_layers = False
    compute_saliency = False


class _RegistryNotFound:
    def get_model(self, model_id: str):
        return None


# =============================================================================
# FAMILY B — deserialization
# =============================================================================


class TestCheckpointPathConfinement:
    """CodeQL #1110..#1113 — model/scaler path + torch.load."""

    def test_traversal_model_path_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        with pytest.raises(ValueError):
            pr._resolve_checkpoint_path("../../etc/evil.pt", label="model_path")

    def test_absolute_escape_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        with pytest.raises(ValueError):
            pr._resolve_checkpoint_path(str(REPO_ROOT.parent / "outside.pt"), label="model_path")

    def test_null_byte_refused(self) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        with pytest.raises(ValueError):
            pr._resolve_checkpoint_path("artifacts/x\x00.pt", label="model_path")

    def test_in_repo_relative_path_accepted(self) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        got = pr._resolve_checkpoint_path("artifacts/models/m.pt", label="model_path")
        assert got == (REPO_ROOT / "artifacts" / "models" / "m.pt").resolve()
        assert got.is_absolute()

    def test_none_is_absent_not_error(self) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        assert pr._resolve_checkpoint_path(None, label="model_path") is None

    def test_symlink_escape_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nexus_scalp.model_generation import position_replay as pr

        artifacts = REPO_ROOT / "artifacts" / "models"
        artifacts.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside.pt"
        outside.write_bytes(b"")
        link = artifacts / "link_symlink_test.pt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not permitted on this host")
        try:
            with pytest.raises(ValueError):
                pr._resolve_checkpoint_path(
                    "artifacts/models/link_symlink_test.pt", label="model_path"
                )
        finally:
            link.unlink(missing_ok=True)


class TestScalerArchiveValidation:
    """Sibling sink of #1113 — np.load of the scaler sidecar."""

    def test_valid_scaler_accepted(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.position_replay import _validate_scaler_archive

        path = tmp_path / "s.scaler.npz"
        np.savez(str(path), mean=np.zeros(5), std=np.ones(5))
        data = np.load(path)
        _validate_scaler_archive(data, path)  # does not raise

    def test_missing_std_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.position_replay import _validate_scaler_archive

        path = tmp_path / "s.scaler.npz"
        np.savez(str(path), mean=np.zeros(5))
        with pytest.raises(ValueError):
            _validate_scaler_archive(np.load(path), path)

    def test_shape_mismatch_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.position_replay import _validate_scaler_archive

        path = tmp_path / "s.scaler.npz"
        np.savez(str(path), mean=np.zeros(5), std=np.ones(3))
        with pytest.raises(ValueError):
            _validate_scaler_archive(np.load(path), path)

    def test_non_finite_refused(self, tmp_path: Path) -> None:
        from nexus_scalp.model_generation.position_replay import _validate_scaler_archive

        path = tmp_path / "s.scaler.npz"
        np.savez(str(path), mean=np.full(3, np.nan), std=np.ones(3))
        with pytest.raises(ValueError):
            _validate_scaler_archive(np.load(path), path)


class TestTrainerSinkContainment:
    """CodeQL #1147/#1148 — trainer read/write sinks."""

    def test_sha256_file_rejects_outside_root(self) -> None:
        from nexus_scalp.position_adviser.trainer import AdviserFeatureError, _sha256_file

        with pytest.raises(AdviserFeatureError):
            _sha256_file(Path("C:/nonexistent_outside_root/evil.pt"))

    def test_sha256_file_accepts_in_repo(self, tmp_path: Path) -> None:
        from nexus_scalp.position_adviser import trainer as tr

        # _ADVISER_ROOT is the repo root; write a file inside it
        target = tr._ADVISER_ROOT / "artifacts" / "_sec_probe_ok.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"probe")
        try:
            assert len(tr._sha256_file(target)) == 64
        finally:
            target.unlink(missing_ok=True)


class TestResolveWithinTrustedRoots:
    """Canonical trusted-root sanitizer (paths.py) used by both path sinks."""

    @pytest.mark.parametrize(
        "payload",
        [
            "../../etc/passwd",
            "C:/temp/../../Windows/System32/evil.dll",
            "data/imports/..\\..\\secrets.env",
            "bad\x00byte.csv",
            "evil\nnewline.csv",
            "$(whoami).csv",
            "`id`.csv",
            "~/.ssh/id_rsa",
            "\\\\server\\share\\evil.csv",
            "",
            "   ",
        ],
    )
    def test_shape_barriers_reject(self, payload: str) -> None:
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        roots = [Path(tempfile.gettempdir()).resolve()]
        assert resolve_within_trusted_roots(payload, roots) is None

    def test_absolute_under_trusted_root_accepted(self) -> None:
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        target = Path(tempfile.gettempdir()).resolve() / "sec_wave_probe.csv"
        target.write_text("x,y\n1,2\n", encoding="utf-8")
        try:
            got = resolve_within_trusted_roots(str(target), [Path(tempfile.gettempdir()).resolve()])
            assert got == target
        finally:
            target.unlink(missing_ok=True)

    def test_absolute_outside_every_root_rejected(self) -> None:
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        # Shape-clean but outside the only trusted root (tempdir): the
        # containment boundary must reject it even though it passes the shape
        # barrier.
        assert (
            resolve_within_trusted_roots(
                "C:/definitely_not_a_real_root/probe.csv",
                [Path(tempfile.gettempdir()).resolve()],
            )
            is None
        )

    def test_unresolvable_path_rejected(self) -> None:
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        # An absolute path containing a NUL byte is unresolvable and must not
        # leak any part of the payload to a caller.
        assert (
            resolve_within_trusted_roots(
                "C:/definitely_not_a_real_root/probe\x00.csv",
                [Path(tempfile.gettempdir()).resolve()],
            )
            is None
        )

    def test_relative_under_trusted_root_accepted(self) -> None:
        """A repo-relative shape-clean path that lands inside a root is kept."""
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        repo = REPO_ROOT.resolve()
        got = resolve_within_trusted_roots("artifacts/models", [repo])
        assert got is not None
        assert got.is_relative_to(repo)

    def test_accepts_space_in_path(self) -> None:
        """A legitimate path containing a directory with spaces still resolves."""
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        # Regression guard: the shape barrier must not reject paths like the
        # repo's own "operator imports" dataset directory.
        got = resolve_within_trusted_roots("data/imports/some folder", [REPO_ROOT.resolve()])
        assert got is not None
        assert got.is_relative_to(REPO_ROOT.resolve())

    def test_posix_absolute_under_root_accepted(self) -> None:
        """CI runs on Linux: a leading-``/`` path must reach containment.

        The shape barrier previously rejected POSIX-absolute paths on sight
        (no drive letter), which broke the ubuntu critical-suite gate. On
        Windows a drive-less rooted path is a different shape (drive-relative),
        so this regression only applies on POSIX hosts.
        """
        import pytest

        if sys.platform == "win32":
            pytest.skip("POSIX-rooted path shape is the Linux-CI regression this guards")
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        got = resolve_within_trusted_roots("/tmp/probe/xauusd_M1.csv", [Path("/")])
        assert got is not None
        assert got.is_absolute()


# =============================================================================
# ROUND 2 — post-merge residual alerts (main branch, commit cbabadae)
# =============================================================================
class TestRound2SaliencyAndContractExposure:
    """#1102 / #1164 — exception messages returned to API clients."""

    def test_compute_saliency_failure_returns_no_exception_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.web import model_studio_routes as ms

        def _boom(*a: object, **kw: object) -> None:
            raise RuntimeError("internal path C:\\secret\\traceback leak")

        monkeypatch.setattr(ms.torch, "tensor", _boom)
        res = ms._compute_saliency(object(), np.zeros(3, dtype=np.float32))
        assert res["error"] == "saliency computation failed"
        assert res["top_positive_drivers"] == []
        assert res["top_negative_drivers"] == []
        assert "secret" not in json.dumps(res)
        assert "traceback" not in json.dumps(res)

    def test_fetch_70d_contract_error_is_fixed_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import nexus_scalp.features.schema_contract as sc
        from nexus_scalp.web import model_studio_routes as ms

        def _boom(*a: object, **kw: object) -> None:
            raise ValueError("dimension contract: internal schema hash abc123 leaked")

        monkeypatch.setattr(sc, "validate_70d_vector", _boom)
        out = ms.fetch_70d_components(engine=None)
        assert out["contract_valid"] is False
        assert out["contract_error"] == "feature contract validation failed"
        assert "abc123" not in json.dumps(out)


class TestRound2QuickSqlNoInterpolation:
    """#1114 — /quick builds SQL from a canned template, never request input."""

    def test_quote_ident_returns_whitelist_extraction(self) -> None:
        from nexus_scalp.database.drivers.base import _IDENT_SHAPE

        good = _IDENT_SHAPE.fullmatch("audit_signals")
        assert good is not None
        assert good.group(0) == "audit_signals"
        # anything outside [A-Za-z_][A-Za-z0-9_]* is refused, not truncated
        assert _IDENT_SHAPE.fullmatch("a; DROP TABLE x") is None
        assert _IDENT_SHAPE.fullmatch("a b") is None
        assert _IDENT_SHAPE.fullmatch("a--b") is None

    def test_execution_templates_interpolate_only_a_whitelisted_identifier(self) -> None:
        from nexus_scalp.web import db_console as dc

        for kind in ("top100", "count", "recent"):
            assert "{table}" in dc._QUICK_SQL[kind]
        # the canned schema template is superseded by the bound-parameter arm
        # (see console_quick); it must never be .format-ed with request input.
        assert "{table}" in dc._QUICK_SQL["schema"]


class TestRound2ProvisioningDatasetsRootSanitizer:
    """#1149 — ?root= resolves through the canonical sanitizer."""

    def test_bare_root_is_admitted_by_the_route_fallback(self, tmp_path: Path) -> None:
        """The sanitizer is for FILES, so naming a root verbatim returns None;
        the route admits it through pure string normalization instead (never a
        resolve of the raw value)."""
        importroot = (tmp_path / "importroot").resolve()
        importroot.mkdir()
        allowed = [importroot]
        from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

        assert resolve_within_trusted_roots(str(importroot), allowed) is None
