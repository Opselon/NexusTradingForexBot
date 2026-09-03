"""TASK-03 final registry updates (additive rows only, CRLF-safe)."""
from pathlib import Path

ROOT = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")


def read_text(p: Path) -> str:
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(p: Path, s: str) -> None:
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


LINE = "\r\n"


def main() -> None:
    # ---------- 1. taskboard.md ----------
    tb = ROOT / "agents" / "taskboard.md"
    c = read_text(tb)
    old = "| FEATURE_SCHEMA_HASH v1 (new), INFERENCE_CONTRACT v1 (new) | none | IN_PROGRESS |"
    new = (
        "| FEATURE_SCHEMA_HASH v1 (new), INFERENCE_CONTRACT v1 (new), "
        "FEATURE_SCHEMA_70D v1 (scalp_v3 canonical) | none | VERIFIED |"
    )
    assert c.count(old) == 1, "taskboard row anchor"
    c = c.replace(old, new)
    note = (
        "- TASK-03-70D-PARITY (2026-08-19, Hermes-Parity): COMPLETE. scalp_v3 redefined "
        "350D->70D (Base 0..49 | News 10D 50..59 = news_context_v1 fields 0..8+news_state | "
        "Liquidity 10D 60..69). Delivered: schema_contract.py (canonical registry + SHA-256 "
        "hash + family validation), features70.py (immutable Feature70Snapshot), "
        "compute_70d_frame + verify_70d_artifact + manifest feature_schema_hash/"
        "training_dataset_id, replay_70d_vector (dataset==replay bit-exact, anti-leakage), "
        "inference_validator (10 rejection codes) + compatible_model_schema (60D/70D matrix), "
        "runtime70.py hook ([FEATURE_CONTRACT] trace, per-stage timing, model gate), golden "
        "corpus (11 scenarios), perf probes (70D assembly p50 4.1ms; no DB on feature path). "
        "70+ parity tests green; ruff/mypy clean; 6 commits pushed. TASK-4 benchmark UNBLOCKED." + LINE
    )
    ni = c.find("## Notes" + LINE)
    assert ni > 0
    c = c[:ni] + note + c[ni:]
    write_text(tb, c)
    print("taskboard done")

    # ---------- 2. contracts.md ----------
    cc = ROOT / "agents" / "contracts.md"
    c2 = read_text(cc)
    ai = c2.find("| FEATURE_SCHEMA_70D |")
    assert ai >= 0
    detail = (
        "## FEATURE_SCHEMA_70D v1 - canonical 70D contract (TASK-03-70D-PARITY)" + LINE +
        "- schema_id scalp_v3, dimension 70, candidate-only; ACTIVE live contract stays scalp_v1." + LINE +
        "- Layout: 0..49 Base (scalp_v1 protected) | 50..59 News 10D (news_context_v1 fields " + LINE +
        "0..8 + news_state, NOT a blind first-10 slice) | 60..69 Liquidity 10D (liquidity_engine " + LINE +
        "as_vector order)." + LINE +
        "- Single source of truth: features/schema_contract.py (canonical registry JSON, " + LINE +
        "feature_schema_hash SHA-256 prefix-16)." + LINE +
        "- Producers: ScalpFeatureEngine.compute_from_bars / news_bridge.news_context_at + " + LINE +
        "features70.news_10d_from_context / liquidity_engine.compute_liquidity_features." + LINE +
        "- Consumers: schema_v2.compute_70d_frame, replay_70d_vector, inference_validator, " + LINE +
        "runtime70, shadow70 (TASK-5)." + LINE +
        "- Compatibility: 60D scalp_v2 model + 70D runtime -> BLOCK; 70D model + 60D runtime -> " + LINE +
        "BLOCK. Scaler dimension must equal feature dimension (SCALER_MISMATCH stops)." + LINE +
        "- Missing semantics: FEATURE_DISABLED (explicit neutral) vs FEATURE_UNAVAILABLE " + LINE +
        "(blocks) - never fabricated values." + LINE + LINE
    )
    c2 = c2[:ai] + detail + c2[ai:]
    write_text(cc, c2)
    print("contracts done")

    # ---------- 3. runtime_invariants.md ----------
    ri = ROOT / "agents" / "runtime_invariants.md"
    c3 = read_text(ri)
    inv = (
        "## INV-020 - 70D feature contract is schema-controlled and canonical (TASK-03-70D-PARITY)" + LINE +
        "scalp_v3 = 70D = Base 0..49 (scalp_v1 protected) + News 10D 50..59 (news_context_v1 " + LINE +
        "fields 0..8 + news_state) + Liquidity 10D 60..69 (liquidity_engine order). Single source " + LINE +
        "of truth: features/schema_contract.py (canonical registry JSON + feature_schema_hash " + LINE +
        "SHA-256 prefix-16 over index+name+family). Dataset, replay, inference and live MUST " + LINE +
        "produce/consume the identical vector; any dimension/order/hash mismatch blocks inference " + LINE +
        "with an explicit rejection code (SCHEMA_MISMATCH/DIMENSION_MISMATCH/" + LINE +
        "FEATURE_ORDER_MISMATCH/SCHEMA_HASH_MISMATCH/SCALER_MISMATCH/NONFINITE_FEATURE/" + LINE +
        "OUT_OF_RANGE_FEATURE/NEWS_UNAVAILABLE/LIQUIDITY_UNAVAILABLE/STALE_FEATURES). Never " + LINE +
        "pad/truncate/substitute. Family missing -> explicit FEATURE_DISABLED (neutral block) or " + LINE +
        "FEATURE_UNAVAILABLE (block) - never fabricated. Schema metadata cached at construction; " + LINE +
        "no DB/file I/O on the per-tick path (INV-001). Legacy 60D (scalp_v2) models keep " + LINE +
        "receiving 60D vectors only." + LINE
    )
    c3 = c3.rstrip("\r\n") + LINE + LINE + inv
    write_text(ri, c3)
    print("invariants done")

    # ---------- 4. repository_state.md ----------
    rs = ROOT / "agents" / "repository_state.md"
    c4 = read_text(rs)
    snap = (
        "## Snapshot 2026-08-19 (TASK-03-70D-PARITY - 70D contract landed)" + LINE +
        "- scalp_v3 redefined 350D -> 70D canonical (Base|News|Liquidity). Registered: scalp_v1 " + LINE +
        "50D (ACTIVE), scalp_v2 60D (candidate), scalp_v3 70D (candidate, canonical), scalp_v4 70D " + LINE +
        "(TASK-2 integration candidate), scalp_liquidity_v1 60D (TASK-1 candidate)." + LINE +
        "- NEW: features/schema_contract.py, features/features70.py, features/inference_validator.py, " + LINE +
        "features/runtime70.py, model_generation/replay.py replay_70d_vector, schema_v2 " + LINE +
        "compute_70d_frame/build_70d_dataset/verify_70d_artifact, manifest feature_schema_hash + " + LINE +
        "training_dataset_id." + LINE +
        "- Tests: tests/unit/test_70d_{contract,dataset,replay,validator,runtime,perf}_task3.py " + LINE +
        "(70+ cases), tests/helpers/golden70d.py (11 scenarios)." + LINE +
        "- Docs: docs/70D_DATA_CONTRACT.md, docs/agent_handoffs/TASK-03-70D-PARITY.md." + LINE +
        "- Commits: 3cc53a3, 09dd0bc, 5401d7f, 14fff5a, b531243, abafa9c (all pushed)." + LINE +
        "- Next: TASK-4 benchmark UNBLOCKED; TASK-5 shadow70 can consume a validated candidate." + LINE
    )
    c4 = c4.rstrip("\r\n") + LINE + LINE + snap
    write_text(rs, c4)
    print("repo_state done")

    print("ALL REGISTRY UPDATES DONE")


if __name__ == "__main__":
    main()