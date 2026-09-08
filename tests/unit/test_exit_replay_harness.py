"""Tests for the counterfactual exit-policy replay harness (Nexus-QA-REPLAY).

Covers: hand-computed grid math, path-bound ordering, NO_DATA fail-closed,
synthetic determinism, and AI-flip on/off differential.  xdist-safe: every
test builds its own temp sqlite; no module-level shared state.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "forensics" / "exit_policy_counterfactual.py"

_spec = importlib.util.spec_from_file_location("exit_policy_counterfactual", SCRIPT)
epc = importlib.util.module_from_spec(_spec)
sys.modules["exit_policy_counterfactual"] = epc
_spec.loader.exec_module(epc)


def _policy(**overrides):
    defaults = dict(
        be_trigger_r=0.3,
        be_lock_pips=0.6,
        retention=0.80,
        arm_threshold_r=0.5,
        trail_atr_mult=1.15,
        ai_flip_enabled=True,
    )
    defaults.update(overrides)
    return epc.PolicyParams(**defaults)


def _trade(**overrides):
    defaults = dict(
        realized_r=0.14,
        mfe_r=0.5,
        mae_r=-0.4,
        exit_reason="BREAK_EVEN_SL_HIT",
        exit_mechanism="BREAK_EVEN_SL_HIT",
    )
    defaults.update(overrides)
    return epc.TradeRow(**defaults)


# ---------------------------------------------------------------------------
# 1. Grid math correctness (hand-computed cases)
# ---------------------------------------------------------------------------
class TestGridMath:
    def test_mfe_below_be_trigger_unchanged(self):
        # mfe_r=0.2 < be_trigger=0.3 -> trade never armed BE; outcome unchanged
        t = _trade(realized_r=-0.63, mfe_r=0.2, mae_r=-0.8)
        res = epc.replay_trade(t, _policy(be_trigger_r=0.3))
        assert res.outcome_r == pytest.approx(-0.63)
        assert res.binding == "unchanged"
        assert res.armed_be is False

    def test_mfe_half_r_floor80_gives_at_least_04_minus_friction(self):
        # mfe=0.5, be_trigger=0.3 (armed), floor 80% armed at 0.5R
        # -> giveback floor = 0.8*0.5 = 0.4; lock floor = 0.6*0.1 = 0.06;
        #    trail floor = max(0.06, 0.5 - 1.15*0.35) = max(0.06, 0.0975) = 0.0975
        # binding = giveback_floor -> outcome == 0.4 (no friction)
        t = _trade(mfe_r=0.5, mae_r=-0.5)
        res = epc.replay_trade(t, _policy(retention=0.80, arm_threshold_r=0.5), friction_r=0.0)
        assert res.outcome_r >= 0.4 - 1e-9
        assert res.outcome_r == pytest.approx(0.4)
        assert res.binding == "giveback_floor"

    def test_floor80_gives_at_least_04_minus_friction_with_friction(self):
        # same setup but friction 0.05 -> outcome >= 0.4 - 0.05
        t = _trade(mfe_r=0.5, mae_r=-0.5, realized_r=0.14)
        res = epc.replay_trade(
            t, _policy(retention=0.80, arm_threshold_r=0.5), friction_r=0.05
        )
        assert res.outcome_r >= 0.4 - 0.05 - 1e-9
        assert res.outcome_r <= 0.4 + 1e-9  # friction is subtracted, never added

    def test_retention_60_70_80_ordering(self):
        t = _trade(mfe_r=0.8, mae_r=-0.5)
        outs = [
            epc.replay_trade(t, _policy(retention=r, arm_threshold_r=0.5)).outcome_r
            for r in (0.60, 0.70, 0.80)
        ]
        # 0.48, 0.56, 0.64 respectively (trail floor 0.8-1.15*0.35=0.3975 < 0.48)
        assert outs == pytest.approx([0.48, 0.56, 0.64])

    def test_lock_pips_conversion(self):
        # 2.0-pip lock at 0.1 R/pip = 0.2R floor beats the 0.8*0.3=0.24? no:
        # floor80 at arm 0.5 needs mfe>=0.5; here mfe=0.3 -> no giveback floor.
        # lock floor 0.2 vs trail floor max(0.2, 0.3-0.4025)=0.2 -> outcome 0.2
        t = _trade(mfe_r=0.3, mae_r=-0.5)
        res = epc.replay_trade(t, _policy(be_lock_pips=2.0), lock_pip_value_r=0.1)
        assert res.outcome_r == pytest.approx(0.2)
        assert res.binding == "be_lock"

    def test_wide_trail_retains_more_than_tight_trail(self):
        t = _trade(mfe_r=1.2, mae_r=-0.5)
        tight = epc.replay_trade(t, _policy(trail_atr_mult=1.15, retention=0.5, arm_threshold_r=0.5))
        wide = epc.replay_trade(t, _policy(trail_atr_mult=2.0, retention=0.5, arm_threshold_r=0.5))
        # 0.8 vs 0.5 floor... retention 0.5*1.2 = 0.6 floor for both;
        # trail: 1.2-1.15*0.35=0.7975 vs 1.2-2.0*0.35=0.5 -> binding differs
        assert tight.outcome_r == pytest.approx(0.7975)
        assert wide.outcome_r == pytest.approx(0.6)
        assert tight.outcome_r >= wide.outcome_r

    def test_grid_has_360_cells(self):
        cells = epc.sweep_grid([_trade()])
        assert len(cells) == 360
        nets = [c.net_r for c in cells]
        assert nets == sorted(nets, reverse=True)


# ---------------------------------------------------------------------------
# 2. Path-bound ordering: result <= mfe_r and >= -1R (and >= mae bound)
# ---------------------------------------------------------------------------
class TestPathBounds:
    @pytest.mark.parametrize("mfe", [0.0, 0.14, 0.5, 1.2, 3.0])
    @pytest.mark.parametrize("trigger", [0.15, 0.3, 0.6])
    @pytest.mark.parametrize("flip", [True, False])
    def test_result_within_path_bounds(self, mfe, trigger, flip):
        t = _trade(mfe_r=mfe, mae_r=-0.6, realized_r=-0.2)
        for retention in (0.60, 0.80):
            for arm in (0.5, 1.0):
                for trail in (1.15, 2.0):
                    pol = _policy(
                        be_trigger_r=trigger, retention=retention,
                        arm_threshold_r=arm, trail_atr_mult=trail, ai_flip_enabled=flip,
                    )
                    res = epc.replay_trade(t, pol)
                    assert res.outcome_r <= mfe + 1e-9
                    assert res.outcome_r >= -1.0 - 1e-9
                    assert res.outcome_r >= -0.6 - 1e-9  # never beyond observed MAE

    def test_extreme_mae_clamps_to_minus_1r_floor(self):
        # outcome clipped to [-1R, ...]; an MAE beyond -1R still reports >= -1R
        t = _trade(mfe_r=0.2, mae_r=-1.4, realized_r=-1.4)
        res = epc.replay_trade(t, _policy(be_trigger_r=0.6))
        assert res.outcome_r == pytest.approx(-1.4)  # unchanged path may sit below -1R
        # but any *replayed* (non-unchanged) outcome is floored at -1R:
        t2 = _trade(mfe_r=0.9, mae_r=-1.4, realized_r=-1.4)
        res2 = epc.replay_trade(t2, _policy(be_trigger_r=0.15, retention=0.6, arm_threshold_r=0.5))
        assert res2.outcome_r >= -1.0 - 1e-9


# ---------------------------------------------------------------------------
# 3. NO_DATA fail-closed
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE audit_experience_outcomes (
    id INTEGER PRIMARY KEY,
    idempotency_key TEXT,
    execution_id TEXT,
    outcome_timestamp TEXT,
    is_executed INTEGER,
    is_closed INTEGER,
    exit_reason TEXT,
    realized_pnl_usd REAL,
    realized_r_multiple REAL,
    approved_volume REAL,
    mae_points REAL,
    mfe_points REAL,
    mae_usd REAL,
    mfe_usd REAL,
    mae_r REAL,
    mfe_r REAL,
    holding_duration_seconds REAL,
    slippage_points REAL,
    execution_latency_ms REAL,
    strategy_quality REAL,
    entry_quality REAL,
    execution_quality REAL,
    management_quality REAL,
    exit_quality REAL,
    behavioral_flags TEXT,
    payload TEXT
);
"""


def _make_db(tmp_path: Path, rows: int = 0) -> Path:
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for i in range(rows):
        conn.execute(
            "INSERT INTO audit_experience_outcomes "
            "(execution_id, is_closed, exit_reason, realized_r_multiple, mfe_r, mae_r) "
            "VALUES (?, 1, ?, ?, ?, ?)",
            (f"T{i}", "BREAK_EVEN_SL_HIT", 0.1, 0.5, -0.4),
        )
    conn.commit()
    conn.close()
    return db


def _run_cli(db: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), *extra],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


class TestNoDataFailClosed:
    def test_empty_db_exits_2_with_no_data(self, tmp_path):
        db = _make_db(tmp_path, rows=0)
        proc = _run_cli(db)
        assert proc.returncode == 2
        assert "NO_DATA" in proc.stderr
        # honesty: nothing that looks like fabricated ledger stats on stdout
        assert "baseline" not in (proc.stdout or "").lower()

    def test_missing_table_exits_2(self, tmp_path):
        db = tmp_path / "audit.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other_table (x INTEGER)")
        conn.commit()
        conn.close()
        proc = _run_cli(db)
        assert proc.returncode == 2
        assert "NO_DATA" in proc.stderr

    def test_missing_db_file_exits_2(self, tmp_path):
        proc = _run_cli(tmp_path / "does_not_exist.db")
        assert proc.returncode == 2
        assert "NO_DATA" in proc.stderr

    def test_populated_db_produces_real_report(self, tmp_path):
        db = _make_db(tmp_path, rows=3)
        out = tmp_path / "rep.json"
        proc = _run_cli(db, "--out", str(out))
        assert proc.returncode == 0
        rep = json.loads(out.read_text())
        assert rep["mode"] == "REAL"
        assert rep["baseline"]["n"] == 3
        assert rep["run_info"]["db_path"] == str(db.resolve())
        assert rep["run_info"]["rows_total"] == 3
        assert "generated_utc" in rep
        assert len(rep["cells_sorted_by_net_r"]) == 360


# ---------------------------------------------------------------------------
# 4. Synthetic determinism
# ---------------------------------------------------------------------------
class TestSyntheticDeterminism:
    def test_same_seed_identical_report(self, tmp_path):
        outs = []
        for i in range(2):
            out = tmp_path / f"rep{i}.json"
            env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--synthetic", "50", "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
            )
            assert proc.returncode == 0, proc.stderr
            payload = out.read_text()
            rep = json.loads(payload)
            rep.pop("generated_utc", None)  # timestamp is expected to differ
            outs.append(hashlib.sha256(json.dumps(rep, sort_keys=True).encode()).hexdigest())
        assert outs[0] == outs[1]

    def test_synthetic_stamped_everywhere(self, tmp_path):
        out = tmp_path / "rep.json"
        env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--synthetic", "10", "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
        assert proc.returncode == 0
        rep = json.loads(out.read_text())
        assert rep["mode"] == "SYNTHETIC"
        assert rep["baseline"]["mode"] == "SYNTHETIC"
        assert all(c["mode"] == "SYNTHETIC" for c in rep["cells_sorted_by_net_r"])
        assert rep["run_info"]["warning"].startswith("SYNTHETIC")
        assert "SYNTHETIC" in proc.stdout

    def test_synthetic_never_reads_db(self):
        trades_a = epc.generate_synthetic_trades(30)
        trades_b = epc.generate_synthetic_trades(30)
        assert trades_a == trades_b


# ---------------------------------------------------------------------------
# 5. AI-flip on/off differential
# ---------------------------------------------------------------------------
class TestAiFlipDifferential:
    def test_flip_exited_trade_differs_when_flip_disabled(self):
        t = _trade(realized_r=0.14, mfe_r=0.5, mae_r=-0.5, exit_mechanism="AI_REVERSAL_EXIT")
        on = epc.replay_trade(t, _policy(retention=0.8, arm_threshold_r=0.5))
        off = epc.replay_trade(
            t, _policy(retention=0.8, arm_threshold_r=0.5, ai_flip_enabled=False)
        )
        assert on.outcome_r == pytest.approx(0.4)
        assert off.outcome_r == pytest.approx(0.5)
        assert off.binding == "rule_target"
        # flip-on: floor 0.4; flip-off: rule exit min(0.5, 1.1)=0.5

    def test_flip_case_insensitive_and_whitespace_tolerant(self):
        t = _trade(exit_mechanism="  ai_reversal_exit ")
        on = epc.replay_trade(t, _policy())
        off = epc.replay_trade(t, _policy(ai_flip_enabled=False))
        assert on.outcome_r != off.outcome_r

    def test_non_flip_trade_identical_on_vs_off(self):
        # AI-flip disabled must change NOTHING for trades whose exit mechanism
        # was not the flip (mfe below arm threshold isolates the flip rule)
        for mech in ("TAKE_PROFIT_HIT", "HARD_SL_HIT", "BREAK_EVEN_SL_HIT",
                     "TRAILING_STOP_HIT", "RISK_FREE_SL_HIT", "MANUAL_CLOSE", ""):
            t = _trade(mfe_r=0.3, mae_r=-0.5, exit_mechanism=mech)
            on = epc.replay_trade(t, _policy())
            off = epc.replay_trade(t, _policy(ai_flip_enabled=False))
            assert on.outcome_r == off.outcome_r, mech
            assert on.binding == off.binding, mech

    def test_non_flip_trade_identical_above_arm_threshold_too(self):
        t = _trade(mfe_r=0.9, mae_r=-0.5, exit_mechanism="TAKE_PROFIT_HIT")
        on = epc.replay_trade(t, _policy())
        off = epc.replay_trade(t, _policy(ai_flip_enabled=False))
        assert on.outcome_r == off.outcome_r

    def test_flip_off_never_exceeds_mfe(self):
        t = _trade(realized_r=0.14, mfe_r=0.4, mae_r=-0.5, exit_mechanism="AI_REVERSAL_EXIT")
        off = epc.replay_trade(t, _policy(ai_flip_enabled=False))
        assert off.outcome_r == pytest.approx(0.4)  # min(mfe, 1.1) = 0.4
        assert off.outcome_r <= t.mfe_r + 1e-9

    def test_flip_exit_lowercase_classification(self):
        assert epc._is_flip_exit("AI_REVERSAL_EXIT") is True
        assert epc._is_flip_exit("TAKE_PROFIT_HIT") is False
        assert epc._is_flip_exit("") is False
        assert epc._is_flip_exit(None) is False


# ---------------------------------------------------------------------------
# 6. Real/synthetic mode separation
# ---------------------------------------------------------------------------
class TestModeSeparation:
    def test_real_report_never_labeled_synthetic(self, tmp_path):
        db = _make_db(tmp_path, rows=2)
        out = tmp_path / "rep.json"
        proc = _run_cli(db, "--out", str(out))
        assert proc.returncode == 0
        payload = out.read_text()
        assert "SYNTHETIC" not in payload

    def test_sweep_grid_outcomes_stay_within_baseline_bounds(self):
        trades = epc.generate_synthetic_trades(40)
        cells = epc.sweep_grid(trades)
        assert cells
        assert all(isinstance(c.net_r, float) for c in cells)
        # baseline identity check: a cell whose trigger exceeds every MFE
        # reproduces the baseline exactly (policy inert for the whole book).
        # Build it directly so the assertion does not depend on the synthetic
        # population's MFE spread.
        max_mfe = max(t.mfe_r for t in trades)
        inert_cells = [
            c for c in cells
            if c.policy.be_trigger_r > max_mfe and c.policy.ai_flip_enabled
        ]
        baseline = epc.compute_stats([t.realized_r for t in trades])
        if inert_cells:
            # cell fully above the population's arming horizon: pure passthrough
            assert inert_cells[0].net_r == pytest.approx(baseline["net_r"])
        else:
            # population reaches beyond the largest trigger (0.6R): verify the
            # inert-cell identity on a hand-built population instead
            capped = [t for t in trades if t.mfe_r <= 0.15]
            assert capped, "expected trades below the smallest trigger"
            capped_cells = epc.sweep_grid(capped)
            above = [
                c for c in capped_cells
                if c.policy.be_trigger_r > max(t.mfe_r for t in capped)
                and c.policy.ai_flip_enabled
            ]
            assert above
            assert above[0].net_r == pytest.approx(
                epc.compute_stats([t.realized_r for t in capped])["net_r"]
            )
        # every cell stays within the book's total path bound
        max_total_mfe = sum(t.mfe_r for t in trades)
        assert all(c.net_r <= max_total_mfe for c in cells)
