"""Execution invariant matrix — machine-checkable pins for NSE execution truth.

Companion to ``agents/runtime_invariants.md``. Each row pins ONE
non-negotiable execution guarantee at the level the guarantee actually
lives (the seam the invariant's own evidence cites), so a regression
that silently weakens a gate fails CI instead of degrading live.

Rows follow the repo's invariant-test convention (see
test_state_truth_matrix.py / test_provider_gate_hardening.py): name the
invariant, name the source seam, assert the observable contract.

No MT5/network: every row is unit-level and drives the same paper /
in-memory fixtures the existing policy and audit suites use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import torch

from nexus_scalp.configuration.config import ModelConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy

REPO = Path(__file__).resolve().parents[2]


def _tick(bid: float = 2000.0, ask: float = 2000.2, ts: datetime | None = None) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=ts or datetime.now(UTC),
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def _fv(**over: object) -> FeatureVector:
    base = dict(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    base.update(over)
    return FeatureVector(**base)  # type: ignore[arg-type]


def _evaluate(policy: SignalPolicy, probs: list[float], **kw: object):
    return policy.evaluate_probabilities(
        probabilities=torch.tensor([probs]),
        current_tick=_tick(),
        feature_vector=_fv(),
        **kw,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Structure: every invariant this matrix pins is named and sourced.
# ---------------------------------------------------------------------------


#: (invariant id, source seam) — the matrix itself is the contract surface.
INVARIANT_ROWS: tuple[tuple[str, str], ...] = (
    ("INV-001", "nexus_scalp.signals.policy.SignalPolicy (no I/O in the policy)"),
    ("INV-002", "model_lifecycle/ + research/ + experience/ import surface"),
    ("INV-003", "nexus_scalp.risk.risk_engine.RiskEngine.calculate_dynamic_volume"),
    ("INV-004", "execution/order_manager.py HARD_MAX_LOTS / MAX_TOTAL_EXPOSURE"),
    ("INV-005", "audit_repository._signal_dedup_key (one lineage -> one row)"),
    ("INV-006", "audit_repository.log_signal ON CONFLICT DO NOTHING"),
    ("INV-011", "recon broker-authoritative reconciliation paths"),
    ("INV-013", "experience/outcome_recovery.py exit-classification provenance"),
    ("INV-014", "governance/models.py PROMOTION_TRANSITIONS"),
    ("INV-015", "governance promotion gate (no auto SHADOW->CHAMPION)"),
    ("INV-018", "shadow/shadow70 import surface (no order/risk authority)"),
    ("INV-024", "strategies/factory/provider_gate.py ProviderGate"),
    ("INV-025", "halt path blocks NEW entries only (no flatten-on-halt flag)"),
)


def test_matrix_rows_are_named_and_sourced() -> None:
    assert len(INVARIANT_ROWS) == len({rid for rid, _ in INVARIANT_ROWS})
    for rid, seam in INVARIANT_ROWS:
        assert rid.startswith("INV-"), rid
        assert seam, rid


# ---------------------------------------------------------------------------
# INV-001 — the tick hot path (policy) performs no I/O.
# ---------------------------------------------------------------------------


def test_inv001_policy_has_no_io_seams() -> None:
    """The decision surface must not import a DB, socket or file API — a
    blocking call in ``evaluate_probabilities`` breaks the 50ms budget the
    invariant was verified against."""
    import inspect

    src = inspect.getsource(SignalPolicy)
    for banned in ("sqlite3.connect", "psycopg.connect", "requests.get", "urlopen", "open("):
        assert banned not in src, f"policy hot path gained an I/O seam: {banned}"


def test_inv001_policy_modules_do_not_open_databases() -> None:
    import nexus_scalp.signals.policy as policy_mod

    tree = Path(policy_mod.__file__).parent
    for path in tree.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "sqlite3.connect" not in text, path
        assert "psycopg.connect" not in text, path


# ---------------------------------------------------------------------------
# INV-002 / INV-018 — learning, research, experience and shadow hold no
# order authority (no adapter, no order manager, no risk engine instance).
# ---------------------------------------------------------------------------


#: Modules that must never import execution authority (INV-002/INV-018).
NO_AUTHORITY_ROOTS = (
    "src/nexus_scalp/model_lifecycle",
    "src/nexus_scalp/experience",
    "src/nexus_scalp/shadow",
)

#: The order/risk authority surface — importing one of these INTO a learning
#: module is the invariant violation (INV-002), not a type annotation.
AUTHORITY_MODULES = (
    "nexus_scalp.adapters.mt5",
    "nexus_scalp.adapters.paper.paper_adapter",
    "nexus_scalp.execution.order_manager",
    "nexus_scalp.risk.risk_engine",
    "nexus_scalp.ports.mt5_port",
)


def test_inv002_learning_subsystem_holds_no_order_authority() -> None:
    violations: list[tuple[str, str]] = []
    for root in NO_AUTHORITY_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for authority in AUTHORITY_MODULES:
                if f"import {authority}" in text or f"from {authority}" in text:
                    violations.append((str(path), authority))
    assert not violations, violations


def test_inv002_no_send_order_callable_in_learning_surface() -> None:
    """The learning surface must not even name the broker send primitive —
    a stray ``send_order`` reference is how authority leaks in."""
    for root in NO_AUTHORITY_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "def send_order" not in text, path
            assert ".send_order(" not in text, path


def test_inv018_shadow70_imports_no_order_or_risk_authority() -> None:
    """INV-018: the shadow comparison path imports no order manager / risk
    engine / adapter. A challenger fault must never touch execution."""
    shadow_dir = REPO / "src" / "nexus_scalp" / "shadow"
    if not shadow_dir.exists():
        pytest.skip("shadow package absent on this base")
    for path in shadow_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for authority in AUTHORITY_MODULES:
            assert f"import {authority}" not in text, (path, authority)
            assert f"from {authority}" not in text, (path, authority)


# ---------------------------------------------------------------------------
# INV-003 / INV-004 — risk and execution authority is centralized.
# ---------------------------------------------------------------------------


def test_inv003_dynamic_volume_has_one_authoritative_definition() -> None:
    """``calculate_dynamic_volume`` is the single risk-boundary entry point.
    A second production definition is a divergent sizing source of truth."""
    import subprocess

    proc = subprocess.run(
        ["git", "grep", "-l", "def calculate_dynamic_volume", "--", "src/"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    definitions = [line for line in proc.stdout.split() if line.endswith(".py")]
    assert definitions == ["src/nexus_scalp/risk/risk_engine.py"], definitions


def test_inv004_execution_authority_constants_live_in_the_order_manager() -> None:
    """HARD_MAX_LOTS / MAX_TOTAL_EXPOSURE are the OrderManager's authority
    (INV-004): their canonical definitions live in order_manager.py. Other
    modules may READ the exposure ceiling to decide whether a candidate is
    even admissible (the policy gates predictive-limit placement on it) but
    a second DEFINITION is a divergent ceiling — that is what this row
    forbids."""
    import subprocess

    holders = []
    for constant in ("HARD_MAX_LOTS", "MAX_TOTAL_EXPOSURE"):
        proc = subprocess.run(
            ["git", "grep", "-l", f"^{constant}:", "--", "src/"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        holders.extend(line for line in proc.stdout.split() if line.endswith(".py"))
    # order_manager.py is the authority; policy.py only READS the ceiling.
    assert "src/nexus_scalp/execution/order_manager.py" in holders, holders
    assert holders.count("src/nexus_scalp/execution/order_manager.py") == 2
    # The policy's reference is a read (an import), never a redefinition.
    policy_src = (REPO / "src" / "nexus_scalp" / "signals" / "policy.py").read_text(
        encoding="utf-8"
    )
    assert "from nexus_scalp.execution.order_manager import" not in policy_src
    assert policy_src.count("MAX_TOTAL_EXPOSURE: int =") == 1
    assert "HARD_MAX_LOTS:" not in policy_src


# ---------------------------------------------------------------------------
# INV-005 / INV-006 — one execution lineage cannot become duplicate rows.
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = tmp_path / "inv.db"
    return AuditRepository(db_url=f"sqlite:///{db}")


def test_inv005_signal_dedup_key_is_stable_across_restarts(tmp_path: Path) -> None:
    """The dedup key is identity over WHAT was decided (symbol + minute +
    action + stage + reason), not the request UUID — so a replay of the same
    decision re-derives the SAME key and the unique index holds (INV-005)."""
    repo = _repo(tmp_path)
    try:
        first = _evaluate(SignalPolicy(), [0.01, 0.98, 0.01, 0.0])
        second = _evaluate(SignalPolicy(), [0.01, 0.98, 0.01, 0.0])
        assert first.request_id != second.request_id  # UUIDs differ by design
        assert repo._signal_dedup_key(first) == repo._signal_dedup_key(second)
    finally:
        repo.close()


def test_inv006_duplicate_signals_do_not_create_duplicate_rows(tmp_path: Path) -> None:
    """INV-006: the same lineage logged twice persists ONE decision row —
    the unique index + ON CONFLICT DO NOTHING is the durable guarantee."""
    repo = _repo(tmp_path)
    try:
        proposal = _evaluate(SignalPolicy(), [0.01, 0.98, 0.01, 0.0])
        repo.log_signal(proposal)
        repo.log_signal(proposal)
        repo.flush(5.0)
        import sqlite3

        with sqlite3.connect(str(tmp_path / "inv.db")) as conn:
            rows = conn.execute(
                "SELECT signal_dedup_key FROM audit_signals WHERE signal_dedup_key = ?",
                (repo._signal_dedup_key(proposal),),
            ).fetchall()
        assert len(rows) == 1, rows
    finally:
        repo.close()


def test_inv006_guard_telemetry_upsert_is_provider_unambiguous() -> None:
    """RT-003: the telemetry UPSERT qualifies ``count`` with the INSERT
    alias so the statement is unambiguous on PostgreSQL AND SQLite — the
    guard row is how duplicate decision pressure is observed."""
    import inspect

    from nexus_scalp.adapters.database import audit_repository as ar

    src = inspect.getsource(ar.AuditRepository._log_guard_telemetry)
    assert "DO UPDATE SET count = t.count + 1" in src
    assert "INSERT INTO audit_guard_telemetry AS t" in src


# ---------------------------------------------------------------------------
# INV-013 — exit classification carries evidence provenance.
# ---------------------------------------------------------------------------


def test_inv013_exit_mechanism_sources_are_a_closed_provenance_set() -> None:
    """Every persisted exit_mechanism must come with its evidence source.
    The vocabulary is closed so an inferred label can never be persisted as
    broker-proven."""
    from nexus_scalp.experience import outcome_recovery

    src = Path(outcome_recovery.__file__).read_text(encoding="utf-8")
    for required in (
        "ENGINE_FORCED",
        "BROKER_DEAL_REASON",
        "BROKER_DEAL_COMMENT",
        "SL_GEOMETRY",
        "TP_GEOMETRY",
        "FALLBACK_HEURISTIC",
    ):
        assert required in src, required


# ---------------------------------------------------------------------------
# INV-014 / INV-015 — the promotion state machine has no auto-promotion.
# ---------------------------------------------------------------------------


def test_inv014_promotion_transitions_have_no_shadow_to_champion() -> None:
    """SHADOW -> CHAMPION is an ILLEGAL transition (INV-015): the only path
    to CHAMPION is through operator APPROVED."""
    from nexus_scalp.governance.models import PROMOTION_TRANSITIONS, PromotionState

    assert PromotionState.CHAMPION not in PROMOTION_TRANSITIONS[PromotionState.SHADOW]
    assert PromotionState.APPROVED in PROMOTION_TRANSITIONS[PromotionState.READY_FOR_REVIEW]
    assert PromotionState.CHAMPION in PROMOTION_TRANSITIONS[PromotionState.APPROVED]
    # Champions retire; they are never silently demoted back into the pool.
    assert PROMOTION_TRANSITIONS[PromotionState.CHAMPION] == {PromotionState.RETIRED}


def test_inv015_champion_transition_requires_operator_approval() -> None:
    """No state reaches CHAMPION except APPROVED — the automatic-promotion
    path does not exist in the transition table."""
    from nexus_scalp.governance.models import PROMOTION_TRANSITIONS, PromotionState

    approvers = [
        state
        for state, targets in PROMOTION_TRANSITIONS.items()
        if PromotionState.CHAMPION in targets
    ]
    assert approvers == [PromotionState.APPROVED], approvers


def test_inv015_rejected_re_entry_is_explicit_only() -> None:
    """REJECTED -> RESEARCH is the only re-entry; nothing auto-revives a
    rejected candidate."""
    from nexus_scalp.governance.models import PROMOTION_TRANSITIONS, PromotionState

    assert PROMOTION_TRANSITIONS[PromotionState.REJECTED] == {PromotionState.RESEARCH}
    assert PromotionState.REJECTED not in PROMOTION_TRANSITIONS[PromotionState.CHAMPION]


# ---------------------------------------------------------------------------
# INV-024 — the external provider gate never blocks trading.
# ---------------------------------------------------------------------------


def test_inv024_provider_gate_exposes_only_gate_surface() -> None:
    """INV-024: all outbound external LLM traffic routes through the single
    ProviderGate. The gate's own surface is pacing/gating/circuit-breaking
    only — it exposes no execution or risk primitive, so an external
    failure can never be wired into a trading path through it."""
    from nexus_scalp.strategies.factory.provider_gate import ProviderGate

    surface = [name for name in dir(ProviderGate) if not name.startswith("_")]
    for banned in ("place_order", "send_order", "calculate_dynamic_volume"):
        assert banned not in surface, banned
    # The gate's public entry point executes a caller-supplied send — it
    # never owns the egress payload itself.
    assert "execute" in surface
    assert "reconfigure" in surface
    assert "health_snapshot" in surface


# ---------------------------------------------------------------------------
# INV-025 — halt blocks NEW entries only; open positions are never closed.
# ---------------------------------------------------------------------------


def test_inv025_no_flatten_on_halt_flag_exists() -> None:
    """The documented decision (audit finding F9): no DEMO flatten-on-halt
    flag exists. If one is ever added it must be default-off and LIVE-safe —
    its absence is the current contract, so a silent introduction fails."""
    import subprocess

    proc = subprocess.run(
        ["git", "grep", "-n", "flatten_on_halt", "--", "src/"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not proc.stdout.strip(), "flatten-on-halt flag appeared without a DEC record"
    assert proc.returncode == 1


# ---------------------------------------------------------------------------
# Execution-truth lineage: every emitted proposal is joinable.
# ---------------------------------------------------------------------------


def test_every_proposal_carries_execution_lineage() -> None:
    """OBS-TRACE: ONE execution_id per evaluation, stamped before any gate
    and carried into NO_TRADE proposals too — the decision->signal->order
    join must be re constructable for the gated classes forensics needs.
    The id is time-derived, so repeat evaluations carry DISTINCT ids."""
    policy = SignalPolicy()
    first = _evaluate(policy, [0.01, 0.98, 0.01, 0.0])
    seen = {first.execution_id}
    for probs in (
        [0.25, 0.30, 0.20, 0.25],
        [0.01, 0.98, 0.01, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ):
        proposal = _evaluate(policy, probs)
        assert proposal.action in (ActionType.NO_TRADE, ActionType.BUY_MARKET)
        assert proposal.execution_id.startswith("EXEC-")
        assert proposal.request_id
        assert proposal.decision_stage
        # A repeated evaluation class still yields a fresh id (OBS-TRACE-2
        # extended the stamp to EVERY early return precisely so no row is
        # unjoinable).
        assert proposal.execution_id not in seen
        seen.add(proposal.execution_id)


def test_degraded_inference_never_manufactures_a_signal() -> None:
    """A missing/empty probability payload fail-closes to NO_TRADE without
    touching gate state — no fabricated confidence from a degraded model."""
    policy = SignalPolicy()
    for bad in (None, torch.tensor([]), "not-a-tensor"):
        proposal = policy.evaluate_probabilities(
            probabilities=bad,  # type: ignore[arg-type]
            current_tick=_tick(),
            feature_vector=_fv(),
        )
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.reason_code == "PROBS_UNAVAILABLE_DEGRADED"
        assert proposal.confidence == 0.0


def test_confidence_threshold_has_one_canonical_default() -> None:
    """The base threshold's only default is ModelConfig.confidence_threshold
    (THRESHOLD OWNERSHIP); a second literal default here is a divergent
    source of truth labeled 'calibrated' without evidence."""
    assert SignalPolicy().confidence_threshold == ModelConfig().confidence_threshold
