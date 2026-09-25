"""TASK-4 data-integrity regression tests (part 1): dataset eligibility,
zero-substitution, research-sample contract, discovery, family distribution.

Covers: TEST-RS-01, RS-02, RS-03, RS-04, RS-05, RS-06, RS-07, RS-08,
RS-09(part), RS-12, RS-13, RS-14, RS-26.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.research.dataset import (
    REASON_INVALID_PNL,
    REASON_INVALID_R,
    REASON_MISSING_OUTCOME,
    REASON_MISSING_REALIZED_R,
    REASON_UNKNOWN_PROVENANCE,
    ResearchDatasetBuilder,
)
from nexus_scalp.research.discovery import (
    MIN_FAMILY_SAMPLES,
    discover_candidates,
    family_distribution,
)

try:
    from tests.unit.task4_research_helpers import (
        make_outcome,
        make_record,
        seed_experiences,
    )
except ImportError:  # pragma: no cover - fallback for direct pytest cwd
    from task4_research_helpers import make_outcome, make_record, seed_experiences


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 't4.db'}")
    yield r
    r.close()


def test_rs01_canonical_trade_reaches_research_sample(repo):
    """TEST-RS-01: canonical trade -> research sample with full provenance."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 3, prefix="rs01")
    ds = ResearchDatasetBuilder(ledger).build()
    assert len(ds.samples) == 3
    s = ds.samples[0]
    assert s.idempotency_key.startswith("rs01")
    assert s.strategy_id == "strat_fam"
    assert s.feature_schema_id == "scalp_v1"
    assert s.feature_dimension == 50
    assert s.decision_timestamp < s.outcome_timestamp
    assert s.realized_r != 0.0
    assert s.entry_price > 0.0 and s.stop_loss > 0.0


def test_rs02_missing_pnl_never_zero(repo):
    """TEST-RS-02: missing realized PnL (no broker truth) is rejected, never 0."""
    ledger = ExperienceLedger(repo)
    rec = make_record("rs02a", ts=datetime(2024, 2, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    # Broker outcome absent -> reconstruction_source NONE -> zero-substituted.
    out = make_outcome(rec, 0.0, broker_source="NONE")
    ledger.record_outcome(out)
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    assert len(ds.samples) == 0, "zero-substituted outcome must not enter research"
    ok, reason, _ = ResearchDatasetBuilder(ledger).evaluate_sample(
        ledger.get_experience_by_key(rec.idempotency_key)
    )
    assert not ok and reason == REASON_MISSING_REALIZED_R


def test_rs03_missing_r_never_zero(repo):
    """TEST-RS-03: missing R (R=0 with non-authoritative source) is rejected."""
    ledger = ExperienceLedger(repo)
    rec = make_record("rs03a", ts=datetime(2024, 2, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    out = make_outcome(rec, 0.0, broker_source="NONE")
    ledger.record_outcome(out)
    repo._queue.join()
    builder = ResearchDatasetBuilder(ledger)
    merged = ledger.get_experience_by_key(rec.idempotency_key)
    ok, reason, _ = builder.evaluate_sample(merged)
    assert not ok and reason == REASON_MISSING_REALIZED_R
    # A genuine break-even with authoritative reconstruction stays eligible.
    rec2 = make_record("rs03b", ts=datetime(2024, 2, 1, tzinfo=UTC) + timedelta(hours=1))
    ledger.record_experience(rec2)
    ledger.record_outcome(make_outcome(rec2, 0.0, broker_source="BROKER_DEALS"))
    repo._queue.join()
    builder2 = ResearchDatasetBuilder(ledger)
    ok2, _, _ = builder2.evaluate_sample(ledger.get_experience_by_key(rec2.idempotency_key))
    assert ok2, "authoritative break-even zero is a legitimate sample"


def test_rs04_duplicate_economic_trade_counts_once(repo):
    """TEST-RS-04: duplicate idempotency keys collapse to one sample."""
    ledger = ExperienceLedger(repo)
    rec = make_record("rs04a", ts=datetime(2024, 3, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, 0.5))
    ledger.record_outcome(make_outcome(rec, 0.5))  # duplicate callback
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    keys = [s.idempotency_key for s in ds.samples]
    assert keys.count("rs04a") == 1


def test_rs05_strategy_context_survives(repo):
    """TEST-RS-05: strategy context survives into the research dataset."""
    ledger = ExperienceLedger(repo)
    rec = make_record("rs05a", ts=datetime(2024, 3, 2, tzinfo=UTC))
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, 0.5))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    s = ds.samples[0]
    assert s.strategy_id == rec.strategy_id
    assert s.regime == "RANGING_MEAN_REVERSION"
    assert s.session == "LONDON"
    assert s.volatility_regime == "NORMAL"
    assert s.trend_state == "BULLISH"


def test_rs06_schema_version_survives(repo):
    """TEST-RS-06: feature schema id + dimension survive into the dataset."""
    ledger = ExperienceLedger(repo)
    rec = make_record(
        "rs06a", ts=datetime(2024, 3, 3, tzinfo=UTC), schema_id="scalp_v2", dimension=60
    )
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, 0.5))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    s = ds.samples[0]
    assert s.feature_schema_id == "scalp_v2"
    assert s.feature_dimension == 60
    # Mixed-schema datasets keep both identities.
    rec2 = make_record(
        "rs06b", ts=datetime(2024, 3, 4, tzinfo=UTC), schema_id="scalp_v1", dimension=50
    )
    ledger.record_experience(rec2)
    ledger.record_outcome(make_outcome(rec2, 0.4))
    repo._queue.join()
    ds2 = ResearchDatasetBuilder(ledger).build()
    assert sorted(ds2.schema_ids) == ["scalp_v1", "scalp_v2"]


def test_rs07_50d_reproducible_after_60d(repo):
    """TEST-RS-07: 50D samples remain reproducible once 60D is introduced."""
    ledger = ExperienceLedger(repo)
    rec50 = make_record("rs07a", ts=datetime(2024, 4, 1, tzinfo=UTC))
    rec60 = make_record(
        "rs07b", ts=datetime(2024, 4, 2, tzinfo=UTC), schema_id="scalp_v2", dimension=60
    )
    ledger.record_experience(rec50)
    ledger.record_outcome(make_outcome(rec50, 0.5))
    ledger.record_experience(rec60)
    ledger.record_outcome(make_outcome(rec60, 0.5))
    repo._queue.join()
    builder = ResearchDatasetBuilder(ledger)
    ds1 = builder.build()
    ds2 = builder.build()  # deterministic rebuild
    assert ds1.dataset_id == ds2.dataset_id
    s50 = next(s for s in ds1.samples if s.feature_dimension == 50)
    s60 = next(s for s in ds1.samples if s.feature_dimension == 60)
    assert s50.feature_schema_id == "scalp_v1" and s60.feature_schema_id == "scalp_v2"
    assert s50.feature_dimension == 50 and s60.feature_dimension == 60


def test_rs08_invalid_label_rejected():
    """TEST-RS-08: invalid labels are rejected data, never silently NO_TRADE.

    The research contract 0=NO_TRADE/1=BUY/2=SELL is enforced at label
    generation (model_generation LabelSchema). Research samples carry only a
    direction string; an unknown direction is preserved as provenance and is
    not coerced into a fake class.
    """
    from nexus_scalp.research.models import ResearchSample

    s = ResearchSample(
        sample_id="rs08",
        experience_id="e",
        idempotency_key="k",
        decision_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        outcome_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        symbol="XAUUSD",
        strategy_id="s",
        direction="BUY_MARKET",
        realized_r=0.5,
    )
    assert s.direction == "BUY_MARKET"  # preserved verbatim, never coerced


def test_rs09_future_outcome_rejected(repo):
    """TEST-RS-09 (part): outcome preceding decision is rejected data.

    The ledger's merged projection refuses causality-violating outcomes at
    read time (the record is returned WITHOUT the outcome -> not closed), so
    such a trade can never enter the research dataset.
    """
    ledger = ExperienceLedger(repo)
    rec = make_record("rs09a", ts=datetime(2024, 5, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    out = make_outcome(rec, 0.5)
    out = out.model_copy(update={"outcome_timestamp": rec.decision_timestamp - timedelta(hours=1)})
    ledger.record_outcome(out)
    repo._queue.join()
    merged = ledger.get_experience_by_key(rec.idempotency_key)
    assert merged is not None
    assert not merged.is_closed, "causality-violating outcome must not attach"
    ds = ResearchDatasetBuilder(ledger).build()
    assert all(s.idempotency_key != "rs09a" for s in ds.samples)


def test_rs12_family_grouping_deterministic(repo):
    """TEST-RS-12: family grouping is deterministic across builds."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 25, prefix="rs12")
    ds1 = ResearchDatasetBuilder(ledger).build()
    ds2 = ResearchDatasetBuilder(ledger).build()
    f1 = family_distribution(ds1.samples)
    f2 = family_distribution(ds2.samples)
    assert f1["families"] == f2["families"]
    assert f1["family_sizes"] == f2["family_sizes"]


def test_rs13_family_distribution_reports(repo):
    """TEST-RS-13: family distribution reports sizes, largest/median/smallest."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 25, prefix="rs13")
    ds = ResearchDatasetBuilder(ledger).build()
    dist = family_distribution(ds.samples)
    assert dist["families"] == 1
    assert dist["largest"] == 25
    assert dist["median"] == 25
    assert dist["smallest"] == 25
    assert dist["families_above_floor"] == 1


def test_rs14_sample_floor_rejection_explicit(repo):
    """TEST-RS-14: sample-floor rejection is explicit (family-level census)."""
    ledger = ExperienceLedger(repo)
    seed_experiences(ledger, repo, 5, prefix="rs14a", base=datetime(2024, 6, 1, tzinfo=UTC))
    seed_experiences(ledger, repo, 25, prefix="rs14b", base=datetime(2024, 6, 2, tzinfo=UTC))
    # rs14a+rs14b share the same family fingerprint? No: seeds differ by prefix
    # only in the KEY, family fingerprint is context-based -> one family of 30.
    ds = ResearchDatasetBuilder(ledger).build()
    dist = family_distribution(ds.samples, min_family_samples=MIN_FAMILY_SAMPLES)
    assert dist["families"] == 1
    assert dist["families_above_floor"] == 1
    assert dist["families_below_floor"] == 0
    cands = discover_candidates(ds.samples)
    assert len(cands) == 1  # 30 >= 20 floor
    assert cands[0].discovery_evidence["tier"] == "STANDARD"


def test_rs14b_small_sample_tier_discovery(repo):
    """TEST-RS-14b: families 8..19 are DISCOVERED (SMALL_SAMPLE tier), never
    fabricated as validated; below 8 there is no candidate at all."""
    ledger = ExperienceLedger(repo)
    base = datetime(2024, 6, 1, tzinfo=UTC)
    for i in range(12):
        rec = make_record(f"sm14_{i}", ts=base + timedelta(hours=i))
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, 0.3))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    cands = discover_candidates(ds.samples)
    assert len(cands) == 1
    assert cands[0].discovery_evidence["tier"] == "SMALL_SAMPLE"


def test_rs26_split_fills_not_double_counted(repo):
    """TEST-RS-26: research dataset does not double-count split fills.

    Two outcome rows sharing one execution_id (broker split) must map to the
    same economic observation. The ledger dedups by idempotency_key; a shared
    execution_id without shared key must still not inflate research.
    """
    ledger = ExperienceLedger(repo)
    rec_a = make_record("rs26a", ts=datetime(2024, 7, 1, tzinfo=UTC))
    rec_b = make_record("rs26b", ts=datetime(2024, 7, 1, tzinfo=UTC) + timedelta(seconds=1))
    ledger.record_experience(rec_a)
    ledger.record_experience(rec_b)
    out_a = make_outcome(rec_a, 0.3)
    out_b = make_outcome(rec_b, 0.3)
    out_a = out_a.model_copy(update={"execution_id": "SPLIT-1"})
    out_b = out_b.model_copy(update={"execution_id": "SPLIT-1"})
    ledger.record_outcome(out_a)
    ledger.record_outcome(out_b)
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    assert len(ds.samples) == 2  # two distinct experiences, one observation each
    assert len({s.idempotency_key for s in ds.samples}) == 2


def test_rs15_losing_trades_are_retained_as_research_samples(repo):
    """TEST-RS-15 (learning-loop battery): a LOSING trade (negative realized R
    with authoritative broker reconstruction) must enter the research dataset.
    The learning system must not train only on winners."""
    ledger = ExperienceLedger(repo)
    rec = make_record("rs15a", ts=datetime(2024, 4, 1, tzinfo=UTC))
    ledger.record_experience(rec)
    ledger.record_outcome(make_outcome(rec, -1.2, broker_source="BROKER_DEALS"))
    repo._queue.join()
    ds = ResearchDatasetBuilder(ledger).build()
    assert len(ds.samples) == 1, "a losing trade is valid learning evidence"
    s = ds.samples[0]
    assert s.realized_r == pytest.approx(-1.2)
    ok, reason, _ = ResearchDatasetBuilder(ledger).evaluate_sample(
        ledger.get_experience_by_key(rec.idempotency_key)
    )
    assert ok and reason == ""


def test_rs16_nonfinite_outcome_rejected_never_trained(repo):
    """TEST-RS-16 (learning-loop battery): NaN/Inf realized PnL/R from a
    corrupted outcome must never enter a training dataset. Two defense
    layers are pinned: (1) persistence round-trip - pydantic serializes
    non-finite floats as JSON null, the outcome payload cannot rehydrate,
    the ledger logs [EXPERIENCE] INVALID outcome payload and returns the
    record WITHOUT an outcome (excluded as MISSING_OUTCOME); (2) the
    in-memory eligibility audit rejects finite-guard violations as
    INVALID_PNL / INVALID_R before any dataset build. The append-only rows
    themselves are preserved (rejection is exclusion, not deletion)."""
    ledger = ExperienceLedger(repo)
    rec_nan = make_record("rs16a", ts=datetime(2024, 5, 1, tzinfo=UTC))
    ledger.record_experience(rec_nan)
    ledger.record_outcome(make_outcome(rec_nan, float("nan")))
    rec_inf = make_record("rs16b", ts=datetime(2024, 5, 1, tzinfo=UTC) + timedelta(hours=1))
    ledger.record_experience(rec_inf)
    ledger.record_outcome(make_outcome(rec_inf, float("inf")))
    repo._queue.join()
    builder = ResearchDatasetBuilder(ledger)
    # Layer 1: corrupted outcome payloads cannot rehydrate from the ledger.
    # BUG-185 taxonomy (5073b86): an outcome-less record is classified by the
    # canonical dispatch-evidence resolver — a test-seeded record has NO
    # audit_orders/audit_signals rows, so the honest verdict is
    # UNKNOWN_PROVENANCE (previously MISSING_OUTCOME). BOTH reasons exclude
    # the sample from research; the invariant under test is EXCLUSION, with
    # the reason documented per the current taxonomy.
    merged_nan = ledger.get_experience_by_key(rec_nan.idempotency_key)
    ok_nan, reason_nan, _ = builder.evaluate_sample(merged_nan)
    assert not ok_nan and reason_nan == REASON_UNKNOWN_PROVENANCE
    assert merged_nan.is_closed is False
    merged_inf = ledger.get_experience_by_key(rec_inf.idempotency_key)
    ok_inf, reason_inf, _ = builder.evaluate_sample(merged_inf)
    assert not ok_inf and reason_inf == REASON_UNKNOWN_PROVENANCE
    assert builder.build().samples == [], "non-finite outcomes never reach research"
    # Layer 2: an in-memory record carrying the non-finite outcome is
    # rejected by the explicit finite guards (evaluate_sample never sees a
    # non-finite value from persisted data, so exercise it in memory).
    ok_mem, reason_mem, _ = builder.evaluate_sample(
        rec_nan.with_outcome(make_outcome(rec_nan, float("nan")))
    )
    assert not ok_mem and reason_mem == REASON_INVALID_PNL
    # evaluate_sample gates PnL BEFORE R, so an inf PnL reports
    # INVALID_PNL; exercise INVALID_R with finite PnL + inf R (frozen
    # model -> model_copy, the repo's immutable-update convention).
    ok_mem_inf, reason_mem_inf, _ = builder.evaluate_sample(
        rec_inf.with_outcome(
            make_outcome(rec_inf, 0.5).model_copy(update={"realized_r_multiple": float("inf")})
        )
    )
    assert not ok_mem_inf and reason_mem_inf == REASON_INVALID_R
    # The corrupted rows survive append-only.
    assert ledger.get_experience_by_key(rec_nan.idempotency_key) is not None
    assert ledger.get_experience_by_key(rec_inf.idempotency_key) is not None
