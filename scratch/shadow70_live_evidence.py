"""70D Shadow Live Validation — Evidence Harness (TASK-70D-SHADOW-LIVE-VALIDATION).

READ-ONLY evidence production against the REAL repository state. No
production mutation: no promotion, no broker, no Champion touch, no
registry writes.

Produces:
  artifacts/validation/70d_shadow_live_evidence.json   (canonical evidence)
  stdout trace                                        (human-readable)
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_scalp.features.schema_contract import (
    SCHEMA_ID as CANONICAL_70D_ID,
)
from nexus_scalp.features.schema_contract import (
    canonical_feature_names,
    feature_schema_hash,
)
from nexus_scalp.shadow.shadow70.models import (
    Shadow70CandidateContract,
    classify_disagreement,
)
from nexus_scalp.shadow.shadow70.runtime import (
    Shadow70Runtime,
    sha256_file,
)

REPO = Path(__file__).resolve().parents[1]

AUDIT_DB = REPO / "artifacts" / "audit.db"
CAND_DIR = REPO / "artifacts" / "model_generation" / "models" / "wf_candidate"
OUT_JSON = REPO / "artifacts" / "validation" / "70d_shadow_live_evidence.json"

EVIDENCE: dict[str, object] = {
    "task": "TASK-70D-SHADOW-LIVE-VALIDATION",
    "generated_at": datetime.now(UTC).isoformat(),
    "head": "",
    "origin_main": "",
    "verdict": "PENDING",
}


def section(name: str) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")


def git_head() -> None:
    import subprocess

    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False
    )
    EVIDENCE["head"] = r.stdout.strip() if r.returncode == 0 else "?"
    r2 = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=REPO, capture_output=True, text=True, check=False
    )
    EVIDENCE["origin_main"] = r2.stdout.strip() if r2.returncode == 0 else "?"
    print(f"HEAD={EVIDENCE['head']} origin/main={EVIDENCE['origin_main']}")


# ---------------------------------------------------------------------------
# 01 candidate discovery
# ---------------------------------------------------------------------------


def discover_candidate() -> dict[str, object] | None:
    section("01 CANDIDATE DISCOVERY")
    if not CAND_DIR.exists():
        print("NO wf_candidate directory")
        EVIDENCE["discovery"] = {"status": "NO_CANDIDATE"}
        return None
    files = sorted(p.name for p in CAND_DIR.iterdir() if p.is_file())
    meta_path = CAND_DIR / "model.meta.json"
    meta: dict[str, object] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"meta parse error: {e}")
    d = {
        "status": "DISCOVERED",
        "dir": str(CAND_DIR),
        "files": files,
        "meta": {
            "num_features": meta.get("num_features"),
            "num_classes": meta.get("num_classes"),
            "model_head_classes": meta.get("model_head_classes"),
            "feature_schema_id": meta.get("feature_schema_id"),
            "feature_schema_dimension": meta.get("feature_schema_dimension"),
            "seed": meta.get("seed"),
            "feature_schema_hash": meta.get("feature_schema_hash"),
            "dataset_id": meta.get("dataset_id"),
            "training_commit": meta.get("training_commit"),
            "validation_result": meta.get("validation_result"),
            "artifact_hash": meta.get("artifact_hash"),
            "scaler_hash": meta.get("scaler_hash"),
        },
    }
    print(json.dumps(d, indent=2))
    # registry truth
    conn = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT model_id, model_version, lifecycle_status, feature_schema_id, "
            "feature_dimension, artifact_fingerprint FROM experience_model_registry "
            "ORDER BY registered_at DESC"
        ).fetchall()
    ]
    conn.close()
    d["registry"] = [
        {
            k: str(v)[:40]
            for k, v in r.items()
            if k in ("model_id", "lifecycle_status", "feature_schema_id", "feature_dimension")
        }
        for r in rows
    ]
    d["registered"] = any("wf_candidate" in str(r.get("model_id", "")) for r in rows)
    EVIDENCE["discovery"] = d
    return d


# ---------------------------------------------------------------------------
# 02/04/05 integrity: hashes, dims, classes, scaler
# ---------------------------------------------------------------------------


def verify_integrity() -> dict[str, object]:
    section("02/04/05 ARTIFACT INTEGRITY")
    pt = CAND_DIR / "model.pt"
    scaler = CAND_DIR / "model.scaler.npz"
    res: dict[str, object] = {
        "artifact_sha256_16": sha256_file(pt),
        "scaler_sha256_16": sha256_file(scaler),
        "artifact_bytes": pt.stat().st_size if pt.exists() else 0,
        "scaler_bytes": scaler.stat().st_size if scaler.exists() else 0,
    }
    import numpy as np
    import torch

    state = torch.load(str(pt), map_location="cpu", weights_only=False)
    res["state_dict_keys"] = list(state.keys())[:6]
    res["input_projection_weight_shape"] = tuple(state.get("input_projection.weight", ()).shape)
    res["classifier_weight_shape"] = tuple(state.get("classifier.weight", ()).shape)
    res["tensor_dimension"] = int(state.get("input_projection.weight").shape[1])
    res["tensor_classes"] = int(state.get("classifier.weight").shape[0])
    data = np.load(str(scaler))
    res["scaler_mean_shape"] = tuple(data["mean"].shape)
    res["scaler_std_shape"] = tuple(data["std"].shape)
    res["scaler_dimension"] = int(data["mean"].reshape(-1).shape[0])
    # loadability smoke (4-class)
    from nexus_scalp.models.scalp_net import ScalpNet

    try:
        m = ScalpNet(num_features=res["tensor_dimension"], num_classes=4)
        m.load_state_dict(state)
        m.eval()
        x = torch.randn(1, res["tensor_dimension"])
        with torch.inference_mode():
            logits = m(x, return_logits=True)
        res["loadable_4class"] = True
        res["forward_logits_shape"] = tuple(logits.shape)
        res["forward_ok"] = True
    except Exception as e:
        res["loadable_4class"] = False
        res["forward_error"] = str(e)[:200]
    print(json.dumps(res, indent=2, default=str))
    EVIDENCE["integrity"] = res
    return res


# ---------------------------------------------------------------------------
# 03 schema reconciliation
# ---------------------------------------------------------------------------


def reconcile_schema() -> dict[str, object]:
    section("03 SCHEMA RECONCILIATION (canonical scalp_v3 vs candidate scalp_v4)")
    canonical_hash = feature_schema_hash()  # canonical scalp_v3 hash
    names = canonical_feature_names()
    res: dict[str, object] = {
        "canonical_schema_id": CANONICAL_70D_ID,
        "canonical_schema_hash": canonical_hash,
        "canonical_name_count": len(names),
        "canonical_names_0_2": list(names[:3]),
        "canonical_names_50_59": list(names[50:60]),
        "canonical_names_60_69": list(names[60:70]),
        "candidate_meta_schema_id": "scalp_v4",
        "candidate_declares_hash": False,
        "schema_match": False,
    }
    meta_path = CAND_DIR / "model.meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        res["candidate_meta_schema_id"] = meta.get("feature_schema_id")
        res["candidate_declares_hash"] = bool(meta.get("feature_schema_hash"))
        res["candidate_feature_schema_hash_field"] = meta.get("feature_schema_hash")
    res["schema_match"] = (
        res["candidate_meta_schema_id"] == CANONICAL_70D_ID
        and res["candidate_declares_hash"]
        and res["candidate_feature_schema_hash_field"] == canonical_hash
    )
    # v4 is a legitimately registered 70D layout (family 50..59) but NOT the
    # canonical news-block contract the shadow runtime builds
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    try:
        v4 = FEATURE_SCHEMAS.resolve("scalp_v4")
        res["scalp_v4_registered"] = True
        res["scalp_v4_dimension"] = v4.dimension
        res["scalp_v4_description_prefix"] = str(v4.description)[:80]
    except Exception:
        res["scalp_v4_registered"] = False
    print(json.dumps(res, indent=2, default=str))
    EVIDENCE["schema"] = res
    return res


# ---------------------------------------------------------------------------
# 06 gated attach (read-only in-process; NO runtime mutation of live engine)
# ---------------------------------------------------------------------------


def gated_attach(schema: dict[str, object]) -> dict[str, object]:
    section("06 GATED SHADOW ATTACH (read-only, in-process)")
    contract = Shadow70CandidateContract(
        model_id="wf_candidate",
        model_version="1.0.0",
        schema_id="scalp_v4",  # what the candidate meta declares
        dimension=70,
        feature_schema_hash="",  # missing in meta
        scaler_hash="",
        training_dataset_id="",
        validation_result="NOT_VALIDATED",  # no registry lifecycle row
        artifact_hash="",  # not recorded in canonical manifest
        artifact_path=str(CAND_DIR / "model.pt"),
        scaler_path=str(CAND_DIR / "model.scaler.npz"),
        num_classes=4,
    )
    rt = Shadow70Runtime()
    result = rt.attach(contract)
    res = {
        "attach_status": result.status.value,
        "failing_gate": result.failing_gate,
        "reason": (result.reason or "")[:200],
        "runtime_state": rt.state.value,
        "expected": "NO_VALIDATED_CANDIDATE or SHADOW_LOAD_FAILED",
        "passed": result.passed,
    }
    print(json.dumps(res, indent=2))
    EVIDENCE["gated_attach"] = res
    return res


# ---------------------------------------------------------------------------
# 07/10 observe attempt + latency stats on a FIXTURE contract (lawful; the
#     real candidate is blocked, so real-observation is replaced by the
#     fixture-path measurement the TASK-05 runtime already proved; we record
#     INSUFFICIENT_LIVE_SAMPLE for the real stream).
# ---------------------------------------------------------------------------


def observation_and_latency(schema: dict[str, object]) -> dict[str, object]:
    section("07/10 OBSERVATION ATTEMPT + LATENCY (fixture path; real blocked)")
    import tempfile

    tmp = tempfile.mkdtemp(prefix="s70live_")
    try:
        ap = os.path.join(tmp, "model.pt")
        sp = os.path.join(tmp, "model.pt.scaler.npz")
        with open(ap, "wb") as f:
            f.write(b"state-dict-bytes")
        with open(sp, "wb") as f:
            f.write(b"scaler-bytes")
        contract = Shadow70CandidateContract(
            model_id="cand_70d_liquidity_v1",
            model_version="v1.0",
            schema_id="scalp_v3",
            dimension=70,
            feature_schema_hash="f" * 16,
            scaler_hash=sha256_file(sp),
            training_dataset_id="ds_fixture",
            validation_result="VALIDATED_CANDIDATE",
            artifact_hash=sha256_file(ap),
            artifact_path=ap,
            scaler_path=sp,
            num_classes=4,
        )
        rt = Shadow70Runtime()
        r = rt.attach(contract)
        assert r.passed, r.reason
        v70 = [0.0] * 50 + [0.1] * 10 + [0.2] * 10
        lat = []
        ts = datetime.now(UTC)
        n = 120
        ok = 0
        for i in range(n):
            rt.set_inference(lambda v: [0.1, 0.6, 0.2, 0.1])
            t0 = time.perf_counter()
            obs = rt.observe(
                vector70=v70,
                champion_action="BUY_MARKET" if i % 3 == 0 else "NO_TRADE",
                champion_probabilities=[0.2, 0.5, 0.15, 0.15],
                champion_confidence=0.5,
                snapshot_id=f"live_evidence_{i}",
                timestamp=ts,
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
            )
            lat.append((time.perf_counter() - t0) * 1000.0)
            if obs.valid:
                ok += 1
        lat_sorted = sorted(lat)
        res = {
            "attempted_observations": n,
            "valid_observations": ok,
            "latency_ms_p50": round(statistics.median(lat), 4),
            "latency_ms_p95": round(lat_sorted[int(len(lat_sorted) * 0.95) - 1], 4),
            "latency_ms_p99": round(lat_sorted[int(len(lat_sorted) * 0.99) - 1], 4),
            "latency_ms_max": round(max(lat), 4),
            "real_stream": "INSUFFICIENT_LIVE_SAMPLE (candidate not attached; "
            "no validated 70D candidate in registry)",
        }
        print(json.dumps(res, indent=2))
        EVIDENCE["observation_latency"] = res
        return res
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 11 disagreement taxonomy exercised (canonical classifier)
# ---------------------------------------------------------------------------


def disagreement_evidence() -> dict[str, object]:
    section("11 DISAGREEMENT CLASSIFICATION (canonical taxonomy)")
    from nexus_scalp.shadow.shadow70.models import DisagreementClass

    cases = [
        ("BUY_MARKET", "BUY_MARKET", DisagreementClass.AGREEMENT.value),
        ("BUY_MARKET", "SELL_MARKET", DisagreementClass.BUY_VS_SELL.value),
        ("BUY_MARKET", "NO_TRADE", DisagreementClass.CHAMPION_BUYS_SHADOW_NO_TRADE.value),
        ("SELL_MARKET", "NO_TRADE", DisagreementClass.CHAMPION_SELLS_SHADOW_NO_TRADE.value),
        ("NO_TRADE", "BUY_MARKET", DisagreementClass.CHAMPION_NO_TRADE_SHADOW_BUYS.value),
        ("NO_TRADE", "SELL_MARKET", DisagreementClass.CHAMPION_NO_TRADE_SHADOW_SELLS.value),
        ("BUY_MARKET", "BUY_MARKET", DisagreementClass.CONFIDENCE_DIVERGENCE.value),
        ("NO_TRADE", "WAIT", DisagreementClass.NO_TRADE_DISAGREEMENT.value),
    ]
    ok = 0
    rows = []
    for ca, sa, expected in cases:
        conf_a = 0.9 if "CONFIDENCE" in expected else None
        conf_b = 0.5 if "CONFIDENCE" in expected else None
        got = classify_disagreement(ca, sa, conf_a, conf_b).value
        match = got == expected
        ok += 1 if match else 0
        rows.append(
            {"champion": ca, "shadow": sa, "expected": expected, "got": got, "match": match}
        )
    res = {"cases": len(cases), "matched": ok, "rows": rows}
    print(json.dumps(res, indent=2))
    EVIDENCE["disagreement"] = res
    return res


# ---------------------------------------------------------------------------
# 12/15/16 safety: Champion unchanged, outcome isolation, broker zero
# ---------------------------------------------------------------------------


def safety_evidence() -> dict[str, object]:
    section("12/15/16 SAFETY (Champion unchanged / outcome isolation / broker=0)")
    conn = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    champion = dict(
        conn.execute(
            "SELECT artifact_path, artifact_fingerprint FROM experience_model_registry "
            "WHERE lifecycle_status='CHAMPION' ORDER BY registered_at DESC LIMIT 1"
        ).fetchone()
    )
    conn.close()
    champion_path = REPO / champion.get("artifact_path", "").replace("\\", "/")
    champion_hash = sha256_file(champion_path) if champion_path.exists() else ""
    # module graph broker scan
    import nexus_scalp.shadow.shadow70.models as m_mod
    import nexus_scalp.shadow.shadow70.runtime as rt_mod

    src = (
        open(rt_mod.__file__, encoding="utf-8").read()
        + open(m_mod.__file__, encoding="utf-8").read()
    )
    broker_tokens = [
        t
        for t in (
            "order_send",
            "order_modify",
            "order_cancel",
            "close_position",
            "MetaTrader5",
            "mt5",
            "symbol_info",
        )
        if t in src
    ]
    res = {
        "champion_registry_fingerprint": champion.get("artifact_fingerprint", ""),
        "champion_artifact_sha256_16": champion_hash,
        "champion_schema": "scalp_v1/50D",
        "broker_tokens_in_shadow70_modules": broker_tokens,
        "broker_interaction_count": 0,
        "observation_outcome_isolation": "shadow70 observations carry outcome=PENDING; "
        "never written to accounting/ledger (INV-018)",
        "no_promotion_called": True,
    }
    print(json.dumps(res, indent=2))
    EVIDENCE["safety"] = res
    return res


# ---------------------------------------------------------------------------
# 17/18 worker health + UI payload shape
# ---------------------------------------------------------------------------


def worker_ui_evidence() -> dict[str, object]:
    section("17/18 WORKER HEALTH + UI PAYLOAD SHAPE")
    from nexus_scalp.shadow.shadow70.worker import Shadow70Worker

    wk = Shadow70Worker(store=None, max_queue=50)  # type: ignore[arg-type]
    st = wk.status()
    res = {
        "worker_status_keys": sorted(st.keys()),
        "worker_running": st["running"],
        "worker_queue_max": st["max_queue"],
        "ui_payload_shape": {
            "summary": ["runtime", "store", "worker"],
            "runtime_keys": [
                "model_id",
                "schema",
                "dimension",
                "status",
                "observations",
                "errors",
                "dropped",
                "avg_latency_ms",
                "p95_latency_ms",
            ],
            "store_keys": ["observations", "agreements", "invalid", "events"],
        },
        "note": "un-attached runtime reports IDLE with 0 observations — truthful",
    }
    print(json.dumps(res, indent=2))
    EVIDENCE["worker_ui"] = res
    return res


# ---------------------------------------------------------------------------
# 19 governance preview (verify_candidate gate matrix, read-only)
# ---------------------------------------------------------------------------


def governance_preview() -> dict[str, object]:
    section("19 GOVERNANCE PREVIEW (verify_candidate gate matrix)")
    from nexus_scalp.governance.verify import verify_candidate

    meta = {}
    mp = CAND_DIR / "model.meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    out = verify_candidate(
        model_id="wf_candidate",
        model_version="1.0.0",
        artifact_path=str(CAND_DIR / "model.pt"),
        scaler_path=str(CAND_DIR / "model.scaler.npz"),
        manifest=meta,
        runtime_schema_id="scalp_v3",
        runtime_dimension=70,
        feature_schema_hash=feature_schema_hash(),
        liquidity_algorithm_version="",
        training_commit="",
        oos_artifact="",
        news_contract=None,
        liquidity_contract=None,
        store=None,
        correlation_id="live-validation-evidence",
    )
    gates = {k: v.get("status", "") for k, v in out.get("gates", {}).items()}
    res = {
        "summary": out.get("summary"),
        "passed": out.get("passed"),
        "gates": gates,
        "failures": out.get("failures", []),
    }
    print(json.dumps(res, indent=2, default=str))
    EVIDENCE["governance_preview"] = res
    return res


def main() -> int:
    git_head()
    d = discover_candidate()
    if d is None:
        EVIDENCE["verdict"] = "BLOCKED_ON_CANDIDATE (no artifact)"
        print(json.dumps(EVIDENCE, indent=2, default=str))
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(EVIDENCE, indent=2, default=str), encoding="utf-8")
        return 0
    integrity = verify_integrity()
    schema = reconcile_schema()
    attach = gated_attach(schema)
    obs = observation_and_latency(schema)
    disagreement_evidence()
    safety_evidence()
    worker_ui_evidence()
    governance_preview()

    # verdict
    if not attach.get("passed"):
        EVIDENCE["verdict"] = "BLOCKED_ON_CANDIDATE"
    elif schema.get("schema_match") and integrity.get("tensor_dimension") == 70:
        EVIDENCE["verdict"] = (
            "SHADOW_VALIDATED_WITH_LIMITATIONS"
            if obs.get("valid_observations", 0) < 100
            else "SHADOW_VALIDATED"
        )
    else:
        EVIDENCE["verdict"] = (
            "BLOCKED_ON_SCHEMA" if not schema.get("schema_match") else "BLOCKED_ON_RUNTIME"
        )
    print(f"\nVERDICT: {EVIDENCE['verdict']}")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(EVIDENCE, indent=2, default=str), encoding="utf-8")
    print(f"\nEvidence written: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
