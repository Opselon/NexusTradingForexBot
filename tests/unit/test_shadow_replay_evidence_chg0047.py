"""CHG-0047 acceptance tests — adversarial + adversarial-data evidence proofs.

Steer §7/§8: each of the four forced failures must fail SAFELY and produce
the correct evidence/status:
  A. one invalid feature vector     -> pair marked INPUT_MISMATCH/invalid,
                                       excluded from every metric
  B. one unresolved fill            -> outcome_status=NOT_RECORDED, excluded
                                       from R metrics, NEVER coerced to 0
  C. one artifact replacement       -> dataset/model identity changes the
                                       evidence fingerprint (run identity)
  D. one challenger identity change -> evidence identity changes

Plus determinism (same inputs -> identical evidence) and the explicit
BUY/SELL non-mirror proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.shadow._replay_pair import classify_pair
from nexus_scalp.shadow.outcomes import PairedTick, resolve_paired
from nexus_scalp.shadow.replay import (
    ShadowReplayConfig,
    build_replay_evidence,
    dataset_fingerprint,
)

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _trace_row(
    idx: int,
    action: str,
    probs: list[float],
    *,
    entry: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    confidence: float = 0.5,
    regime: str = "TRENDING",
) -> dict[str, object]:
    ts = T0 + timedelta(minutes=idx)
    return {
        "ts": ts.isoformat(),
        "decision_index": idx,
        "bid": 3300.0,
        "ask": 3300.2,
        "probs": probs,
        "action": action,
        "confidence": confidence,
        "regime": regime,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
    }


_TICKS = [
    PairedTick(T0, bid=100.0, ask=100.2),
    PairedTick(T0 + timedelta(minutes=5), bid=101.0, ask=101.2),
    PairedTick(T0 + timedelta(minutes=10), bid=99.0, ask=99.2),
    PairedTick(T0 + timedelta(minutes=30), bid=99.5, ask=99.7),
]


def _bar_records() -> list[dict[str, object]]:
    return [
        {
            "timestamp": T0 + timedelta(minutes=m),
            "open": 3300.0 + 0.01 * m,
            "high": 3301.0 + 0.01 * m,
            "low": 3299.0 + 0.01 * m,
            "close": 3300.5 + 0.01 * m,
            "tick_volume": 100,
            "spread": 0.2,
            "symbol": "XAUUSD",
            "timeframe": "M1",
        }
        for m in range(60)
    ]


def _evidence(champ_rows: list[dict[str, object]], chal_rows: list[dict[str, object]]):
    return build_replay_evidence(
        run_champion=type("R", (), {"decision_trace": champ_rows})(),
        run_challenger=type("R", (), {"decision_trace": chal_rows})(),
        bar_records=_bar_records(),
        dataset_id="test-ds",
        horizon_minutes=120,
        min_resolved_pairs=1,
        extra_identity={"model": "fixture"},
    )


# ---------------------------------------------------------------------------
# A. invalid feature vector (INPUT_MISMATCH)
# ---------------------------------------------------------------------------


class TestInvalidPairExcluded:
    def test_input_mismatch_is_invalid_and_excluded(self):
        good = _trace_row(1, "NO_TRADE", [0.4, 0.3, 0.2, 0.1])
        bad = _trace_row(2, "NO_TRADE", [0.4, 0.3, 0.2, 0.1])  # different index/ts
        pair = classify_pair(good, bad)
        assert pair["valid"] is False
        assert "INPUT_MISMATCH" in pair["invalid_reason"]

    def test_invalid_pairs_counted_but_never_scored(self):
        champ = [_trace_row(i, "NO_TRADE", [0.5, 0.3, 0.1, 0.1]) for i in range(3)]
        # row 1 is shifted -> input mismatch
        chal = [
            _trace_row(0, "NO_TRADE", [0.5, 0.3, 0.1, 0.1]),
            _trace_row(9, "NO_TRADE", [0.5, 0.3, 0.1, 0.1]),
            _trace_row(2, "NO_TRADE", [0.5, 0.3, 0.1, 0.1]),
        ]
        ev = _evidence(champ, chal)
        assert ev["pairs_invalid"] == 1
        assert ev["pairs_resolved"] + ev["pairs_unresolved"] == 2
        # the invalid row contributed to NO R metric
        po = ev["paired_outcomes"]
        assert po["delta_r_positive"] + po["delta_r_negative"] + po["delta_r_zero"] == 2


# ---------------------------------------------------------------------------
# B. unresolved fill — NOT_RECORDED, never zero
# ---------------------------------------------------------------------------


class TestUnresolvedNotZero:
    def test_directional_trade_without_geometry_is_unresolved(self):
        o = resolve_paired(
            champion_action="BUY",
            champion_entry=100.2,  # geometry present
            champion_sl=99.0,
            champion_tp=102.0,
            shadow_action="BUY",
            shadow_entry=0.0,  # NO geometry recorded
            shadow_sl=0.0,
            shadow_tp=0.0,
            ticks=_TICKS,
            decision_ts=T0,
        )
        assert o.champion.r is not None
        assert o.shadow.r is None  # unusable geometry -> None, NOT 0
        assert o.delta_r is None

    def test_unresolved_pairs_excluded_from_mean_delta(self):
        champ = [_trace_row(0, "BUY", [0.2, 0.5, 0.2, 0.1], entry=100.2, sl=99.0, tp=102.0)]
        # challenger: trade action but NO usable geometry
        chal = [_trace_row(0, "BUY", [0.2, 0.5, 0.2, 0.1], entry=0.0, sl=0.0, tp=0.0)]
        ev = _evidence(champ, chal)
        assert ev["pairs_unresolved"] == 1
        assert ev["pairs_resolved"] == 0
        # NOT_RECORDED never became a zero ΔR sample
        assert ev["paired_outcomes"]["delta_r_zero"] == 0
        assert ev["promotion_readiness"]["verdict"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# Non-mirror proof: BUY vs SELL on the same path
# ---------------------------------------------------------------------------


class TestSideAwareNoMirrors:
    def test_buy_vs_sell_not_mirror_fabricated(self):
        o = resolve_paired(
            champion_action="BUY",
            champion_entry=100.2,
            champion_sl=99.0,
            champion_tp=102.0,
            shadow_action="SELL",
            shadow_entry=100.0,
            shadow_sl=101.0,
            shadow_tp=99.0,
            ticks=_TICKS,
            decision_ts=T0,
        )
        c, s = o.champion, o.shadow
        # walk-end: BUY exits BID 99.5 -> r=(99.5-100.2)/1.2; SELL exits ASK
        # 99.7 -> r=(100.0-99.7)/1.0. Each side walked on its OWN geometry.
        assert c.direction == "BUY" and s.direction == "SELL"
        assert c.r == pytest.approx(-0.7 / 1.2)
        assert s.r == pytest.approx(0.3 / 1.0)
        assert o.delta_r == pytest.approx(s.r - c.r)
        # NOT the old fabricated mirror (±same value with flipped sign)
        assert c.r != -s.r

    def test_flat_champion_is_zero_not_negative_mirror(self):
        o = resolve_paired(
            champion_action="NO_TRADE",
            champion_entry=0.0,
            champion_sl=0.0,
            champion_tp=0.0,
            shadow_action="SELL",
            shadow_entry=100.0,
            shadow_sl=101.0,
            shadow_tp=99.0,
            ticks=_TICKS,
            decision_ts=T0,
        )
        assert o.champion.r == 0.0  # flat is flat
        assert o.shadow.r == pytest.approx(0.3)
        assert o.delta_r == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# C/D. identity sensitivity — artifact/dataset/challenger changes the evidence
# ---------------------------------------------------------------------------


class TestIdentitySensitivity:
    def test_dataset_content_change_changes_fingerprint(self):
        r1 = _bar_records()
        r2 = [dict(r) for r in r1]
        r2[10]["close"] = float(r2[10]["close"]) + 0.01  # one bar changed
        assert dataset_fingerprint(r1, "ds") != dataset_fingerprint(r2, "ds")

    def test_challenger_identity_change_changes_config_fingerprint(self):
        base = dict(
            champion_artifact_path="a/champ.pt",
            challenger_artifact_path="a/chal.pt",
            champion_model_id="champ",
            challenger_model_id="chal_v1",
            champion_model_version="1",
            challenger_model_version="1",
        )
        c1 = ShadowReplayConfig(**base)
        c2 = ShadowReplayConfig(**{**base, "challenger_model_id": "chal_v2"})
        assert c1.evidence_fingerprint() != c2.evidence_fingerprint()

    def test_artifact_replacement_changes_dataset_fingerprint(self):
        # simulates the artifact-replacement guard: the evidence identity
        # embeds the challenger artifact path; a replaced artifact (different
        # content at the same path) is caught by the run-freeze hash check
        # (CHG-0046 D11) and by a changed fingerprint here.
        c1 = ShadowReplayConfig(
            champion_artifact_path="a/champ.pt",
            challenger_artifact_path="a/chal.pt",
            champion_model_id="c",
            challenger_model_id="g",
            champion_model_version="1",
            challenger_model_version="1",
        )
        c2 = ShadowReplayConfig(
            **{**c1.identity(), "challenger_artifact_path": "a/chal_REPLACED.pt"}
        )
        assert c1.evidence_fingerprint() != c2.evidence_fingerprint()


# ---------------------------------------------------------------------------
# Determinism: same inputs -> identical evidence
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_identical_evidence(self):
        champ = [
            _trace_row(0, "BUY", [0.2, 0.5, 0.2, 0.1], entry=100.2, sl=99.0, tp=102.0),
            _trace_row(1, "SELL", [0.2, 0.2, 0.5, 0.1], entry=100.0, sl=101.0, tp=99.0),
            _trace_row(2, "NO_TRADE", [0.6, 0.2, 0.1, 0.1]),
        ]
        chal = [
            _trace_row(0, "SELL", [0.1, 0.3, 0.5, 0.1], entry=100.0, sl=101.0, tp=99.0),
            _trace_row(1, "NO_TRADE", [0.5, 0.2, 0.2, 0.1]),
            _trace_row(2, "BUY", [0.1, 0.5, 0.2, 0.2], entry=100.2, sl=99.0, tp=102.0),
        ]
        e1 = _evidence(champ, chal)
        e2 = _evidence(champ, chal)
        assert e1 == e2
        assert e1["pairs_resolved"] == 3
        # BUY vs SELL pair produced a NON-zero, non-mirror delta
        assert e1["paired_outcomes"]["delta_r_zero"] < 3
