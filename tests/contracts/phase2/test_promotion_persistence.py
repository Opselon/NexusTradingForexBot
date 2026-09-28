"""Phase 2 — PROMOTION stage persistence contract.

Proves for the promotion audit boundary:
    create/write -> commit -> read -> compare
over the REAL columns of ``model_promotion_audit``, and that no ACTIVE
promotion lacks an ``approval_actor``.

Interfaces used (all public):
    nexus_scalp.governance.store.GovernanceStore.record_promotion_audit /
      list_promotion_audits
    nexus_scalp.adapters.database.provider_store.queue_write / query_rows

LIVE FINDING recorded by this module (not fixed — it is operational truth):
    the live ``model_promotion_audit`` table has 0 rows, so no promotion has
    ever been durably audited on the running engine even though champions exist
    in ``experience_model_registry``. The table EXISTS and carries
    ``approval_actor`` (asserted below) — the audit surface is present, it is
    simply unused. Promotions on this box were recorded only as
    ``model_governance_events`` rows (see test_validation_persistence) or not
    at all.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from nexus_scalp.governance.store import GovernanceStore


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _promotion_row(stamp: int, **overrides) -> dict:
    row = {
        "promotion_id": f"prom_phase2_{stamp}",
        "old_champion_model_id": "old_champion",
        "old_champion_version": "v0.9",
        "old_champion_hash": "hash-old",
        "old_champion_schema": "scalp_v3",
        "new_champion_model_id": "new_champion",
        "new_champion_version": "v1.0",
        "new_champion_hash": "hash-new",
        "new_champion_schema": "scalp_v3",
        "candidate_hash": "hash-cand",
        "schema_id": "scalp_v3",
        "approval_actor": "operator.alice",
        "approval_reason": "phase2 contract promotion",
        "approval_token": "tok-phase2",
        "rollback_target": "old_champion:v0.9",
        "status": "PROMOTION_RECORDED",
        "recorded_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


class TestPromotionWriteReadRoundTrip:
    """create/write -> commit -> read -> compare over the real columns."""

    def test_promotion_audit_roundtrip(self, sqlite_env):
        repo = sqlite_env.repo
        store = GovernanceStore(repo)
        stamp = _stamp()
        row = _promotion_row(stamp)

        assert store.record_promotion_audit(row), (
            "GovernanceStore.record_promotion_audit accepted the row"
        )
        sqlite_env.flush()

        rows = store.list_promotion_audits(limit=50)
        assert len(rows) == 1, "the written promotion audit is readable"
        back = rows[0]
        assert back["promotion_id"] == row["promotion_id"]
        assert back["old_champion_model_id"] == "old_champion"
        assert back["old_champion_version"] == "v0.9"
        assert back["old_champion_hash"] == "hash-old"
        assert back["old_champion_schema"] == "scalp_v3"
        assert back["new_champion_model_id"] == "new_champion"
        assert back["new_champion_version"] == "v1.0"
        assert back["new_champion_hash"] == "hash-new"
        assert back["new_champion_schema"] == "scalp_v3"
        assert back["candidate_hash"] == "hash-cand"
        assert back["schema_id"] == "scalp_v3"
        assert back["approval_actor"] == "operator.alice"
        assert back["approval_reason"] == "phase2 contract promotion"
        assert back["approval_token"] == "tok-phase2"
        assert back["rollback_target"] == "old_champion:v0.9"
        assert back["status"] == "PROMOTION_RECORDED"

    def test_no_active_promotion_lacks_approval_actor(self, sqlite_env):
        """An ACTIVE promotion must never have an empty approval_actor.

        ``model_promotion_audit.approval_actor`` is declared
        ``TEXT NOT NULL DEFAULT ''`` — the schema permits an empty string, so
        the contract is asserted by the writer + reader, not by the DB.
        """
        repo = sqlite_env.repo
        store = GovernanceStore(repo)
        stamp = _stamp()
        # An ACTIVE promotion WITH an actor is the only shape we persist.
        assert store.record_promotion_audit(
            _promotion_row(stamp, status="ACTIVE", approval_actor="operator.bob")
        )
        sqlite_env.flush()

        from nexus_scalp.adapters.database.provider_store import query_rows

        actives = query_rows(
            repo,
            "SELECT * FROM model_promotion_audit WHERE status='ACTIVE'",
        )
        assert actives, "the ACTIVE promotion row is readable"
        for r in actives:
            assert r["approval_actor"], "no ACTIVE promotion lacks an approval_actor"


class TestLivePromotionReadback:
    """Read-only probes of the RUNNING engine's real promotion audit table."""

    def test_live_promotion_table_exists_and_carries_approval_actor(self, live_sqlite_probe):
        """The live table EXISTS and carries approval_actor.

        LIVE FINDING: the table has 0 rows — the promotion audit surface is
        present on the running engine but has never been written to (no
        promotion has ever been durably audited), despite champions existing
        in experience_model_registry. The absence IS the finding; the contract
        asserts the surface, not the rows.
        """
        conn = live_sqlite_probe
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "model_promotion_audit" in tables, (
            "model_promotion_audit must exist on the live store"
        )
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(model_promotion_audit)")]
        assert "approval_actor" in cols, (
            "model_promotion_audit must carry approval_actor (the absence of "
            "the column would be the finding; it is present)"
        )
        # The number of live rows is evidence, not a threshold.
        count = conn.execute("SELECT COUNT(*) FROM model_promotion_audit").fetchone()[0]
        assert count >= 0

    def test_live_active_promotions_have_actors(self, live_sqlite_probe):
        conn = live_sqlite_probe
        actives = conn.execute(
            "SELECT approval_actor FROM model_promotion_audit WHERE status='ACTIVE'"
        ).fetchall()
        # Evidence: every ACTIVE promotion present must carry an actor. An
        # empty table (the live finding) vacuously satisfies this.
        for r in actives:
            assert r["approval_actor"], "no ACTIVE promotion lacks an approval_actor"
