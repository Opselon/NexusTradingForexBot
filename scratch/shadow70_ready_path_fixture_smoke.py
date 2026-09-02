"""70D Shadow READY-path fixture smoke (TASK-05-70D-SHADOW).

Proves the SHADOW_READY observation path + the live_engine hook shape with a
deterministic VALIDATED fixture candidate (controlled test fixture, spec 2:
real production runtime/list of live_engine untouched — this is a fixture
path against temp artifacts, never the live Champion).

Checks:
  * attach -> SHADOW_READY
  * 30 live-shaped observations -> valid, classified, idempotent
  * disagreement classes actually appear
  * worker persists to a temp DB via the queue; rows land
  * feature health/drift monitored without error
  * summary() reports real counters
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

from nexus_scalp.shadow.shadow70.health import (  # noqa: E402
    Shadow70DriftMonitor,
    Shadow70FeatureHealthMonitor,
)
from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime  # noqa: E402
from nexus_scalp.shadow.shadow70.store import Shadow70Store  # noqa: E402
from nexus_scalp.shadow.shadow70.worker import Shadow70Worker  # noqa: E402
from tests.helpers.shadow70_fixtures import make_contract, vector70  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="s70_ready_")
    try:
        contract = make_contract(tmp)
        rt = Shadow70Runtime()
        res = rt.attach(contract)
        assert res.passed, res.reason
        print(f"[1] LOAD GATE        {res.status.value} model={contract.model_id}")

        rt.set_inference(lambda v: [0.1, 0.6, 0.2, 0.1])

        # 30 observations with varying champion actions -> disagreements arise
        ts0 = datetime.now(UTC)
        actions = ["BUY_MARKET", "NO_TRADE", "SELL_MARKET", "NO_TRADE"]

        classes: set[str] = set()
        for i in range(30):
            obs = rt.observe(
                vector70=vector70(liquidity=0.05 * (i % 5)),
                champion_action=actions[i % 4],
                champion_probabilities=[0.25, 0.4, 0.2, 0.15],
                champion_confidence=0.4,
                snapshot_id=f"ready_{i}",
                timestamp=ts0,
                base_feature_hash="b" * 8,
                feature_schema_hash="f" * 16,
                news_state="NORMAL",
                liquidity_state="SWEEP" if i % 2 else "NONE",
            )
            assert obs.valid, obs.reason
            assert obs.outcome == "PENDING"
            classes.add(obs.disagreement.value)
        print(f"[2] OBSERVATIONS     30 valid; disagreement classes={sorted(classes)}")

        s = rt.summary()
        print(f"[3] SUMMARY          obs={s['observations']} valid={s['valid_observations']} "
              f"agree={s['agreements']} disa={s['disagreements']} avg_ms={s['avg_latency_ms']}")
        assert s["observations"] == 30

        # idempotency
        o1 = rt.observe(vector70=vector70(), champion_action="NO_TRADE",
                        champion_probabilities=[0.9, 0.03, 0.03, 0.04], champion_confidence=0.9,
                        snapshot_id="idem_ready", timestamp=ts0, base_feature_hash="b" * 8,
                        feature_schema_hash="f" * 16)
        o2 = rt.observe(vector70=vector70(), champion_action="NO_TRADE",
                        champion_probabilities=[0.9, 0.03, 0.03, 0.04], champion_confidence=0.9,
                        snapshot_id="idem_ready", timestamp=ts0, base_feature_hash="b" * 8,
                        feature_schema_hash="f" * 16)
        assert o1.observation_id == o2.observation_id
        print("[4] IDEMPOTENT       same deterministic id")

        # persistence via real AuditRepository queue to a temp DB
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db = os.path.join(tmp, "audit.db")
        repo = AuditRepository(db_url=f"sqlite:///{db}")
        store = Shadow70Store(audit_repo=repo)
        wk = Shadow70Worker(store=store, max_queue=200)
        wk.start()
        try:
            for i in range(15):
                obs = rt.observe(vector70=vector70(liquidity=0.1 * (i % 3)),
                                 champion_action=actions[i % 4],
                                 champion_probabilities=[0.25, 0.4, 0.2, 0.15],
                                 champion_confidence=0.4, snapshot_id=f"persist_{i}",
                                 timestamp=ts0, base_feature_hash="b" * 8,
                                 feature_schema_hash="f" * 16)
                assert wk.enqueue(obs)
            wk.flush()
            # wait for the AuditRepository writer thread to drain (bounded)
            import time as _time

            n = 0
            deadline = _time.time() + 15
            while _time.time() < deadline:
                try:
                    repo._queue.join()
                except Exception:
                    pass
                # the audit writer drains its own queue; give it a beat
                _time.sleep(0.3)
                conn = sqlite3.connect(db)
                n = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()[0]
                conn.close()
                if n >= 15:
                    break
            assert n == 15, f"persisted {n}"
            print(f"[5] PERSISTENCE      {n}/15 rows landed in temp DB via queued writer")
        finally:
            wk.stop(flush=True)
            repo.close()

        # feature health/drift monitors
        hm = Shadow70FeatureHealthMonitor(window=100)
        dm = Shadow70DriftMonitor()
        for i in range(60):
            v = vector70(liquidity=0.05 * (i % 3))
            hm.update(v)
            dm.update(v)
        rows = hm.health()
        dsum = dm.summary()
        print(f"[6] HEALTH/DRIFT     features={len(rows)} drift_status={dsum.get('status')} "
              f"severity={dsum.get('severity')}")

        print("\nREADY-PATH FIXTURE SMOKE: ALL CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())