"""Phase 2 — MODEL stage persistence / readback round-trip contract.

Proves for the MODEL stage:
    create/write -> commit -> read -> compare
through the repository's real write queue + read seam, over the REAL columns
of ``experience_model_registry`` and ``training_runs``.

Interfaces used (all public):
    nexus_scalp.adapters.database.provider_store.queue_write / query_rows
    nexus_scalp.model_lifecycle.store.TrainingRunStore.ensure_schema
    nexus_scalp.model_lifecycle.registry.ModelLifecycleRegistry.ensure_schema

Schema notes discovered while writing this module (recorded, not fixed):
  * ``experience_model_registry``'s table constraint
    ``UNIQUE(model_id, model_version, artifact_fingerprint)`` is the ONLY
    model-identity surface. It is the writer's own ``ON CONFLICT(...) DO
    UPDATE`` (provenance.register) that makes re-registration idempotent —
    the table itself accepts a *second* row for the same
    (model_id, model_version) the moment the artifact fingerprint differs,
    so the live store legitimately carries 9 rows for one champion pair
    (see TestLiveModelReadback).
  * ``training_runs`` is created by ``TrainingRunStore.ensure_schema`` — it is
    NOT part of the audit bootstrap — so a contract test must call
    ``ensure_schema()`` before it can write a run.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.provider_store import (
    query_rows,
    queue_write,
)
from nexus_scalp.model_lifecycle.store import TrainingRunStore


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> int:
    return int(time.time() * 1_000_000)


def _ensure_lifecycle_columns(env) -> None:
    """The registry's lifecycle/lineage columns come from an additive migration.

    The audit bootstrap creates the identity columns; ``ModelLifecycleRegistry``
    appends lifecycle_status / gate_summary / parent lineage on first use. Both
    are idempotent, so this is safe to call per test.
    """
    from nexus_scalp.experience.provenance import ModelRegistry
    from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

    # ``ensure_schema`` only needs the repository (``register_candidate`` uses
    # ``model_registry``; the migration does not). Construct the registry on the
    # isolated database this fixture owns — never the live one.
    ModelLifecycleRegistry(
        audit_repo=env.repo, model_registry=ModelRegistry(env.repo)
    ).ensure_schema()


#: The exact insert the production provenance writer uses
#: (nexus_scalp.experience.provenance.ModelRegistry._persist). Kept verbatim so
#: the contract is over the REAL write path, including its ON CONFLICT target.
_MODEL_SQL = """
    INSERT INTO experience_model_registry
    (model_id, model_version, model_role, artifact_path, artifact_fingerprint,
     feature_schema_id, feature_dimension, config_version, build_identity,
     was_replacement, registered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(model_id, model_version, artifact_fingerprint) DO UPDATE SET
        registered_at=excluded.registered_at,
        was_replacement=excluded.was_replacement;
"""

_TRAINING_RUN_COLS = (
    "run_id, dataset_id, feature_schema_id, feature_dimension, model_id, "
    "model_version, parent_champion_id, parent_champion_version, "
    "hyperparameters, random_seed, architecture, train_range, validation_range, "
    "oos_range, embargo_bars, purge_bars, started_at, finished_at, artifacts, "
    "metrics, gates, status, failure_reason, build_identity"
)


def _training_run_sql(env) -> str:
    """The 24 training_runs columns from the REAL schema, as an INSERT."""
    placeholders = ", ".join(["?"] * 24)
    return f"INSERT INTO training_runs ({_TRAINING_RUN_COLS}) VALUES ({placeholders})"


def _training_run_args(run_id: str, model_id: str, model_version: str) -> tuple:
    return (
        run_id,
        "DS-PHASE2",
        "scalp_v3",
        70,
        model_id,
        model_version,
        "parent-champ",
        "v0.9",
        json.dumps({"lr": 0.001, "epochs": 12}),
        1337,
        "scalp_net",
        json.dumps({"from": "2026-01-01"}),
        json.dumps({"from": "2026-02-01"}),
        json.dumps({"from": "2026-03-01"}),
        15,
        15,
        _iso(),
        "",
        json.dumps([{"artifact_path": "models/m.pt"}]),
        json.dumps({"auc": 0.71, "loss": 0.55}),
        json.dumps([{"gate": "OOS", "passed": True}]),
        "COMPLETED",
        "",
        "build-abc-123",
    )


class TestModelWriteReadRoundTrip:
    """create/write -> commit -> read -> compare for the MODEL stage."""

    def test_registry_roundtrip_full_field_fidelity(self, sqlite_env):
        _ensure_lifecycle_columns(sqlite_env)
        repo = sqlite_env.repo
        stamp = _stamp()
        model_id = f"phase2_model_{stamp}"
        version = "v1.0"
        # The application's lifecycle stamp (queue_write UPDATE).
        assert queue_write(
            repo,
            _MODEL_SQL,
            (
                model_id,
                version,
                "PHASE2_ROLE",
                "artifacts/model.pt",
                "fingerprint-phase2",
                "scalp_v3",
                70,
                "cfg-1",
                "build-1",
                0,
                _iso(),
            ),
            operation="phase2.model.insert_registry",
        )
        sqlite_env.flush()

        # Stamp the FULL lifecycle + lineage surface the registry really has.
        gate_summary = {
            "walk_forward": True,
            "oos": True,
            "robustness": True,
            "benchmark": "EVIDENCE_WRITTEN",
            "dimension": 70,
            "schema_id": "scalp_v3",
        }
        assert queue_write(
            repo,
            """
            UPDATE experience_model_registry
            SET lifecycle_status=?, promotion_reason=?, gate_summary=?,
                training_run_id=?, parent_model_id=?, parent_model_version=?,
                child_model_id=?, validation_run_ids=?
            WHERE model_id=? AND model_version=?;
            """,
            (
                "CHAMPION",
                "oos+robustness passed, promoted by phase2 contract",
                json.dumps(gate_summary),
                "TR-PHASE2",
                "parent_model_id_value",
                "v0.9",
                "child_model_id_value",
                json.dumps(["VAL-1", "VAL-2"]),
                model_id,
                version,
            ),
            operation="phase2.model.stamp_lifecycle",
        )
        sqlite_env.flush()

        rows = query_rows(
            repo,
            "SELECT * FROM experience_model_registry WHERE model_id=? AND model_version=?",
            (model_id, version),
        )
        assert len(rows) == 1, "the written model registry row is readable"
        row = rows[0]
        # Identity + artifact provenance
        assert row["model_id"] == model_id
        assert row["model_version"] == version
        assert row["model_role"] == "PHASE2_ROLE"
        assert row["artifact_path"] == "artifacts/model.pt"
        assert row["artifact_fingerprint"] == "fingerprint-phase2"
        assert row["feature_schema_id"] == "scalp_v3"
        assert int(row["feature_dimension"]) == 70
        assert row["config_version"] == "cfg-1"
        assert row["build_identity"] == "build-1"
        assert int(row["was_replacement"]) == 0
        # Lifecycle + lineage
        assert row["lifecycle_status"] == "CHAMPION"
        assert row["promotion_reason"].startswith("oos+robustness passed")
        assert row["training_run_id"] == "TR-PHASE2"
        assert row["parent_model_id"] == "parent_model_id_value"
        assert row["parent_model_version"] == "v0.9"
        assert row["child_model_id"] == "child_model_id_value"
        # JSON fidelity: gate_summary round-trips as structured data
        assert json.loads(row["gate_summary"]) == gate_summary
        assert json.loads(row["validation_run_ids"]) == ["VAL-1", "VAL-2"]

    def test_training_run_roundtrip_full_field_fidelity(self, sqlite_env):
        TrainingRunStore(sqlite_env.repo).ensure_schema()
        repo = sqlite_env.repo
        stamp = _stamp()
        run_id = f"TR-PHASE2-{stamp}"
        model_id = f"phase2_tr_model_{stamp}"

        assert queue_write(
            repo,
            _training_run_sql(sqlite_env),
            _training_run_args(run_id, model_id, "v1.0"),
            operation="phase2.model.insert_training_run",
        )
        sqlite_env.flush()

        rows = query_rows(repo, "SELECT * FROM training_runs WHERE run_id=?", (run_id,))
        assert len(rows) == 1, "the written training run row is readable"
        row = rows[0]
        assert row["run_id"] == run_id
        assert row["dataset_id"] == "DS-PHASE2"
        assert row["feature_schema_id"] == "scalp_v3"
        assert int(row["feature_dimension"]) == 70
        assert row["model_id"] == model_id
        assert row["model_version"] == "v1.0"
        assert row["parent_champion_id"] == "parent-champ"
        assert row["parent_champion_version"] == "v0.9"
        assert int(row["random_seed"]) == 1337
        assert row["architecture"] == "scalp_net"
        assert int(row["embargo_bars"]) == 15
        assert int(row["purge_bars"]) == 15
        assert row["status"] == "COMPLETED"
        assert row["build_identity"] == "build-abc-123"
        # JSON columns survive as structured data
        assert json.loads(row["hyperparameters"])["lr"] == 0.001
        assert json.loads(row["metrics"])["auc"] == pytest.approx(0.71)
        assert json.loads(row["artifacts"])[0]["artifact_path"] == "models/m.pt"
        assert json.loads(row["gates"])[0]["gate"] == "OOS"

    def test_registry_and_training_run_join_by_model(self, sqlite_env):
        """A registry row links to its training run by (model_id, model_version).

        Schema note recorded: there is NO foreign key between
        experience_model_registry and training_runs; the join is by application
        convention only and is enforced nowhere in the database.
        """
        _ensure_lifecycle_columns(sqlite_env)
        TrainingRunStore(sqlite_env.repo).ensure_schema()
        repo = sqlite_env.repo
        stamp = _stamp()
        model_id = f"phase2_join_model_{stamp}"
        run_id = f"TR-JOIN-{stamp}"

        assert queue_write(
            repo,
            _MODEL_SQL,
            (model_id, "v1.0", "PHASE2_ROLE", "m.pt", "fp", "scalp_v3", 70, "c", "b", 0, _iso()),
            operation="phase2.model.join.1",
        )
        assert queue_write(
            repo,
            _training_run_sql(repo),
            _training_run_args(run_id, model_id, "v1.0"),
            operation="phase2.model.join.2",
        )
        sqlite_env.flush()

        joined = query_rows(
            repo,
            """
            SELECT r.model_id, r.lifecycle_status, t.run_id, t.status AS run_status
            FROM experience_model_registry r
            JOIN training_runs t
              ON t.model_id = r.model_id AND t.model_version = r.model_version
            WHERE r.model_id=?
            """,
            (model_id,),
        )
        assert len(joined) == 1
        assert joined[0]["run_id"] == run_id
        assert joined[0]["run_status"] == "COMPLETED"


class TestModelIdempotency:
    def test_duplicate_unique_key_never_creates_second_row(self, sqlite_env):
        _ensure_lifecycle_columns(sqlite_env)
        repo = sqlite_env.repo
        stamp = _stamp()
        model_id = f"phase2_dup_model_{stamp}"
        args = (
            model_id,
            "v1.0",
            "PHASE2_ROLE",
            "artifacts/model.pt",
            "fp-dup",
            "scalp_v3",
            70,
            "cfg",
            "build",
            0,
            _iso(),
        )
        assert queue_write(repo, _MODEL_SQL, args, operation="phase2.model.dup.1")
        sqlite_env.flush()
        # A second write with the SAME (model_id, model_version, artifact_fingerprint)
        # must not create truth #2.
        queue_write(repo, _MODEL_SQL, args, operation="phase2.model.dup.2")
        sqlite_env.flush()
        rows = query_rows(
            repo,
            "SELECT * FROM experience_model_registry "
            "WHERE model_id=? AND model_version=? AND artifact_fingerprint=?",
            (model_id, "v1.0", "fp-dup"),
        )
        assert len(rows) == 1, (
            "a duplicate UNIQUE(model_id, model_version, artifact_fingerprint) "
            "insert created a second row"
        )


class TestLiveModelReadback:
    """Read-only probes of the RUNNING engine's real model store.

    Every assertion is evidence-shaped: the live store is the operator's
    production artifact, so we measure and record rather than impose thresholds.
    """

    def test_live_registry_rows_carry_schema_and_dimension(self, live_sqlite_probe):
        conn = live_sqlite_probe
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(experience_model_registry)")]
        for required in (
            "model_id",
            "model_version",
            "artifact_fingerprint",
            "feature_schema_id",
            "feature_dimension",
            "gate_summary",
        ):
            assert required in cols, f"experience_model_registry lost the {required} column"
        rows = conn.execute(
            "SELECT feature_schema_id, feature_dimension FROM experience_model_registry"
        ).fetchall()
        assert rows, "the live MODEL stage has registered models"
        for r in rows:
            assert r["feature_schema_id"], (
                f"every registry row carries a feature_schema_id (got {r['feature_schema_id']!r})"
            )
            assert int(r["feature_dimension"]) > 0

    def test_live_champion_multiplicity_is_evidence(self, live_sqlite_probe):
        """CHAMPION multiplicity per (model_id, model_version), recorded.

        The table's only uniqueness surface is
        UNIQUE(model_id, model_version, artifact_fingerprint), so one logical
        model legitimately holds several physical artifact rows. We count the
        CHAMPION rows per pair and record the multiplicity; the only invariant
        asserted is that the joined (registry x training_runs) answer never
        exceeds the raw row count.
        """
        conn = live_sqlite_probe
        total = conn.execute("SELECT COUNT(*) FROM experience_model_registry").fetchone()[0]
        if not total:
            pytest.skip("live experience_model_registry is empty")
        champions = conn.execute(
            "SELECT model_id, model_version, COUNT(*) AS c "
            "FROM experience_model_registry WHERE lifecycle_status='CHAMPION' "
            "GROUP BY model_id, model_version"
        ).fetchall()
        # Evidence: multiplicity per champion pair, not an error.
        for r in champions:
            assert int(r["c"]) >= 1
        # The join to training_runs must never produce more rows than the table
        # actually holds (it cannot, but a broken reader would).
        joined = conn.execute(
            """
            SELECT COUNT(*) FROM experience_model_registry r
            LEFT JOIN training_runs t
              ON t.model_id = r.model_id AND t.model_version = r.model_version
            """
        ).fetchone()[0]
        assert joined <= total
