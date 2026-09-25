"""CHG-0047 runner: two deterministic replays -> promotion evidence artifact.

Uses the REAL champion bundle (70d_liquidity) and a REAL registered CHALLENGER
bundle (70d_news — lifecycle CHALLENGER in experience_model_registry, scalp_v3/
70D, never promoted, Champion untouched) over the SAME deterministic bar window
from data/raw/XAUUSD_M1.parquet. Writes the evidence artifact + promotion-
readiness report under artifacts/forensics/.

Usage:
    .venv/Scripts/python.exe -m scripts.shadow_replay_evidence [--bars N]

CLI flags only choose the window SIZE; the window itself is derived
deterministically (last N clean-spread bars, oldest->newest) so the same
N always yields the same dataset fingerprint.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.observability.logging import get_logger  # noqa: E402
from nexus_scalp.research.event_source import BarEventSource  # noqa: E402
from nexus_scalp.research.mt5_tick_dataset import (  # noqa: E402
    dataset_fingerprint as research_dataset_fingerprint,
)
from nexus_scalp.research.streaming_replay import (  # noqa: E402
    ReplayRunResult,
    ReplaySessionConfig,
    StreamingReplayEngine,
    load_model_artifacts,
)
from nexus_scalp.shadow.replay import (  # noqa: E402
    ShadowReplayConfig,
    build_replay_evidence,
)

logger = get_logger("scripts.shadow_replay_evidence")

CHAMPION_ARTIFACT = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
CHALLENGER_ARTIFACT = "artifacts/models/scalp/XAUUSD/70d_news/model.pt"
M1_PARQUET = "data/raw/XAUUSD_M1.parquet"
OUT_DIR = REPO / "artifacts" / "forensics"


def load_bar_records(bars: int) -> tuple[list[dict[str, object]], str]:
    """Deterministically loads the replay window + its fingerprint."""
    import polars as pl

    df = pl.read_parquet(REPO / M1_PARQUET).filter((pl.col("spread") > 0) & (pl.col("spread") < 50))
    df = df.tail(bars).sort("time")
    records: list[dict[str, object]] = []
    for r in df.iter_rows(named=True):
        ts = datetime.fromtimestamp(r["time"], tz=UTC)
        records.append(
            {
                "timestamp": ts,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]),
                "spread": float(r["spread"]),
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    ds_id = f"XAUUSD_M1_shadow_evidence_{bars}"
    return records, research_dataset_fingerprint(
        [
            {
                "timestamp": str(r["timestamp"]),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
            }
            for r in records
        ],
        ds_id,
    )


def run_pipeline(bars: int) -> dict[str, Any]:
    records, research_fp = load_bar_records(bars)
    champ_art = load_model_artifacts(REPO / CHAMPION_ARTIFACT)
    chal_art = load_model_artifacts(REPO / CHALLENGER_ARTIFACT)

    from nexus_scalp.features.schema_contract import SCHEMA_ID, feature_schema_hash
    from nexus_scalp.shadow.replay import dataset_fingerprint as shadow_fp

    cfg = ShadowReplayConfig(
        champion_artifact_path=CHAMPION_ARTIFACT,
        challenger_artifact_path=CHALLENGER_ARTIFACT,
        champion_model_id="primary_scalp_scalp_v3_70d",
        challenger_model_id="scalp_70d_news_scalp_v3_70d",
        champion_model_version="v1.0",
        challenger_model_version="1.0.0",
        policy_params={"confidence_threshold": 0.20},
        git_revision=_git_revision(),
        configuration_version="SHADOW_EVIDENCE_V2",
        dataset_id=f"XAUUSD_M1_shadow_evidence_{bars}",
        horizon_minutes=120,
    )

    def _engine(path: str, run_id: str) -> ReplayRunResult:
        session = ReplaySessionConfig(
            model_artifact_path=path,
            policy_params=dict(cfg.policy_params),
            decide_on="bar_close",
            git_commit=cfg.git_revision,
        )
        return StreamingReplayEngine(session).run(BarEventSource(list(records)), run_id=run_id)

    logger.info("[SHADOW_EVIDENCE] event=REPLAY_START bars=%s", bars)
    run_champ = _engine(CHAMPION_ARTIFACT, "SHADOW-EVID-CHAMP")
    run_chal = _engine(CHALLENGER_ARTIFACT, "SHADOW-EVID-CHAL")

    identity = cfg.identity(
        {
            "schema_id": SCHEMA_ID,
            "schema_hash": feature_schema_hash(),
            "dataset_fingerprint_shadow": shadow_fp(records, cfg.dataset_id),
            "dataset_fingerprint_research": research_fp,
            "champion_model_fingerprint": champ_art.model_fingerprint,
            "champion_scaler_fingerprint": champ_art.scaler_fingerprint,
            "champion_input_width": champ_art.num_features,
            "challenger_model_fingerprint": chal_art.model_fingerprint,
            "challenger_scaler_fingerprint": chal_art.scaler_fingerprint,
            "challenger_input_width": chal_art.num_features,
            "replay_config_fingerprint_champion": run_champ.config_fingerprint,
            "replay_config_fingerprint_challenger": run_chal.config_fingerprint,
            "replay_event_hash_champion": run_champ.event_hash,
            "replay_event_hash_challenger": run_chal.event_hash,
            "replay_ledger_hash_champion": run_champ.ledger_hash,
            "replay_ledger_hash_challenger": run_chal.ledger_hash,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )
    evidence = build_replay_evidence(
        run_champion=run_champ,
        run_challenger=run_chal,
        bar_records=records,
        dataset_id=cfg.dataset_id,
        horizon_minutes=cfg.horizon_minutes,
        min_resolved_pairs=cfg.min_resolved_pairs,
        extra_identity=identity,
    )
    evidence["replay_run_summary"] = {
        "champion": {
            "decisions": run_champ.decisions,
            "orders": len(run_champ.orders),
            "trades": len(run_champ.trades),
            "total_pnl_usd": round(run_champ.total_pnl_usd, 2),
        },
        "challenger": {
            "decisions": run_chal.decisions,
            "orders": len(run_chal.orders),
            "trades": len(run_chal.trades),
            "total_pnl_usd": round(run_chal.total_pnl_usd, 2),
        },
    }
    return evidence


def _git_revision() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip()[:12] if out.returncode == 0 else ""
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=600)
    args = parser.parse_args()

    evidence = run_pipeline(args.bars)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"shadow_challenger_evidence_{stamp}.json"
    out_path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(f"evidence artifact: {out_path}")
    pr = evidence["promotion_readiness"]
    print(
        f"pairs={evidence['pairs_total']} resolved={evidence['pairs_resolved']} "
        f"unresolved={evidence['pairs_unresolved']} invalid={evidence['pairs_invalid']}"
    )
    print(
        f"model argmax disagreements={evidence['model_level']['argmax_disagreement']} "
        f"policy disagreements={evidence['policy_level']['action_disagreement'] + evidence['policy_level']['direction_disagreement']}"
    )
    print(
        f"mean champR={evidence['paired_outcomes']['mean_champion_r']} "
        f"mean chalR={evidence['paired_outcomes']['mean_challenger_r']} "
        f"mean dR={evidence['paired_outcomes']['mean_delta_r']}"
    )
    print(f"VERDICT: {pr['verdict']} ({'; '.join(pr['reasons'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
