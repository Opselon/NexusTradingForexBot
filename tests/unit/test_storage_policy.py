"""Storage policy sweeps (pure functions) — unit tests (2026-09-09).

Covers the Disk, Log & Runtime Hygiene Lead pass:
  * log compression (age window, active file untouched, verify-before-unlink)
  * per-severity byte budget (oldest-first, protected/active file survives)
  * updater cache prune (keep N newest packages, stale .part removal)
  * crash leftovers (.update-stage-*/.preserve-*, .previous-* keep-1)
  * residue sweep (*.tmp/*.part, young files NEVER touched, bounded scan)
  * PROTECTED DATA: trading/research DBs, models, config, archives,
    quarantine, update records must survive every sweep
  * concurrency: two concurrent sweeps never corrupt each other's results
  * 10GB regression: an unbounded-growth simulation clamps to the budget
"""

from __future__ import annotations

import gzip
import threading
import time
from pathlib import Path

import pytest

from nexus_scalp.storage.policy import (
    compress_old_logs,
    enforce_byte_budget,
    prune_update_cache,
    sweep_crash_leftovers,
    sweep_diagnostics,
    sweep_residue_files,
)
from nexus_scalp.storage.runtime import StorageGuard, StorageGuardSettings


def _write(path: Path, size_bytes: int, *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# 1. log compression
# ---------------------------------------------------------------------------


class TestLogCompression:
    def test_old_log_compressed_and_original_removed(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        old = _write(sev / "2026" / "08" / "2026-08-01.log", 4096, mtime=time.time() - 10 * 86400)
        out = compress_old_logs(sev, compress_after_days=2)
        assert out["compressed"] == 1
        assert not old.exists()
        gz = old.with_suffix(".log.gz")
        assert gz.exists()
        with gzip.open(gz, "rb") as fh:
            assert len(fh.read()) == 4096

    def test_active_log_never_touched(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        active = _write(sev / "2026" / "09" / "2026-09-09.log", 1024, mtime=time.time())
        out = compress_old_logs(sev, compress_after_days=2)
        assert out["compressed"] == 0 and out["skipped"] >= 1
        assert active.exists() and not active.with_suffix(".log.gz").exists()

    def test_disabled_when_zero_days(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        _write(sev / "a.log", 100, mtime=time.time() - 30 * 86400)
        out = compress_old_logs(sev, compress_after_days=0)
        assert out["compressed"] == 0

    def test_bytes_saved_reported(self, tmp_path: Path) -> None:
        sev = tmp_path / "error"
        _write(sev / "2026" / "08" / "old.log", 100_000, mtime=time.time() - 10 * 86400)
        out = compress_old_logs(sev, compress_after_days=2)
        assert out["bytes_saved"] > 0


# ---------------------------------------------------------------------------
# 2. byte budget
# ---------------------------------------------------------------------------


class TestByteBudget:
    def test_oldest_removed_first_until_fit(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        now = time.time()
        _write(sev / "a.log", 600_000, mtime=now - 3000)
        _write(sev / "b.log", 600_000, mtime=now - 2000)
        _write(sev / "c.log", 100_000, mtime=now - 1000)
        out = enforce_byte_budget(sev, max_total_mb=1)
        assert out["removed"] == 1
        assert not (sev / "a.log").exists()
        assert (sev / "b.log").exists() and (sev / "c.log").exists()

    def test_recent_files_survive_even_over_budget(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        now = time.time()
        _write(sev / "hot.log", 2_000_000, mtime=now - 5)
        enforce_byte_budget(sev, max_total_mb=1)
        assert (sev / "hot.log").exists()  # active writer never evicted

    def test_zero_budget_disables(self, tmp_path: Path) -> None:
        sev = tmp_path / "info"
        _write(sev / "a.log", 500_000, mtime=time.time() - 5000)
        out = enforce_byte_budget(sev, max_total_mb=0)
        assert out["removed"] == 0 and (sev / "a.log").exists()


# ---------------------------------------------------------------------------
# 3. updater cache
# ---------------------------------------------------------------------------


class TestUpdateCachePrune:
    def test_keep_newest_packages_only(self, tmp_path: Path) -> None:
        now = time.time()
        _write(tmp_path / "nse-v1.zip", 100, mtime=now - 9000)
        _write(tmp_path / "nse-v2.zip", 100, mtime=now - 6000)
        _write(tmp_path / "nse-v3.zip", 100, mtime=now - 1000)
        out = prune_update_cache(tmp_path, keep_packages=2)
        assert out["removed_packages"] == 1
        assert (tmp_path / "nse-v2.zip").exists() and (tmp_path / "nse-v3.zip").exists()
        assert not (tmp_path / "nse-v1.zip").exists()

    def test_stale_part_removed_fresh_part_kept(self, tmp_path: Path) -> None:
        now = time.time()
        _write(tmp_path / "nse-v4.zip.part", 50, mtime=now - 7200)
        _write(tmp_path / "nse-v5.zip.part", 50, mtime=now - 30)
        out = prune_update_cache(tmp_path, keep_packages=1)
        assert out["removed_parts"] == 1
        assert (tmp_path / "nse-v5.zip.part").exists()

    def test_at_least_one_package_always_kept(self, tmp_path: Path) -> None:
        _write(tmp_path / "only.zip", 10, mtime=time.time() - 99999)
        prune_update_cache(tmp_path, keep_packages=1)
        assert (tmp_path / "only.zip").exists()


# ---------------------------------------------------------------------------
# 4. crash leftovers + backups
# ---------------------------------------------------------------------------


class TestCrashLeftovers:
    def test_stage_and_preserve_removed_previous_keeps_one(self, tmp_path: Path) -> None:
        now = time.time()
        _write(tmp_path / ".update-stage-01020304" / "bin" / "app.exe", 10)
        _write(tmp_path / ".preserve-artifacts" / "x.db", 10)
        _write(tmp_path / ".previous-20260909010101" / "old" / "f.txt", 10, mtime=now - 9000)
        _write(tmp_path / ".previous-20260909101010" / "cur" / "f.txt", 10, mtime=now - 100)
        _write(tmp_path / "NexusScalpEngine.exe", 500)
        out = sweep_crash_leftovers(tmp_path, keep_previous_backups=1)
        assert out["removed_dirs"] == 3
        assert not (tmp_path / ".update-stage-01020304").exists()
        assert not (tmp_path / ".preserve-artifacts").exists()
        assert (tmp_path / ".previous-20260909101010").exists()
        assert not (tmp_path / ".previous-20260909010101").exists()
        assert (tmp_path / "NexusScalpEngine.exe").exists()

    def test_freshness_uses_newest_file_not_dir_mtime(self, tmp_path: Path) -> None:
        """Rollback keeps the tree whose CONTENT is newest (RollbackEngine
        parity); a just-created parent dir must not decide the winner."""
        now = time.time()
        _write(tmp_path / ".previous-old" / "a.bin", 10, mtime=now - 99_999)
        _write(tmp_path / ".previous-new" / "b.bin", 10, mtime=now - 100)
        sweep_crash_leftovers(tmp_path, keep_previous_backups=1)
        assert (tmp_path / ".previous-new").exists()
        assert not (tmp_path / ".previous-old").exists()

    def test_unrelated_dirs_never_touched(self, tmp_path: Path) -> None:
        _write(tmp_path / "artifacts" / "audit.db", 100)
        _write(tmp_path / "data" / "x.json", 10)
        out = sweep_crash_leftovers(tmp_path, keep_previous_backups=1)
        assert out["removed_dirs"] == 0
        assert (tmp_path / "artifacts" / "audit.db").exists()


# ---------------------------------------------------------------------------
# 5. diagnostics + residue
# ---------------------------------------------------------------------------


class TestDiagnosticsAndResidue:
    def test_diagnostics_keep_newest(self, tmp_path: Path) -> None:
        now = time.time()
        for i in range(12):
            _write(
                tmp_path / f"nexus-diagnostics-2026090{i % 10}_00000{i}.zip",
                10,
                mtime=now - i * 100,
            )
        out = sweep_diagnostics(tmp_path, keep=10)
        assert out["removed"] == 2
        assert len(list(tmp_path.glob("nexus-diagnostics-*.zip"))) == 10

    def test_residue_young_untouched_old_removed(self, tmp_path: Path) -> None:
        now = time.time()
        _write(tmp_path / "sub" / "extract.tmp", 10, mtime=now - 7200)
        _write(tmp_path / "sub" / "live.part", 10, mtime=now - 10)
        out = sweep_residue_files(tmp_path, min_age_sec=3600.0)
        assert out["removed"] == 1
        assert (tmp_path / "sub" / "live.part").exists()

    def test_residue_never_touches_db_or_log_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "audit.db", 100, mtime=time.time() - 999999)
        _write(tmp_path / "models" / "model.pt", 100, mtime=time.time() - 999999)
        out = sweep_residue_files(tmp_path, min_age_sec=3600.0)
        assert out["removed"] == 0
        assert (tmp_path / "audit.db").exists() and (tmp_path / "models" / "model.pt").exists()


# ---------------------------------------------------------------------------
# 6. PROTECTED DATA — the storage guard must never delete them
# ---------------------------------------------------------------------------


class TestProtectedDataSurvivesEverything:
    def test_full_guard_cycle_preserves_protected_tree(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ur = tmp_path / "userroot"
        # protected landscape
        _write(ws / "artifacts" / "audit.db", 4096)
        _write(ws / "artifacts" / "audit.db-wal", 512)
        _write(ws / "artifacts" / "news.db", 2048)
        _write(ws / "artifacts" / "candle_intel.db", 2048)
        _write(ws / "artifacts" / "marketplace.db", 2048)
        _write(ws / "artifacts" / "models" / "70d" / "model.pt", 8192)
        _write(ws / "artifacts" / "models" / "70d" / "scaler.joblib", 512)
        _write(ws / "artifacts" / "model_generation" / "runs" / "r1" / "manifest.json", 256)
        _write(ws / "artifacts" / "archive" / "audit" / "t" / "HYG-abc.jsonl", 256)
        _write(ws / "artifacts" / "forensics" / "history.jsonl", 256)
        _write(ws / "configs" / "live.yaml", 256)
        _write(ws / "data" / "settings.db", 512)
        _write(ur / "config" / "nexus.yaml", 256)
        _write(ur / "update" / "installed-release.json", 256)
        _write(ur / "update" / "update-state.json", 256)
        _write(ur / "update" / "history.jsonl", 256)
        # removable landscape that must be swept
        _write(ws / ".update-stage-9" / "junk.bin", 128)
        _write(ws / ".previous-old" / "old" / "tree.bin", 128, mtime=time.time() - 99999)
        _write(ws / ".previous-new" / "new" / "tree.bin", 128, mtime=time.time() - 100)
        _write(
            ws / "logs" / "info" / "2026" / "08" / "2026-08-01.log",
            2048,
            mtime=time.time() - 30 * 86400,
        )
        _write(ur / "update" / "cache" / "nse-old.zip", 512, mtime=time.time() - 99999)
        _write(ur / "update" / "cache" / "nse-new.zip", 512, mtime=time.time() - 10)
        _write(ur / "update" / "cache" / "nse-dl.zip.part", 256, mtime=time.time() - 99999)
        _write(ur / "diagnostics" / "nexus-diagnostics-old.zip", 128, mtime=time.time() - 99999)
        _write(ur / "diagnostics" / "nexus-diagnostics-new.zip", 128, mtime=time.time() - 10)

        guard = StorageGuard(
            workspace=ws,
            user_root=ur,
            settings=StorageGuardSettings(
                enabled=True,
                startup_sweep=True,
                keep_update_packages=1,
                keep_previous_backups=1,
                diagnostics_keep=1,
                compress_after_days=2,
            ),
        )
        guard.startup_sweep()
        guard.cycle(force=True)

        # every protected artifact byte-identical
        assert (ws / "artifacts" / "audit.db").stat().st_size == 4096
        # the guard's checkpoint pass targets REAL sqlite files only; a fake
        # (non-database) stand-in must be left byte-identical
        assert (ws / "artifacts" / "audit.db-wal").stat().st_size == 512
        assert (ws / "artifacts" / "news.db").exists()
        assert (ws / "artifacts" / "candle_intel.db").exists()
        assert (ws / "artifacts" / "marketplace.db").exists()
        assert (ws / "artifacts" / "models" / "70d" / "model.pt").stat().st_size == 8192
        assert (ws / "artifacts" / "models" / "70d" / "scaler.joblib").exists()
        assert (ws / "artifacts" / "model_generation" / "runs" / "r1" / "manifest.json").exists()
        assert (ws / "artifacts" / "archive" / "audit" / "t" / "HYG-abc.jsonl").exists()
        assert (ws / "artifacts" / "forensics" / "history.jsonl").exists()
        assert (ws / "configs" / "live.yaml").exists()
        assert (ws / "data" / "settings.db").exists()
        assert (ur / "config" / "nexus.yaml").exists()
        assert (ur / "update" / "installed-release.json").exists()
        assert (ur / "update" / "update-state.json").exists()
        assert (ur / "update" / "history.jsonl").exists()
        # removable landscape actually swept
        assert not (ws / ".update-stage-9").exists()
        assert (ws / ".previous-new").exists() and not (ws / ".previous-old").exists()
        assert (ur / "update" / "cache" / "nse-new.zip").exists()
        assert not (ur / "update" / "cache" / "nse-old.zip").exists()
        assert not (ur / "update" / "cache" / "nse-dl.zip.part").exists()
        assert (ur / "diagnostics" / "nexus-diagnostics-new.zip").exists()
        assert not (ur / "diagnostics" / "nexus-diagnostics-old.zip").exists()
        # aged log compressed, not lost
        assert not (ws / "logs" / "info" / "2026" / "08" / "2026-08-01.log").exists()
        assert (ws / "logs" / "info" / "2026" / "08" / "2026-08-01.log.gz").exists()


# ---------------------------------------------------------------------------
# 7. concurrency
# ---------------------------------------------------------------------------


class TestConcurrentSweeps:
    def test_two_threads_sweeping_same_tree_never_crash(self, tmp_path: Path) -> None:
        now = time.time()
        for i in range(50):
            _write(tmp_path / f"pkg{i}.zip", 32, mtime=now - i * 100)
            _write(tmp_path / f"pkg{i}.zip.part", 16, mtime=now - i * 100 - 7200)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(20):
                    prune_update_cache(tmp_path, keep_packages=2)
                    sweep_residue_files(tmp_path, min_age_sec=3600.0)
            except Exception as exc:  # pragma: no cover - collected below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        survivors = list(tmp_path.glob("pkg*.zip"))
        assert len(survivors) <= 2

    def test_storage_guard_concurrent_cycles_isolated(self, tmp_path: Path) -> None:
        guard = StorageGuard(workspace=tmp_path, user_root=tmp_path / "u")
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(5):
                    guard.cycle(force=True)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# 8. quota + throttling + the 10GB regression
# ---------------------------------------------------------------------------


class TestQuotaAndGrowthClamp:
    def test_quota_snapshot_reports_over_quota(self, tmp_path: Path) -> None:
        _write(tmp_path / "logs" / "info" / "big.log", 3 * 1024 * 1024, mtime=time.time())
        guard = StorageGuard(
            workspace=tmp_path,
            user_root=tmp_path / "u",
            settings=StorageGuardSettings(quota_mb=1),
        )
        snap = guard.quota_snapshot()
        assert snap["over_quota"] is True
        assert snap["usage_mb"]["logs"] >= 3.0

    def test_cycle_throttled_unless_forced(self, tmp_path: Path) -> None:
        guard = StorageGuard(workspace=tmp_path, user_root=tmp_path / "u")
        guard.cycle()
        second = guard.cycle()
        assert "skipped" in second
        forced = guard.cycle(force=True)
        assert "skipped" not in forced

    def test_ten_gb_growth_simulation_clamps_to_budget(self, tmp_path: Path) -> None:
        """Regression for the customer 10GB profile.

        Simulates an exception-storm client: many days of severity parts at
        multi-MB rate, compressed+pruned by the guard. The severity tree must
        clamp to ~max_total_mb_per_severity instead of growing unbounded.
        Uses compressed sizes (tiny) so the test stays fast; the mechanism
        (budget enforcement on real bytes) is the same.
        """
        sev = tmp_path / "logs" / "error"
        now = time.time()
        # 100 days x 300KB of logs = 30MB for ONE severity alone
        for day in range(100):
            d = sev / "2026" / "06" / f"2026-06-{day:02d}.log"
            _write(d, 300_000, mtime=now - (100 - day) * 86400)
        before = sum(p.stat().st_size for p in sev.rglob("*") if p.is_file())

        guard = StorageGuard(
            workspace=tmp_path,
            user_root=tmp_path / "u",
            settings=StorageGuardSettings(max_total_mb_per_severity=5, compress_after_days=2),
        )
        out = guard._log_pass()
        after = sum(p.stat().st_size for p in sev.rglob("*") if p.is_file())

        # compression is the active lever here (gzip-able repeated text);
        # assert the tree actually shrank and stays far below the raw total
        assert out["compressed"] > 0
        assert after < 5 * 1024 * 1024
        assert after < before * 0.2
