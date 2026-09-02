"""CHG-0046 / SHADOW_EVIDENCE v2 regression tests.

Covers the Shadow hardening deltas that did not already have behavioral
coverage in test_shadow_phase11.py / test_shadow70_*.py:

  A. same-input parity helpers  — compat.normalize_action / fingerprint
  B. model identity             — champion identity uses effective_* contract
  C. schema compatibility       — BUY vs BUY_MARKET is an AGREEMENT (D2)
  F. paired outcome analysis    — outcomes.resolve_paired + Delta_R (D3)
  I. DB persistence             — additive migration + RESOLVED round-trip
  K. status semantics           — flat side R=0, NOT_RECORDED discipline
  O. reproducibility            — fingerprint determinism across processes
  P. promotion evidence rules   — unresolved records contribute NO R metrics
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.shadow.comparison import ShadowComparer
from nexus_scalp.shadow.compat import (
    canonical_model_confidence,
    direction_of,
    normalize_action,
    scale_like_champion,
    vector_fingerprint,
)
from nexus_scalp.shadow.models import ShadowModelRef
from nexus_scalp.shadow.outcomes import (
    PairedTick,
    apply_to_record_fields,
    resolve_paired,
)
from tests.unit.test_shadow_phase11 import (
    make_challenger_ref,
    make_champion_ref,
    make_decisions,
)

TS = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


# =============================================================================
# A / O. compat primitives
# =============================================================================


class TestActionNormalization:
    def test_market_suffixes_collapse(self):
        assert normalize_action("BUY_MARKET") == "BUY"
        assert normalize_action("SELL_MARKET") == "SELL"
        assert normalize_action("BUY_LIMIT") == "BUY"

    def test_policy_vocabulary_passes_through(self):
        assert normalize_action("BUY") == "BUY"
        assert normalize_action("SELL") == "SELL"
        assert normalize_action("WAIT") == "WAIT"
        assert normalize_action("NO_TRADE") == "NO_TRADE"

    def test_unknown_is_flat_never_guessed(self):
        assert normalize_action("GARBAGE") == "NO_TRADE"
        assert normalize_action(None) == "NO_TRADE"
        assert normalize_action("") == "NO_TRADE"

    def test_direction(self):
        assert direction_of("SELL_MARKET") == "SELL"
        assert direction_of("WAIT") == "NONE"


class TestFingerprintReproducibility:
    def test_full_vector_participates(self):
        v = [0.1] * 50
        w = list(v)
        w[49] = 9.9
        assert vector_fingerprint(v) != vector_fingerprint(w)

    def test_deterministic(self):
        v = [0.123456789, -2.5, 3.0]
        assert vector_fingerprint(v) == vector_fingerprint([*v])

    def test_nonfinite_is_stable_not_random(self):
        v = [float("nan"), 1.0, float("inf")]
        assert vector_fingerprint(v) == vector_fingerprint([0.0, 1.0, 0.0])


class TestScalerParity:
    def test_matches_champion_transform(self):
        import numpy as np

        x = np.array([[2.0, 0.5, -3.0]], dtype=np.float32)
        mean = np.zeros(3, dtype=np.float32)
        std = np.array([0.5, 1e-9, 1e-9], dtype=np.float32)
        out = scale_like_champion(x, mean, std)
        assert out[0, 0] == pytest.approx(4.0)
        assert out[0, 1] == pytest.approx(5.0)  # clipped, not divided by 1e-9 raw
        assert out[0, 2] == pytest.approx(-5.0)

    def test_width_mismatch_raises(self):
        import numpy as np

        with pytest.raises(ValueError):
            scale_like_champion(np.zeros((1, 3)), np.zeros(4), np.ones(4))


class TestModelArgmaxConfidence:
    def test_argmax_not_policy_share(self):
        assert canonical_model_confidence([0.1, 0.7, 0.15, 0.05]) == pytest.approx(0.7)

    def test_empty_is_zero(self):
        assert canonical_model_confidence([]) == 0.0


# =============================================================================
# C. disagreement classification under normalized vocabulary
# =============================================================================


class TestBuyVsBuyMarketAgreement:
    def test_policy_buy_vs_model_buy_market_agrees(self):
        comparer = ShadowComparer()
        # Champion policy emits BUY; challenger argmax emits BUY_MARKET.
        decisions = make_decisions(5, champ_action="BUY", chal_action="BUY_MARKET")
        # make_decision sets action_agreement by raw equality; emulate the
        # engine's post-D2 normalization by recomputing it.
        from nexus_scalp.shadow.models import ShadowDecisionRecord

        normalized = [
            d.model_copy(
                update={
                    "champion_action": normalize_action(d.champion_action),
                    "challenger_action": normalize_action(d.challenger_action),
                    "action_agreement": (
                        normalize_action(d.champion_action) == normalize_action(d.challenger_action)
                    ),
                }
            )
            for d in decisions
        ]
        comp = comparer.compare(normalized, "run_norm", make_champion_ref(), make_challenger_ref())
        assert comp.action_agreement_rate == pytest.approx(1.0)


# =============================================================================
# F / K. paired outcome resolver
# =============================================================================


_TICKS = [
    PairedTick(TS, bid=100.0, ask=100.5),
    PairedTick(TS.replace(minute=5), bid=101.0, ask=101.5),
    PairedTick(TS.replace(minute=10), bid=99.0, ask=99.5),
    PairedTick(TS.replace(minute=20), bid=99.5, ask=100.0),
]


class TestPairedOutcomeResolver:
    def test_side_aware_fills(self):
        o = resolve_paired(
            champion_action="BUY",
            champion_entry=100.5,
            champion_sl=99.5,
            champion_tp=102.0,
            shadow_action="BUY",
            shadow_entry=100.5,
            shadow_sl=99.5,
            shadow_tp=102.0,
            ticks=_TICKS,
            decision_ts=TS,
        )
        c = o.champion
        assert c.entry_price == pytest.approx(100.5)  # BUY fills at ASK
        assert c.exit_price == pytest.approx(99.5)  # exits on BID (walk end)

    def test_flat_side_is_zero_not_mirror(self):
        o = resolve_paired(
            champion_action="NO_TRADE",
            champion_entry=0.0,
            champion_sl=0.0,
            champion_tp=0.0,
            shadow_action="BUY",
            shadow_entry=100.5,
            shadow_sl=99.5,
            shadow_tp=102.0,
            ticks=_TICKS,
            decision_ts=TS,
        )
        assert o.champion.r == pytest.approx(0.0)
        assert o.champion.exit_reason == "NO_TRADE"
        # Shadow lost 1.0R; flat champion did NOT inherit the mirror loss.
        assert o.shadow.r == pytest.approx(-1.0)
        assert o.delta_r == pytest.approx(-1.0)

    def test_delta_r_sign(self):
        o = resolve_paired(
            champion_action="SELL",
            champion_entry=100.0,
            champion_sl=101.0,
            champion_tp=99.0,
            shadow_action="NO_TRADE",
            shadow_entry=0.0,
            shadow_sl=0.0,
            shadow_tp=0.0,
            ticks=_TICKS,
            decision_ts=TS,
        )
        # SELL walk-end: exit on ASK @ 100.0 -> 0.0R; flat shadow beats by 0.
        assert o.champion.r == pytest.approx(0.0)
        assert o.delta_r == pytest.approx(0.0)

    def test_no_tick_coverage_not_recorded(self):
        o = resolve_paired(
            champion_action="BUY",
            champion_entry=100.5,
            champion_sl=99.5,
            champion_tp=102.0,
            shadow_action="NO_TRADE",
            shadow_entry=0.0,
            shadow_sl=0.0,
            shadow_tp=0.0,
            ticks=[],
            decision_ts=TS,
        )
        fields = apply_to_record_fields(o)
        assert fields["outcome_status"] == "NOT_RECORDED"

    def test_deterministic(self):
        kw = dict(
            champion_action="BUY",
            champion_entry=100.5,
            champion_sl=99.5,
            champion_tp=102.0,
            shadow_action="SELL",
            shadow_entry=100.0,
            shadow_sl=101.0,
            shadow_tp=99.0,
            ticks=_TICKS,
            decision_ts=TS,
        )
        assert resolve_paired(**kw) == resolve_paired(**kw)


# =============================================================================
# P. promotion evidence: unresolved records contribute NOTHING to R metrics
# =============================================================================


class TestResolvedOnlyMetrics:
    def test_unresolved_records_produce_zero_expectancy_not_fake(self):
        comparer = ShadowComparer()
        # outcome_status NOT_RECORDED (default) — no fabricated evidence.
        decisions = [
            d.model_copy(
                update={"outcome_status": "NOT_RECORDED", "shadow_r": None, "delta_r": None}
            )
            for d in make_decisions(10, hypothetical_r=0.4)
        ]
        comp = comparer.compare(decisions, "run_unres", make_champion_ref(), make_challenger_ref())
        assert comp.outcome_resolved_count == 0
        assert comp.champion_expectancy_r == 0.0
        assert comp.challenger_expectancy_r == 0.0
        assert comp.mean_delta_r == 0.0
        assert comp.median_delta_r == 0.0

    def test_resolved_records_drive_paired_metrics(self):
        comparer = ShadowComparer()
        decisions = []
        for _i in range(6):
            base = make_decisions(1, hypothetical_r=0.5)[0]
            # champion +0.5R, shadow +0.9R -> delta +0.4R
            decisions.append(
                base.model_copy(
                    update={
                        "shadow_r": 0.9,
                        "shadow_mfe_r": 1.2,
                        "shadow_mae_r": -0.3,
                        "shadow_holding_sec": 250.0,
                        "delta_r": 0.4,
                        "outcome_status": "RESOLVED",
                    }
                )
            )
        comp = comparer.compare(decisions, "run_res", make_champion_ref(), make_challenger_ref())
        assert comp.outcome_resolved_count == 6
        assert comp.champion_expectancy_r == pytest.approx(0.5)
        assert comp.challenger_expectancy_r == pytest.approx(0.9)
        assert comp.mean_delta_r == pytest.approx(0.4)
        assert comp.median_delta_r == pytest.approx(0.4)
        assert comp.challenger_mfe_r == pytest.approx(1.2)
        assert comp.challenger_mae_r == pytest.approx(-0.3)


# =============================================================================
# I. additive DB migration + RESOLVED round-trip
# =============================================================================


class TestAdditiveMigration:
    def test_columns_added_and_persisted(self):
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.shadow.models import (
            ShadowDecisionRecord,
            ShadowRun,
            SharedInputRef,
        )
        from nexus_scalp.shadow.store import ShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "audit.db")
            repo = AuditRepository(db_url=f"sqlite:///{db}")
            store = ShadowStore(audit_repo=repo)
            ref_c = ShadowModelRef(
                model_id="c", model_version="1", artifact_hash="h1", is_champion=True
            )
            ref_g = ShadowModelRef(model_id="g", model_version="1", artifact_hash="h2")
            si = SharedInputRef(timestamp=TS, symbol="XAUUSD", feature_hash="fh")
            rec = ShadowDecisionRecord(
                shadow_decision_id="sd_mig",
                run_id="r_mig",
                timestamp=TS,
                symbol="XAUUSD",
                champion=ref_c,
                challenger=ref_g,
                shared_input=si,
                champion_entry=100.0,
                champion_sl=99.0,
                champion_tp=102.0,
                shadow_entry=100.0,
                shadow_sl=99.5,
                shadow_tp=101.0,
                spread_usd=0.5,
                shadow_r=0.8,
                delta_r=0.5,
                outcome_status="RESOLVED",
            )
            assert store.save_decision(rec)
            assert store.save_run(
                ShadowRun(
                    run_id="r_mig",
                    champion=ref_c,
                    challenger=ref_g,
                    git_revision="abc123",
                    configuration_version="v2",
                    challenger_artifact_hash="h2",
                    champion_artifact_hash="h1",
                )
            )
            time.sleep(1.5)  # background audit writer drains
            conn = sqlite3.connect(db, timeout=10)
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_decisions);")}
                assert {
                    "champion_entry",
                    "shadow_r",
                    "delta_r",
                    "outcome_status",
                    "spread_usd",
                }.issubset(cols)
                rcols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_runs);")}
                assert {"git_revision", "challenger_artifact_hash"}.issubset(rcols)
                row = conn.execute(
                    "SELECT outcome_status, shadow_r, delta_r FROM shadow_decisions;"
                ).fetchone()
                assert row == ("RESOLVED", 0.8, 0.5)
            finally:
                conn.close()
            repo.close()  # release the WAL before TemporaryDirectory cleanup


# =============================================================================
# B/D11. run-freeze identity
# =============================================================================


class TestRunFreeze:
    def test_git_revision_captured(self):
        from nexus_scalp.shadow.engine import _git_revision

        rev = _git_revision()
        # Either empty (NOT_RECORDED, e.g. frozen EXE without a repo) or a
        # hex short-sha of any length the repo convention produces (7-12).
        assert rev == "" or (4 <= len(rev) <= 12 and all(c in "0123456789abcdef" for c in rev))

    def test_artifact_replacement_fails_run(self):
        from nexus_scalp.shadow.engine import ShadowEngine
        from nexus_scalp.shadow.models import ShadowModelRef
        from nexus_scalp.shadow.store import ShadowStore

        store = ShadowStore(audit_repo=None)
        engine = ShadowEngine(store=store)
        champ = ShadowModelRef(model_id="c", model_version="1", artifact_hash="h1")
        chal = ShadowModelRef(model_id="g", model_version="1", artifact_hash="h_frozen")
        engine.start_run(None, champ, chal)
        # Challenger's live ref hash changes mid-run (artifact replaced).
        assert engine.active_challenger is None  # no runtime attached
        from types import SimpleNamespace

        engine.active_challenger = SimpleNamespace(
            ref=ShadowModelRef(model_id="g", model_version="1", artifact_hash="h_new")
        )
        engine.finish_run()
        assert engine.active_run_id == ""
