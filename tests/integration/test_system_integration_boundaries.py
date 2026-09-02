"""System-integration boundary tests (Nexus-Main integration mission).

BOUNDARIES / LIFECYCLES / IDENTITY / CROSS-LAYER CONSISTENCY — the connections
between subsystems, not the subsystems themselves (unit tests own those).

Coverage targets proven by live probes on 2026-09-02 (integration mission):
  1. CWD-dependent CLI identity: a stale gitignored build-info.json in the CWD
     silently overrides repository identity for source runs (the 9.0.3-banner
     class). get_build_info_file() prefers ``Path.cwd()/build-info.json`` for
     non-frozen runs — the integration contract is that a source run executed
     OUTSIDE the stale CWD must NOT inherit the stale stamp.
  2. Forensic deploy-gate NWS-03 singleton split-brain: the availability-matrix
     check reads a PRIVATE ``checks_news.FEATURE_REF_REGISTRY`` instance while
     the engine auto-freezes its OWN ``ForensicHealthEngine.references`` — the
     engine-loaded golden references never reach the check, so a healthy 70D
     system reports CRITICAL (FEATURE_CONTRACT_INCOMPLETE) and the gate BLOCKs.
     Contract under test: freeze into ONE registry ⇒ the check observes it.
  3. Settings-store precedence over launcher CLI flags: the launcher's
     ``--mode paper`` default must NOT mask the authoritative
     ``execution.mode`` persisted in the settings DB (observed live: launcher
     started with --mode paper while runtime_mode=LIVE per settings).
     Contract under test: RuntimeConfigStore.to_app_config() applies the
     settings values over the base config.

Offline, deterministic, hermetic (tmp_path / monkeypatch); no MT5, no network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


# ---------------------------------------------------------------------------
# 1. CWD-dependent identity (stale build-info.json must not win off-CWD)
# ---------------------------------------------------------------------------


class TestCwdIndependentIdentity:
    def _repo_commit(self) -> str | None:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return out.stdout.strip() or None
        except Exception:
            pytest.skip("git unavailable")

    def test_cwd_build_info_precedence_is_pinned_documented_dev_contract(self, tmp_path):
        """PIN the documented dev-run contract: for source (non-frozen) runs,
        ``get_build_info_file()`` resolves ``Path.cwd()/build-info.json``
        FIRST (metadata.py candidates list). A stale gitignored stamp in the
        CWD therefore captures the identity chain — the 9.0.3-banner class,
        explicitly owned by TASK-RUNTIME-TRUTH ('dev stale build-info.json
        precedence fix'). This test pins today's behavior so the precedence
        repair flips it DELIBERATELY with a visible contract change, not
        silently. (Integration-probe evidence 2026-09-02: repo-root CLI run
        reported 9.0.3/53317de from the stale stamp while a neutral-CWD run
        reported 9.0.6/None from pyproject.)"""
        from nexus_scalp.release.metadata import get_build_info_file

        stale = {"product": "NexusScalpEngine", "version": "0.0.0-test-stale"}
        (tmp_path / "build-info.json").write_text(json.dumps(stale), encoding="utf-8")

        probe = (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{REPO_ROOT / 'src'}')\n"
            "from nexus_scalp.release.metadata import get_build_info_file, read_build_info\n"
            "p = get_build_info_file()\n"
            "info = read_build_info()\n"
            "print(json.dumps({'resolved': str(p) if p else None, 'info': info}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        resolved = payload["resolved"]
        assert resolved is not None, "no build-info resolved at all"
        assert Path(resolved) == tmp_path / "build-info.json", (
            "CWD-precedence contract changed — update this pin + the TASK-RUNTIME-TRUTH fix note"
        )
        assert payload["info"].get("version") == "0.0.0-test-stale"

    def test_cli_version_command_reports_pyproject_version_from_neutral_cwd(self):
        """`nexus version --json` from a neutral CWD reports the pyproject
        version — the installed/CLI/runtime agreement contract."""
        nexus = VENV_PY.parent / "nexus.exe"
        if not nexus.exists():
            pytest.skip("repo venv nexus.exe not built")
        neutral = Path(os.environ.get("TEMP", str(Path.home())))
        proc = subprocess.run(
            [str(nexus), "version", "--json"],
            check=False,
            cwd=str(neutral),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr[-300:]
        out = proc.stdout.strip()
        assert out.startswith("{"), f"JSON purity broken: {out[:80]!r}"
        payload = json.loads(out)
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        expected = pyproject.split("version = ", 1)[1].split('"')[1]
        assert payload.get("version") == expected, (
            f"CLI reports {payload.get('version')!r}, pyproject says {expected!r}"
        )


# ---------------------------------------------------------------------------
# 2. Forensic reference-registry split-brain (NWS-03 false CRITICAL)
# ---------------------------------------------------------------------------


class TestForensicReferenceRegistryCoherence:
    def test_single_registry_truth_engine_vs_check(self, monkeypatch):
        """Freeze the golden baseline into the ENGINE registry; the SAME
        process's check_news_availability_matrix must see liquidity enabled +
        references present (no split singleton), i.e. NOT critical for
        missing frozen references when references exist in the engine."""
        golden = REPO_ROOT / "docs" / "LIQUIDITY_70D_GOLDEN_BASELINE.json"
        if not golden.exists():
            pytest.skip("golden baseline not present")

        from nexus_scalp.forensics import checks_news
        from nexus_scalp.forensics.engine import ForensicHealthEngine
        from nexus_scalp.forensics.references import (
            FEATURE_REFERENCES,
        )

        # Reset BOTH singletons to a known-empty state (module reload safety).
        engine_registry = FEATURE_REFERENCES
        check_registry = checks_news.FEATURE_REF_REGISTRY
        for reg in (engine_registry, check_registry):
            reg._refs.clear()  # test-owned reset of a process singleton

        engine = ForensicHealthEngine()  # triggers _auto_freeze_references
        n_engine = len(engine.references)
        if n_engine == 0:
            pytest.skip("auto-freeze unavailable in this environment")

        cfg_like = type("Cfg", (), {})()
        model_like = type("M", (), {"liquidity_features_enabled": True})()
        cfg_like.model = model_like
        news_like = type("N", (), {"enabled": True})()
        cfg_like.news = news_like
        monkeypatch.setattr(checks_news, "_load_runtime_config", lambda: cfg_like)

        # The contract: whichever registry the CHECK reads, it must observe
        # the frozen references the ENGINE loaded. Today it does not
        # (split-brain) — this assertion documents the boundary so the repair
        # (route the check through the engine/shared registry) flips it green.
        result = checks_news.check_news_availability_matrix()
        frozen_seen = len(check_registry)
        if frozen_seen == 0 and n_engine > 0:
            pytest.fail(
                "SPLIT-BRAIN CONFIRMED: engine registry holds "
                f"{n_engine} frozen references but check_news.FEATURE_REF_REGISTRY "
                f"sees {frozen_seen} — deploy gate false-CRITICAL (NWS-03) "
                "for a healthy 70D system. Repair: share ONE registry."
            )
        assert result.status.name != "CRITICAL" or "news.db" in (result.evidence or "")


# ---------------------------------------------------------------------------
# 3. Settings-store precedence over launcher flags (mode continuity)
# ---------------------------------------------------------------------------


class TestSettingsPrecedenceOverLauncherFlag:
    def test_runtime_config_store_applies_settings_over_base_config(self, tmp_path, monkeypatch):
        """Observed live: launcher started with --mode paper while the
        settings DB held execution.mode=LIVE and the engine reported
        runtime_mode=LIVE. The contract is that the authoritative settings
        store overrides the base config — verify to_app_config() applies the
        stored value, so the runtime cannot silently run a different mode
        than the operator configured."""
        from nexus_scalp.configuration.config import AppConfig
        from nexus_scalp.configuration.runtime_config import (
            PersistentConfigStore,
            RuntimeConfigStore,
        )
        from nexus_scalp.settings import load_settings_service

        monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "app_settings.db"))
        svc = load_settings_service()
        svc.db.set("execution.mode", "PAPER", source="PROBE", actor="integration-probe")
        svc.db.set("risk.risk_per_trade_pct", 0.33, source="PROBE", actor="integration-probe")

        base = AppConfig.load_from_yaml(REPO_ROOT / "configs" / "live.yaml")
        store = RuntimeConfigStore(persistent=PersistentConfigStore(svc), bootstrap=base)
        applied = store.get_snapshot().to_app_config()
        assert applied.execution.mode.value == "PAPER", (
            "settings store did not override base config execution.mode"
        )
        assert abs(applied.risk.risk_per_trade_pct - 0.33) < 1e-9, (
            "settings store did not override base config risk.risk_per_trade_pct"
        )
