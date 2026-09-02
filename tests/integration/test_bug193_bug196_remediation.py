"""BUG-193 anti-greenwashing + determinism + process-isolation tests.

Adds to the existing remediation net (runs as a standalone pytest file so it
can be wired into the critical suite selectively):

  CASE A: healthy references -> check NOT critical (singletons share truth)
  CASE B: real critical (a frozen reference registers an out-of-contract
          value is NOT the failure mode here; the honest critical injection
          is: liquidity enabled + registry EMPTIED (references truly absent)
          -> check MUST be CRITICAL again). This proves the alias fix did not
          disable the check - it fires exactly when references are missing.
  DETERMINISM: 25 repeated evaluations -> identical status, no registry
          growth (no accumulation/duplication).
  PROCESS ISOLATION: a fresh subprocess must see the SAME shared registry
          state (no reliance on in-memory state from a parent process).

Offline, hermetic, no MT5.
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


def _patched_runtime_config(monkeypatch, liquidity: bool = True):
    from nexus_scalp.forensics import checks_news

    cfg_like = type("Cfg", (), {})()
    model_like = type("M", (), {"liquidity_features_enabled": liquidity})()
    cfg_like.model = model_like
    cfg_like.news = type("N", (), {"enabled": True})()
    monkeypatch.setattr(checks_news, "_load_runtime_config", lambda: cfg_like)


class TestBug193SharedRegistryTruth:
    def test_case_a_healthy_references_not_critical(self, monkeypatch):
        """Healthy tree: engine-registered frozen references are visible to
        the availability-matrix check (the BUG-193 split is closed)."""
        from nexus_scalp.forensics import checks_news
        from nexus_scalp.forensics.engine import ForensicHealthEngine
        from nexus_scalp.forensics.references import FEATURE_REFERENCES

        engine = ForensicHealthEngine()  # freeze-once owner
        n = len(engine.references)
        if n == 0:
            pytest.skip("golden baseline auto-freeze unavailable")
        # ONE registry: the check module reads the same object the engine froze.
        assert checks_news.FEATURE_REF_REGISTRY is FEATURE_REFERENCES
        _patched_runtime_config(monkeypatch, liquidity=True)
        result = checks_news.check_news_availability_matrix()
        assert result.status.name != "CRITICAL", (
            f"healthy system still critical: {result.evidence}"
        )

    def test_case_b_missing_references_real_critical(self, monkeypatch):
        """Real critical survives: with liquidity enabled and the shared
        registry genuinely EMPTY (references truly absent), the check MUST
        stay CRITICAL - the fix removed only the false critical."""
        from nexus_scalp.forensics import checks_news
        from nexus_scalp.forensics.references import FEATURE_REFERENCES

        saved = dict(FEATURE_REFERENCES._refs)
        try:
            FEATURE_REFERENCES._refs.clear()
            _patched_runtime_config(monkeypatch, liquidity=True)
            result = checks_news.check_news_availability_matrix()
            assert result.status.name == "CRITICAL", (
                "missing frozen references must stay CRITICAL (fail-closed)"
            )
            assert "no frozen reference" in (result.evidence or "")
        finally:
            FEATURE_REFERENCES._refs.clear()
            FEATURE_REFERENCES._refs.update(saved)

    def test_repeated_evaluation_deterministic_no_accumulation(self, monkeypatch):
        """25 repeated evaluations: same verdict, registry length constant
        (no state accumulation / duplication)."""
        from nexus_scalp.forensics import checks_news
        from nexus_scalp.forensics.engine import ForensicHealthEngine
        from nexus_scalp.forensics.references import FEATURE_REFERENCES

        from nexus_scalp.forensics.engine import ForensicHealthEngine

        ForensicHealthEngine()  # freeze-once owner initializes the registry
        _patched_runtime_config(monkeypatch, liquidity=True)
        n0 = len(FEATURE_REFERENCES)
        if n0 == 0:
            pytest.skip("golden baseline auto-freeze unavailable")
        statuses = set()
        for _ in range(25):
            r = checks_news.check_news_availability_matrix()
            statuses.add(r.status.name)
            assert len(FEATURE_REFERENCES) == n0, "registry grew between runs"
        assert len(statuses) == 1, f"non-deterministic verdicts: {statuses}"

    def test_fresh_process_sees_shared_registry(self):
        """Process isolation: a brand-new interpreter resolves the SAME
        singleton identity (no in-memory state carried over)."""
        code = (
            "import json\n"
            "from nexus_scalp.forensics import checks_news\n"
            "from nexus_scalp.forensics import checks_features\n"
            "from nexus_scalp.forensics.references import FEATURE_REFERENCES\n"
            "from nexus_scalp.forensics.engine import ForensicHealthEngine\n"
            "e = ForensicHealthEngine()\n"
            "print(json.dumps({\n"
            "    'news_is_canonical': checks_news.FEATURE_REF_REGISTRY is FEATURE_REFERENCES,\n"
            "    'features_is_canonical': checks_features.FEATURE_REF_REGISTRY is FEATURE_REFERENCES,\n"
            "    'engine_len': len(e.references),\n"
            "    'registry_len': len(FEATURE_REFERENCES),\n"
            "}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=240,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["news_is_canonical"] is True
        assert payload["features_is_canonical"] is True
        assert payload["registry_len"] == payload["engine_len"]


class TestBug196ForeignCwdSafety:
    def _nexus(self) -> Path:
        nexus = VENV_PY.parent / "nexus.exe"
        if not nexus.exists():
            pytest.skip("repo venv nexus.exe not built")
        return nexus

    def test_version_and_doctor_json_from_foreign_cwd(self, tmp_path):
        """json.loads(stdout) must pass and NO artifacts/ may be created in
        the foreign CWD (BUG-196 S1+S2: read-only probes never materialize
        DBs)."""
        nexus = self._nexus()
        for cmd in ("version", "doctor"):
            proc = subprocess.run(
                [str(nexus), cmd, "--json"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(tmp_path),
                check=False,
            )
            assert proc.returncode == 0, proc.stderr[-300:]
            payload = json.loads(proc.stdout)  # literal parse: purity contract
            assert isinstance(payload, dict)
            if cmd == "doctor":
                assert "overall" in payload
            else:
                assert payload.get("version")
        created = sorted(p.name for p in (tmp_path / "artifacts").glob("*.db")) if (
            tmp_path / "artifacts"
        ).exists() else []
        assert created == [], f"foreign CWD polluted with {created}"

    def test_repeated_json_runs_no_drift(self, tmp_path):
        """3x doctor --json from the same foreign CWD: valid JSON every time,
        no DB creation, no output drift."""
        nexus = self._nexus()
        outs = []
        for _ in range(3):
            proc = subprocess.run(
                [str(nexus), "doctor", "--json"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(tmp_path),
                check=False,
            )
            assert proc.returncode == 0
            outs.append(json.loads(proc.stdout)["overall"])
        assert outs == outs[:1] * 3
        assert not (tmp_path / "artifacts").exists() or not any(
            (tmp_path / "artifacts").glob("*.db")
        )

    def test_human_mode_unbroken(self):
        """Human-readable CLI still renders (no JSON purity fix may break it)."""
        nexus = self._nexus()
        proc = subprocess.run(
            [str(nexus), "version"],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == 0
        assert "9.0." in proc.stdout
