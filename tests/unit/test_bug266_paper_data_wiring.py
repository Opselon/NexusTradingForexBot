"""BUG-266 — the shipped paper REPLAY mode had NO production wiring (audit K2).

Root cause (2026-09-13, NSE-Swarm role 1 Architect):
``PaperMT5Adapter`` has supported a real-historical ``REPLAY`` market-data mode
since a5f38467 (P0 phase 4) via ``replay_source=``, and ``ReplayTickSource``
loads real recorded ticks/bars. But grep proved ZERO production constructions
passed ``replay_source``:

    src/nexus_scalp/cli/engine_boot.py            PaperMT5Adapter(symbol=...)
    NexusTradingForexBot.py                       PaperMT5Adapter(symbol=...)
    src/.../application/live_engine.py            PaperMT5Adapter(symbol=..., initial_balance=...)
    src/.../application/live/runtime_mode.py      PaperMT5Adapter(initial_balance=..., symbol=...)

so every PAPER session — boot, CLI, double-click launcher, hot-swap and boot
re-alignment — was locked to the synthetic AR(1) world around the hardcoded
_SEED_BASELINES price. Training-grade paper (the whole point of K2) was
impossible regardless of configuration.

Fix contract pinned here:
1. ``AppConfig.paper_data`` (SYNTHETIC default / REPLAY) exists and round-trips
   through YAML + NSE_PAPER_DATA__MODE env.
2. ``build_paper_adapter`` is the single decision point:
   * default/None config -> SYNTHETIC (existing behavior byte-identical),
   * REPLAY + available source -> a genuine REPLAY adapter with provenance,
   * REPLAY + missing source -> fail-closed raise (interactive boot),
   * REPLAY + missing source + degrade policy -> SYNTHETIC stamped with an
     explicit ``degraded_reason`` (boundary re-alignment must never brick).
3. allow_replay=False (SHADOW keeps the real-feed contract) never attaches
   replay even when configured.
4. All four PAPER construction sites route through the factory (AST-level
   source contract, mirrors the established BUG-212 launcher test style).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.adapters.paper.paper_data import (
    ON_REPLAY_UNAVAILABLE_RAISE,
    ON_REPLAY_UNAVAILABLE_SYNTHETIC,
    build_paper_adapter,
)
from nexus_scalp.adapters.paper.replay_source import ReplayDataUnavailableError
from nexus_scalp.configuration.config import AppConfig, PaperDataConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_raw_bars(path: Path, n: int = 5) -> Path:
    """Minimal real-export-shaped M1 CSV (columns per ReplayTickSource)."""
    lines = ["time,open,high,low,close,tick_volume,spread,real_volume,time_utc"]
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    for i in range(n):
        price = 3200.0 + i * 0.5
        ts = t0.replace(minute=12 + i)
        lines.append(
            f"2026-05-01 12:{12 + i:02d}:00,{price},{price + 0.4},{price - 0.4},"
            f"{price + 0.1},100,20,100,{ts.isoformat()}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Config surface
# ---------------------------------------------------------------------------


def test_paper_data_defaults_to_synthetic() -> None:
    cfg = AppConfig()
    assert cfg.paper_data.mode == "SYNTHETIC"
    assert cfg.paper_data.dataset_id == ""
    assert cfg.paper_data.allow_raw_fallback is True


def test_paper_data_yaml_roundtrip(tmp_path: Path) -> None:
    yaml_path = tmp_path / "live.yaml"
    yaml_path.write_text(
        "execution:\n  symbol: XAUUSD\n  mode: PAPER\n"
        "paper_data:\n  mode: REPLAY\n  dataset_id: ds-abc\n  allow_raw_fallback: false\n",
        encoding="utf-8",
    )
    cfg = AppConfig.load_from_yaml(yaml_path)
    assert cfg.paper_data == PaperDataConfig(
        mode="REPLAY", dataset_id="ds-abc", allow_raw_fallback=False
    )


def test_paper_data_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSE_PAPER_DATA__MODE", "replay")
    cfg = AppConfig()
    assert cfg.paper_data.mode == "replay"  # value verbatim; factory normalizes


# ---------------------------------------------------------------------------
# 2. Factory decision matrix
# ---------------------------------------------------------------------------


def test_factory_default_is_synthetic_and_unchanged() -> None:
    adapter = build_paper_adapter(symbol="XAUUSD")
    assert isinstance(adapter, PaperMT5Adapter)
    assert adapter.market_data_mode == "SYNTHETIC"
    assert adapter._current_price == pytest.approx(PaperMT5Adapter._SEED_BASELINES["XAUUSD"])


def test_factory_synthetic_config_attaches_no_source() -> None:
    adapter = build_paper_adapter(symbol="XAUUSD", paper_data=PaperDataConfig())
    assert adapter.market_data_mode == "SYNTHETIC"
    assert adapter._replay_source is None


def test_factory_replay_with_raw_bars_attaches_real_source(tmp_path: Path) -> None:
    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    cfg = PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv))
    adapter = build_paper_adapter(symbol="XAUUSD", paper_data=cfg)
    assert adapter.market_data_mode == "REPLAY"
    prov = adapter.replay_provenance
    assert prov["source"] == "RAW_BARS_CSV"
    assert prov["record_count"] == 5
    # the served tick is HISTORICAL, not a synthetic walk around 4400
    tick = adapter.get_last_tick("XAUUSD")
    assert tick.timestamp.year == 2026 and tick.timestamp.month == 5
    assert tick.bid == pytest.approx(3200.1)
    assert "degraded_reason" not in prov


def test_factory_replay_missing_source_fails_closed(tmp_path: Path) -> None:
    cfg = PaperDataConfig(mode="REPLAY", raw_bars_path=str(tmp_path / "absent.csv"))
    with pytest.raises(ReplayDataUnavailableError):
        build_paper_adapter(
            symbol="XAUUSD", paper_data=cfg, on_replay_unavailable=ON_REPLAY_UNAVAILABLE_RAISE
        )
    # explicit default is the fail-closed policy
    with pytest.raises(ReplayDataUnavailableError):
        build_paper_adapter(symbol="XAUUSD", paper_data=cfg)


def test_factory_replay_missing_source_degrades_loudly_not_silently(tmp_path: Path) -> None:
    cfg = PaperDataConfig(mode="REPLAY", raw_bars_path=str(tmp_path / "absent.csv"))
    adapter = build_paper_adapter(
        symbol="XAUUSD",
        paper_data=cfg,
        on_replay_unavailable=ON_REPLAY_UNAVAILABLE_SYNTHETIC,
    )
    # execution boundary kept (still a simulation adapter)...
    assert isinstance(adapter, PaperMT5Adapter)
    assert adapter.market_data_mode == "SYNTHETIC"
    # ...but the data-truth degradation is stamped into provenance so the
    # session can NEVER be attributed to real-market replay evidence.
    prov = adapter.replay_provenance
    assert prov["requested_mode"] == "REPLAY"
    assert str(prov["degraded_reason"]).startswith("REPLAY_UNAVAILABLE")


def test_factory_replay_honours_allow_raw_fallback_false(tmp_path: Path) -> None:
    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    cfg = PaperDataConfig(
        mode="REPLAY", dataset_id="", allow_raw_fallback=False, raw_bars_path=str(csv)
    )
    with pytest.raises(ReplayDataUnavailableError):
        build_paper_adapter(symbol="XAUUSD", paper_data=cfg)


def test_factory_shadow_never_attaches_replay(tmp_path: Path) -> None:
    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    cfg = PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv))
    adapter = build_paper_adapter(symbol="XAUUSD", paper_data=cfg, allow_replay=False)
    assert adapter.market_data_mode == "SYNTHETIC"
    assert adapter._replay_source is None


def test_factory_preserves_initial_balance_and_symbol(tmp_path: Path) -> None:
    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    adapter = build_paper_adapter(
        symbol="XAUUSD",
        initial_balance=777.0,
        paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv)),
    )
    assert adapter.balance == 777.0
    assert adapter.symbol == "XAUUSD"
    assert adapter.current_account_source == "PAPER"


# ---------------------------------------------------------------------------
# 3. Engine surfaces: boot re-alignment + hot swap build through the factory
# ---------------------------------------------------------------------------


class _FakeSettingsDb:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, key: str):
        if key not in self._mapping:
            return None

        class _Row:
            def __init__(self, value: object) -> None:
                self.value = value

        return _Row(self._mapping[key])


def _align(real_adapter: object, cfg: AppConfig, mode: str):
    from unittest.mock import patch

    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.domain.enums import ExecutionMode

    with (
        patch("nexus_scalp.application.live_engine.load_settings_service") as lss,
        patch("nexus_scalp.adapters.mt5.mt5_adapter.DirectMT5Adapter") as direct,
    ):
        lss.return_value.db = _FakeSettingsDb({"execution.mode": mode})
        direct.return_value = object()
        engine = LiveEngine.__new__(LiveEngine)
        engine.config = cfg
        return engine.align_adapter_to_boot_mode(real_adapter, ExecutionMode(mode))  # type: ignore[arg-type]


def _cfg_with_replay(tmp_path: Path) -> AppConfig:
    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    return AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER"},
        model={"model_artifact_path": "artifacts/m.pt"},
        paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv)),
    )


def test_align_paper_boot_real_adapter_becomes_replay(tmp_path: Path) -> None:
    """PAPER boot over a real adapter must land on the CONFIGURED substrate."""
    from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

    class _Realish:
        def is_connected(self) -> bool:
            return True

        def connection_state(self) -> MT5ConnectionState:
            return MT5ConnectionState.CONNECTED  # type: ignore[no-any-return]

    result = _align(_Realish(), _cfg_with_replay(tmp_path), "PAPER")
    assert isinstance(result, PaperMT5Adapter)
    assert result.market_data_mode == "REPLAY"


def test_align_paper_boot_missing_data_still_simulates(tmp_path: Path) -> None:
    """A broken REPLAY source may never brick the PAPER execution boundary."""
    cfg = AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER"},
        model={"model_artifact_path": "artifacts/m.pt"},
        paper_data=PaperDataConfig(mode="REPLAY", raw_bars_path=str(tmp_path / "gone.csv")),
    )

    class _Realish:
        def is_connected(self) -> bool:
            return True

    result = _align(_Realish(), cfg, "PAPER")
    assert isinstance(result, PaperMT5Adapter)
    assert result.market_data_mode == "SYNTHETIC"
    assert str(result.replay_provenance.get("degraded_reason", "")).startswith("REPLAY_UNAVAILABLE")


def test_hot_swap_paper_replay_and_shadow_semantics(tmp_path: Path) -> None:
    """set_execution_mode: PAPER honors REPLAY, SHADOW never attaches it."""
    from types import SimpleNamespace

    from nexus_scalp.application.live.runtime_mode import RuntimeModeService
    from nexus_scalp.domain.enums import ExecutionMode

    class _Engine(SimpleNamespace):
        def _invalidate_cross_mode_state(self, *a: object, **k: object) -> None:
            return None

        def _update_runtime_mode(self) -> None:
            self._runtime_mode = self.config.execution.mode

    class _Realish:
        current_account_source = "LIVE"

        def disconnect(self) -> None:
            return None

        def is_connected(self) -> bool:
            return True

    csv = _write_raw_bars(tmp_path / "XAUUSD_M1.csv")
    replay_cfg = PaperDataConfig(mode="REPLAY", raw_bars_path=str(csv))

    engine = _Engine(
        config=AppConfig(
            execution={"symbol": "XAUUSD", "mode": "LIVE"},
            model={"model_artifact_path": "artifacts/m.pt"},
            paper_data=replay_cfg,
        ),
        adapter=_Realish(),
        order_manager=SimpleNamespace(),
        _mode_session_generation=0,
        _runtime_mode=ExecutionMode.LIVE,
        _last_balance=1234.0,
    )
    res = RuntimeModeService.set_execution_mode(engine, ExecutionMode.PAPER, source="TEST")
    assert res["success"] is True and res["adapter_swapped"] is True
    assert isinstance(engine.adapter, PaperMT5Adapter)
    assert engine.adapter.market_data_mode == "REPLAY"
    assert engine.adapter.balance == 1234.0
    assert engine.adapter.current_account_source == "PAPER"

    # SHADOW -> keep the real-feed contract: no replay substrate attached
    engine2 = _Engine(
        config=AppConfig(
            execution={"symbol": "XAUUSD", "mode": "LIVE"},
            model={"model_artifact_path": "artifacts/m.pt"},
            paper_data=replay_cfg,
        ),
        adapter=_Realish(),
        order_manager=SimpleNamespace(),
        _mode_session_generation=0,
        _runtime_mode=ExecutionMode.LIVE,
        _last_balance=0.0,
    )
    res2 = RuntimeModeService.set_execution_mode(engine2, ExecutionMode.SHADOW, source="TEST")
    assert res2["success"] is True
    assert isinstance(engine2.adapter, PaperMT5Adapter)
    assert engine2.adapter.market_data_mode == "SYNTHETIC"


# ---------------------------------------------------------------------------
# 4. Source contract: every PAPER construction goes through the factory
# ---------------------------------------------------------------------------


def _paper_binds(path: Path, func: str | None = None) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if func is not None:
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    else:
        node = tree
    calls: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            names = {ast.unparse(t) for t in targets}
            if "adapter" in names or "new_adapter" in names or "replacement" in names:
                if isinstance(n.value, ast.Call):
                    calls.append(ast.unparse(n.value.func))
    return calls


def test_engine_boot_paper_bind_uses_factory() -> None:
    binds = _paper_binds(
        _REPO_ROOT / "src" / "nexus_scalp" / "cli" / "engine_boot.py", "_run_engine"
    )
    assert any("build_paper_adapter" in c for c in binds), binds
    assert not any(c.endswith("PaperMT5Adapter") for c in binds), (
        "engine_boot still constructs PaperMT5Adapter directly — REPLAY config would be ignored"
    )


def test_live_engine_and_hot_swap_paper_binds_use_factory() -> None:
    le = _paper_binds(
        _REPO_ROOT / "src" / "nexus_scalp" / "application" / "live_engine.py",
        "align_adapter_to_boot_mode",
    )
    assert any("build_paper_adapter" in c for c in le), le
    rm = _paper_binds(
        _REPO_ROOT / "src" / "nexus_scalp" / "application" / "live" / "runtime_mode.py",
        "set_execution_mode",
    )
    assert any("build_paper_adapter" in c for c in rm), rm


def test_no_production_paper_construction_bypasses_the_factory() -> None:
    """Every src-side direct PaperMT5Adapter(...) construction is accounted for.

    The paper adapter's own module and the factory are allowed; anything else
    on a production path would re-introduce BUG-266 (config silently ignored).
    scripts/ci/runtime_gate.py is deliberately exempt: the certification gate
    owns its own tripwire wrapper and must never be replayed.
    """
    allowed = {
        "src/nexus_scalp/adapters/paper/paper_adapter.py",
        "src/nexus_scalp/adapters/paper/paper_data.py",
    }
    offenders: list[str] = []
    for p in sorted((_REPO_ROOT / "src").rglob("*.py")):
        rel = p.relative_to(_REPO_ROOT).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("PaperMT5Adapter"):
                offenders.append(f"{rel}:{n.lineno}")
    assert offenders == [], f"direct PAPER constructions bypass the data factory: {offenders}"
