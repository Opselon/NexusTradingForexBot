"""BUG-212 regression tests — PAPER/SHADOW hard simulation boundary.

Root cause (BUG-212): the primary launcher `NexusTradingForexBot.py` bound
`DirectMT5Adapter` on every win32 boot regardless of execution mode, so a
`--mode paper` boot stayed wired to the REAL MetaTrader 5 terminal (real
account, real positions managed by OrderManager) while the UI claimed
PAPER. The canonical CLI path (`src/nexus_scalp/cli/engine_boot.py`)
already implemented the BUG-148 guard; the primary launcher never got it.

Contract pinned here (2026-09-02, Hermes-EngineGuard):
  1. A PAPER boot must NEVER construct/bind DirectMT5Adapter — the
     simulation adapter (PaperMT5Adapter) is the required boundary.
  2. LiveEngine.align_adapter_to_boot_mode() re-asserts the boundary
     inside the engine (defense in depth) at boot AND from the launcher.
  3. SHADOW is observation-only: any non-NO_TRADE proposal is downgraded
     to a logged NO_TRADE (SHADOW_OBSERVATION_ONLY) BEFORE dispatch, and
     intelligent hedges are suppressed. Live prediction data flow is kept.

Fixture note (2026-09-07, integration-recovery): constructing a LiveEngine
runs the P1 artifact-trust gate (verify_artifact_integrity) on the configured
artifact path. The tests below that build a REAL engine therefore point the
config at a HERMETIC bundle (weights + manifest.json generated together with
the same production verification contract) instead of the machine-state
production champion — the integrity gate itself is exercised for real (a
tampered artifact is rejected in test_artifact_integrity_gate_rejects_tampered_weights).
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.domain.enums import ActionType, ExecutionMode


def _write_verified_bundle(directory, num_features: int = 50):
    """Create model.pt + manifest.json as a coherent trust-chain pair.

    The manifest digest is computed FROM the emitted weights — the exact
    production contract (load_integrity.verify_artifact_integrity): what is
    on disk must equal what the manifest declares. Returns (path, digest).
    """
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / "model.pt"
    # The P0 serving gate refuses fresh-init / behavioral-degenerate weights,
    # so boot fixtures must mint TRAINED artifacts (fix the fixture, not the
    # gate — precedent ab9db747 / AGENT-12 c004). Deterministic recipe — and
    # BUG-154: manual_seed MUST precede construction, because __init__ draws
    # the initial weights from the AMBIENT RNG; constructing first makes the
    # minted bundle's behavioral health machine-dependent (linux CI drew a
    # 0.003-sensitivity net -> LOAD_REJECTED while dev boxes stayed green).
    import torch as _torch

    _torch.manual_seed(999)
    model = ScalpNet(num_features=num_features)
    model.train()
    gen = _torch.Generator().manual_seed(1234)
    n_cls = int(model.classifier.weight.shape[0])
    X = _torch.randn(256, num_features, generator=gen)
    y = _torch.randint(0, n_cls, (256,), generator=gen)
    opt = _torch.optim.AdamW(model.parameters(), lr=1e-3)
    for _ in range(30):
        opt.zero_grad()
        loss = _torch.nn.functional.cross_entropy(model(X, return_logits=True), y)
        loss.backward()
        opt.step()
    model.eval()
    torch.save(model.state_dict(), model_path)

    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": "1.0.0",
        "bundle_id": f"bundle_{digest[:12]}",
        "model_sha256": digest,
        "feature_schema_id": "scalp_v1" if num_features == 50 else "scalp_v3",
        "input_dim": num_features,
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "lineage": "CLEAN_HISTORICAL",
        "production_eligible": True,
        "smoke": True,
        "dataset_id": "test",
        "dataset_sha256": "0" * 64,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return model_path, digest


class _FakeAdapter:
    """Minimal duck adapter so LiveEngine construction stays offline."""

    def __init__(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected


def _make_engine(mode: ExecutionMode, adapter: object, tmp_path=None) -> LiveEngine:
    from nexus_scalp.configuration.config import AppConfig

    cfg = AppConfig()
    cfg.execution.mode = mode
    if tmp_path is not None:
        # Hermetic artifact: a self-paired (weights, manifest) bundle under
        # tmp_path so the boot-time integrity gate passes deterministically.
        model_path, _digest = _write_verified_bundle(tmp_path / "bundle")
        cfg.model.model_artifact_path = str(model_path)
    return LiveEngine(config=cfg, adapter=adapter, audit_repo=MagicMock())


@pytest.fixture()
def _hermetic_artifact(tmp_path):
    """Default the engine fixture path to a verified tmp bundle.

    Test isolation: the artifact-trust gate must never depend on the state
    of the production champion on this machine (the weights can be swapped
    by any runtime process at any time; the manifest only matches right
    after a coherent publish).
    """
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Boot alignment: PAPER must replace a real adapter with the simulation
# ---------------------------------------------------------------------------


def test_boot_alignment_paper_replaces_real_adapter(tmp_path) -> None:
    """BUG-212 core regression: a PAPER boot must never keep DirectMT5Adapter."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    real = DirectMT5Adapter()
    engine = _make_engine(ExecutionMode.PAPER, real, tmp_path=tmp_path)
    aligned = engine.align_adapter_to_boot_mode(engine.adapter, ExecutionMode.PAPER)
    assert isinstance(aligned, PaperMT5Adapter), (
        f"PAPER boot must bind the simulation adapter, got {type(aligned).__name__}"
    )
    assert aligned is not real


def test_boot_alignment_paper_keeps_paper_adapter(tmp_path) -> None:
    """A PaperMT5Adapter on a PAPER boot is returned unchanged (no churn)."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    paper = PaperMT5Adapter(symbol="XAUUSD")
    engine = _make_engine(ExecutionMode.PAPER, paper, tmp_path=tmp_path)
    aligned = engine.align_adapter_to_boot_mode(paper, ExecutionMode.PAPER)
    assert aligned is paper


def test_boot_alignment_shadow_keeps_live_data_adapter(tmp_path) -> None:
    """SHADOW keeps its live-data adapter (observation contract, not identity).

    The shadow-observation contract needs the REAL feed/positions to record
    evidence; the no-mutation guarantee is enforced by the decision-path
    boundary (SHADOW_BOUNDARY), tested below.
    """
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    real = DirectMT5Adapter()
    engine = _make_engine(ExecutionMode.SHADOW, real, tmp_path=tmp_path)
    aligned = engine.align_adapter_to_boot_mode(real, ExecutionMode.SHADOW)
    assert aligned is real


def test_boot_alignment_live_keeps_real_adapter(tmp_path) -> None:
    """LIVE boots are untouched by the alignment (no LIVE behavior change)."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    real = DirectMT5Adapter()
    engine = _make_engine(ExecutionMode.LIVE, real, tmp_path=tmp_path)
    aligned = engine.align_adapter_to_boot_mode(real, ExecutionMode.LIVE)
    assert aligned is real


def test_engine_constructor_aligns_adapter_at_boot(tmp_path) -> None:
    """The __init__ defense-in-depth: constructing LiveEngine with a real
    adapter under PAPER must leave engine.adapter as the simulation adapter
    BEFORE any downstream wiring (OrderLifecycleManager sees the boundary)."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    real = DirectMT5Adapter()
    engine = _make_engine(ExecutionMode.PAPER, real, tmp_path=tmp_path)
    assert isinstance(engine.adapter, PaperMT5Adapter)
    assert engine.adapter is not real


# ---------------------------------------------------------------------------
# 2. Launcher source contract (the original BUG-212 defect site)
# ---------------------------------------------------------------------------


def test_launcher_source_binds_paper_adapter_for_paper_boot() -> None:
    """The canonical launcher must mirror the engine_boot.py BUG-148 guard:
    a PAPER boot binds PaperMT5Adapter BEFORE the real-adapter branch, and
    the real branch is only reachable for non-PAPER modes."""
    import ast
    from pathlib import Path

    src = Path("NexusTradingForexBot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")

    # Order check: the PaperMT5Adapter bind must appear before the
    # DirectMT5Adapter bind in main()'s line order.
    paper_bind_line = None
    direct_bind_line = None
    paper_guard_seen = False
    for node in ast.walk(main_fn):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            if "ExecutionMode.PAPER" in test_src and "args.gateway" in test_src:
                paper_guard_seen = True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {ast.unparse(t) for t in targets}
            if "adapter" in names and node.value is not None:
                call = node.value
                call_src = ast.unparse(call)
                if "PaperMT5Adapter(" in call_src and paper_bind_line is None:
                    paper_bind_line = node.lineno
                if "DirectMT5Adapter(" in call_src and direct_bind_line is None:
                    direct_bind_line = node.lineno
    assert paper_guard_seen, "launcher must guard the adapter bind on ExecutionMode.PAPER"
    assert paper_bind_line is not None, "launcher must bind PaperMT5Adapter for PAPER boots"
    assert direct_bind_line is not None, "launcher keeps the real adapter branch for LIVE"
    assert paper_bind_line < direct_bind_line, (
        "the PAPER simulation bind must be checked BEFORE the real-adapter branch"
    )


# ---------------------------------------------------------------------------
# 3. SHADOW observation-only decision boundary
# ---------------------------------------------------------------------------


def _shadow_engine(tmp_path) -> LiveEngine:
    from nexus_scalp.domain.models import (
        TradeProposal,
    )

    engine = _make_engine(ExecutionMode.SHADOW, _FakeAdapter(), tmp_path=tmp_path)
    return engine


def _make_proposal(action: ActionType, ticket: int = 0):
    from datetime import UTC, datetime

    from nexus_scalp.domain.models import TradeProposal

    return TradeProposal(
        request_id=f"shadow_test_{ticket}_{action.value}",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=action,
        confidence=0.9,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        ticket=ticket,
    )


def test_shadow_boundary_source_contains_no_mutation_downgrade() -> None:
    """The decision path must downgrade non-NO_TRADE proposals in SHADOW
    before dispatch (structural regression on the fix site)."""
    import inspect

    # L2 extraction: the decision-execution stage (boundary + downgrade +
    # dispatch) is owned by DecisionExecutor; LiveEngine must delegate and
    # the owner must carry the contract markers in the right order.
    from nexus_scalp.application.live.decision_executor import DecisionExecutor
    from nexus_scalp.application.live_engine import LiveEngine

    facade_src = inspect.getsource(LiveEngine._process_tick_pipeline)
    assert "_decision_executor.execute_decision_stage(" in facade_src, (
        "LiveEngine tick pipeline must delegate the decision stage to DecisionExecutor"
    )
    src = inspect.getsource(DecisionExecutor.execute_decision_stage)
    assert "SHADOW_OBSERVATION_ONLY" in src, "SHADOW downgrade marker missing"
    assert "SHADOW_BOUNDARY" in src, "SHADOW boundary logging missing"
    # the downgrade must run BEFORE the dispatch router call site
    boundary_pos = src.find("SHADOW EXECUTION BOUNDARY")
    dispatch_pos = src.find("dispatch_order(")
    assert boundary_pos != -1 and dispatch_pos != -1
    assert boundary_pos < dispatch_pos, "SHADOW boundary must precede dispatch"


def test_shadow_proposal_downgrade_model_copy_semantics() -> None:
    """The downgrade payload must produce a NO_TRADE observation proposal
    with the canonical rejection markers (mirrors the exact model_copy
    the engine applies)."""
    proposal = _make_proposal(ActionType.BUY, ticket=0)
    downgraded = proposal.model_copy(
        update={
            "action": ActionType.NO_TRADE,
            "reason_code": "SHADOW_OBSERVATION_ONLY",
            "rejection_reason": "SHADOW mode is observation-only: BUY suppressed",
            "final_action": "NO_TRADE",
            "is_ai_reversal": False,
            "reversal_action": None,
        }
    )
    assert downgraded.action == ActionType.NO_TRADE
    assert downgraded.reason_code == "SHADOW_OBSERVATION_ONLY"
    assert downgraded.final_action == "NO_TRADE"
    assert downgraded.is_ai_reversal is False
    assert downgraded.reversal_action is None
    # the original proposal is untouched (frozen model semantics)
    assert proposal.action == ActionType.BUY


def test_shadow_suppresses_intelligent_hedge_dispatch() -> None:
    """_evaluate_hedging_policy must NOT call order_manager.execute_order in
    SHADOW mode (the hedge is an order mutation)."""
    from pathlib import Path

    src = Path("src/nexus_scalp/application/live_engine.py").read_text(encoding="utf-8")
    hedge_pos = src.find("Dispatching intelligent hedging limit order")
    assert hedge_pos != -1
    guard = src.rfind("SHADOW observation-only", 0, hedge_pos)
    assert guard != -1, "hedge dispatch must be guarded by the SHADOW boundary"


# ---------------------------------------------------------------------------
# 4. Mode-switch contract preserved (no LIVE behavior change)
# ---------------------------------------------------------------------------


def test_hot_mode_switch_contract_unchanged(tmp_path) -> None:
    """set_execution_mode keeps its BUG-148 hot-swap behavior (PAPER+SHADOW
    swap to simulation on a mode CHANGE) — the boot alignment must not have
    altered the hot path."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    real = DirectMT5Adapter()
    engine = _make_engine(ExecutionMode.LIVE, real, tmp_path=tmp_path)
    result = engine.set_execution_mode(ExecutionMode.PAPER, source="TEST")
    assert result["success"] is True
    assert isinstance(engine.adapter, PaperMT5Adapter)
    assert engine.order_manager.adapter is engine.adapter


# ---------------------------------------------------------------------------
# 5. The artifact-integrity gate itself stays REAL (negative coverage)
# ---------------------------------------------------------------------------


def test_artifact_integrity_gate_rejects_tampered_weights(tmp_path) -> None:
    """The gate must fail closed when weights are mutated AFTER the manifest
    was written (the exact production trap this fixture guards against):
    constructing LiveEngine on a tampered bundle must raise, never silently
    serve unverified weights."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError

    bundle_dir = tmp_path / "tampered_bundle"
    model_path, _digest = _write_verified_bundle(bundle_dir)
    # Mutate the weights AFTER the manifest was created (in-place flip).
    import torch

    state = torch.load(model_path, map_location="cpu", weights_only=True)
    first_key = next(iter(state))
    state[first_key] = state[first_key] + 1.0
    torch.save(state, model_path)

    from nexus_scalp.configuration.config import AppConfig

    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.PAPER
    cfg.model.model_artifact_path = str(model_path)
    with pytest.raises(ArtifactIntegrityError) as excinfo:
        LiveEngine(config=cfg, adapter=PaperMT5Adapter(), audit_repo=MagicMock())
    assert excinfo.value.verdict.status.value == "HASH_MISMATCH"
