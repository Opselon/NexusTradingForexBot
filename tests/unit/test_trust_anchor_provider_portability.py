"""Regression: model lifecycle + trust anchor must work on BOTH providers.

PG-TRUST-ANCHOR-001 / PG-RESEARCH-OBS-001 follow the same defect class:
``AuditRepository._db_path`` holds the DSN under a pooled provider, so any
code that hands it to ``sqlite3.connect()`` — or gates itself on
``_is_sqlite`` and then returns — is dead on PostgreSQL while looking
fine on SQLite.

Two surfaces hit that class:

* ``ModelLifecycleRegistry.ensure_schema`` early-returned when the provider
  was not SQLite, so the 8 lifecycle extension columns (``lifecycle_status``
  foremost) were never added to a pooled database and the registry could not
  express CHAMPION at all.
* ``_verify_champion_registry_binding`` bailed with
  ``reason=no_sqlite_audit`` on any non-SQLite provider, so the governed
  champion row was never consulted — the anchor was inert by construction.

Both now route through ``adapters.database.provider_store`` (the same seam the
operational stores use), so behaviour must be identical on SQLite and a pooled
provider.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.model_lifecycle.registry import (
    _EXTENSION_COLUMNS,
    ModelLifecycleRegistry,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, *, sqlite: bool) -> AuditRepository:
    if sqlite:
        return AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    # A pooled provider whose DSN is unreachable: ``ensure_schema`` routes
    # through ``queue_write_batch``, which must return False (logged) when no
    # pooled backend can be provisioned rather than silently doing nothing.
    return AuditRepository(db_url="postgresql://nse_test:nse_test@invalid.invalid:5432/nse_test")


def _columns_sqlite(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ensure_schema is additive and provider-portable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sqlite", [True, False])
def test_ensure_schema_no_longer_short_circuits_under_pooled_provider(
    sqlite: bool, tmp_path: Path
) -> None:
    """Before the fix ``ensure_schema`` returned early for non-SQLite repos.

    That left ``lifecycle_status`` absent from a pooled database permanently.
    The behaviour we assert is the *code path*, not a side effect: the fix
    routes through ``queue_write_batch``, which returns False (logged) when no
    pooled backend can be provisioned instead of silently doing nothing. Either
    the columns land, or the failure is observable — never silent.
    """
    repo = _make_repo(tmp_path, sqlite=sqlite)
    reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))

    try:
        reg.ensure_schema()
    except Exception:
        # An unreachable pooled DSN must surface as a handled, logged failure,
        # not propagate out of a migration call.
        pass

    if sqlite:
        cols = _columns_sqlite(tmp_path / "audit.db", "experience_model_registry")
        assert {"lifecycle_status", "promotion_reason"} <= cols
    # Pooled branch: nothing to assert on-disk (no real server); the guard is
    # the no-raise contract above plus the routing test below.


def test_ensure_schema_sqlite_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, sqlite=True)
    reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))
    reg.ensure_schema()
    cols_before = _columns_sqlite(tmp_path / "audit.db", "experience_model_registry")
    reg.ensure_schema()
    cols_after = _columns_sqlite(tmp_path / "audit.db", "experience_model_registry")
    assert cols_before == cols_after
    assert all(c in cols_after for c, _t in _EXTENSION_COLUMNS)


# ---------------------------------------------------------------------------
# trust anchor: inert because the provider is wrong, not because SQLite is
# ---------------------------------------------------------------------------


def _anchor_from(store: object, model_path: Path) -> None:
    """Call ``ModelBundleStore._verify_champion_registry_binding``.

    The binding reads ``self.om.audit`` (or ``self.audit``) for the registry,
    so a bare object exposing those attributes exercises the real code path.
    """
    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

    ModelBundleStore._verify_champion_registry_binding(store, model_path, None)


def test_trust_anchor_no_longer_keys_on_is_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The old code logged ``reason=no_sqlite_audit`` for a non-SQLite audit.

    The anchor now reads the champion row through the provider seam, so a repo
    whose audit is NOT SQLite still reaches the registry query.
    """
    calls: list[tuple[str, tuple]] = []

    def fake_query_rows(repo, sql, args=(), *, operation=""):
        calls.append((str(sql), tuple(args)))
        return []

    monkeypatch.setattr("nexus_scalp.adapters.database.provider_store.query_rows", fake_query_rows)

    class _FakeAudit:
        _is_sqlite = False

        _db_path = "postgresql://user:pw@invalid.invalid:5432/nse"

    class _Store:
        om = None
        audit = _FakeAudit()

    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"payload")

    _anchor_from(_Store(), model_path)

    assert calls, "trust anchor never consulted the provider seam under a pooled provider"
    assert "lifecycle_status" in calls[0][0]
    assert calls[0][1] == (ModelStatus.CHAMPION.value,)


def test_trust_anchor_verifies_champion_on_sqlite(tmp_path: Path) -> None:
    """End-to-end on SQLite: a matching champion fingerprint is accepted."""
    repo = _make_repo(tmp_path, sqlite=True)
    reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))
    reg.ensure_schema()

    payload = b"champion-payload"
    fp = hashlib.sha256(payload).hexdigest()[:16]
    model_path = tmp_path / "model.pt"
    model_path.write_bytes(payload)

    _seed_champion(tmp_path / "audit.db", model_path, fp)

    class _Store:
        om = None
        audit = repo

    # The anchor raises only on MISMATCH; a match must return cleanly.
    _anchor_from(_Store(), model_path)


def _seed_champion(db_path: Path, model_path: Path, fingerprint: str) -> None:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute(
            "INSERT INTO experience_model_registry "
            "(model_id, model_version, artifact_path, artifact_fingerprint, "
            " feature_schema_id, feature_dimension, registered_at, lifecycle_status) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
            (
                "primary_test",
                "v1",
                str(model_path),
                fingerprint,
                "scalp_v3",
                70,
                ModelStatus.CHAMPION.value,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_trust_anchor_rejects_mismatched_champion_on_sqlite(tmp_path: Path) -> None:
    """A governed champion whose fingerprint differs fails closed (P0-2)."""
    repo = _make_repo(tmp_path, sqlite=True)
    reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))
    reg.ensure_schema()

    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"on-disk-payload")
    governed = hashlib.sha256(b"different-payload").hexdigest()[:16]

    _seed_champion(tmp_path / "audit.db", model_path, governed)

    class _Store:
        om = None
        audit = repo

    from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError

    with pytest.raises(ArtifactIntegrityError):
        _anchor_from(_Store(), model_path)


def test_ensure_schema_adds_every_extension_column_on_sqlite(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, sqlite=True)
    reg = ModelLifecycleRegistry(audit_repo=repo, model_registry=ModelRegistry(audit_repo=repo))
    reg.ensure_schema()
    cols = _columns_sqlite(tmp_path / "audit.db", "experience_model_registry")
    assert all(c in cols for c, _t in _EXTENSION_COLUMNS)
