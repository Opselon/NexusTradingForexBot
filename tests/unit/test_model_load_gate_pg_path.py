"""
ModelLoadGate construction: db_path must never be a provider URI (PG-DBPATH-BOOT-001)
====================================================================================
``AuditRepository._db_path`` is a provider URI (``postgresql://...``) under a
non-SQLite provider since PG-READ-PLANE-001, and ``live_engine`` passes it into
``ModelLoadGate(db_path=...)`` during challenger attach. ``Path()`` of that URI is
a meaningless location that would poison any later sqlite use, so the gate must
stay inert (``self.db_path is None``) instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.governance.load_gate import (
    ModelLoadGate,
    sha256_hex,
)

PG_URI = "postgresql://localhost:5432/nexusdb"


class TestModelLoadGateDbPath:
    def test_sqlite_path_round_trips_unchanged(self):
        # No-regression: a normal sqlite filesystem path is preserved as-is.
        gate = ModelLoadGate(db_path="/var/lib/nexus/audit.db")
        assert gate.db_path == Path("/var/lib/nexus/audit.db")

    def test_postgresql_uri_string_becomes_inert(self):
        # A provider URI string must not become a junk Path.
        gate = ModelLoadGate(db_path=PG_URI)
        assert gate.db_path is None

    def test_postgresql_uri_path_object_becomes_inert(self):
        # The same guard must hold when the URI arrives already wrapped in Path
        # (Path("postgresql://...") never raises; POSIX collapses "//" and
        # Windows rewrites it to "postgresql:\host:5432\nexusdb").
        gate = ModelLoadGate(db_path=Path(PG_URI))
        assert gate.db_path is None

    def test_pathlib_mangled_uri_still_inert(self):
        # If a caller wraps the URI in Path first, pathlib normalizes it to
        # "postgresql:\host:5432\db" on Windows and "postgresql:/host:5432/db"
        # on POSIX. The guard must catch the mangled form too, since that is
        # exactly the junk value Path() would otherwise store.
        mangled = Path(PG_URI)
        assert str(mangled) != PG_URI
        assert ModelLoadGate(db_path=mangled).db_path is None

    @pytest.mark.parametrize("empty", [None, ""])
    def test_empty_inputs_stay_inert(self, empty):
        gate = ModelLoadGate(db_path=empty)
        assert gate.db_path is None

    def test_other_scheme_uri_also_becomes_inert(self):
        # The guard is scheme-agnostic: any scheme-bearing URI is not a path.
        gate = ModelLoadGate(db_path="mysql://user:pass@dbHost:3306/nexus")
        assert gate.db_path is None

    def test_windows_path_with_drive_is_preserved(self):
        # A Windows drive path contains "://" in no form; ensure it survives.
        gate = ModelLoadGate(db_path=r"C:\nexus\db\audit.db")
        assert gate.db_path == Path(r"C:\nexus\db\audit.db")

    def test_evaluate_still_passes_on_real_artifact(self, tmp_path):
        # The guard did not break the happy path: the gate still reaches a PASS
        # verdict on a valid challenger artifact with the PG URI passed in.
        import numpy as np
        import torch

        art = tmp_path / "model.pt"
        torch.save({"input_projection.weight": torch.zeros(3, 50)}, art)
        sca = tmp_path / "scaler.npz"
        np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
        manifest = {
            "model_id": "challenger",
            "model_version": "v1",
            "feature_schema_id": "scalp_v1",
            "feature_dimension": 50,
            "class_count": 3,
            "label_schema_id": "triple_barrier_3class_v1",
            "role": "CHALLENGER",
            "artifact_hash": sha256_hex(art),
        }
        gate = ModelLoadGate(db_path=PG_URI)
        res = gate.evaluate(
            artifact_path=art,
            scaler_path=sca,
            model_id="challenger",
            model_version="v1",
            manifest=manifest,
            lifecycle_state="CHALLENGER",
        )
        assert res.passed is True
        assert res.failing_gate is None
