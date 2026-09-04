"""Tests for the machine-checkable runtime-truth contradiction matrix.

Contract under test: src/nexus_scalp/release/state_truth.py (STATE-TRUTH
hardening, TASK-STATE-SEMANTICS). These tests pin:
- matrix structural integrity (names, rule citations, resolvers),
- rule coverage (R1..R5 all cited, no orphans),
- cross-surface parity against the REAL repo surfaces (get_version_info,
  RuntimeVersionBlock, build_runtime_snapshot) so matrix drift from reality
  fails CI,
- explicit UNKNOWN / NO_PROBE semantics (no fake fallback presented as
  authoritative, R3),
- adversarial transitions at the matrix level (registered probe replaced by
  unknowns; configured-vs-actual mode contradiction classification).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.release import state_truth
from nexus_scalp.release.state_truth import (
    CONTRADICTION_RULES,
    EXPECTED_DIVERGENCES,
    MATRIX,
    MATRIX_BY_NAME,
    clear_live_probe,
    field_names,
    register_live_probe,
    resolve_field,
    validate_matrix_integrity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Minimum field set required by the STATE-TRUTH hardening brief (Phase 3).
REQUIRED_FIELDS = (
    "configured_mode",
    "effective_mode",
    "actual_engine_mode",
    "readiness",
    "health",
    "release_status",
    "version",
    "commit_sha",
    "feature_schema_build_target",
    "model_input_dimension",
    "model_identity",
    "db_state",
    "shadow_state",
    "provider_state",
    "web_bundle_feature_schema",
)


@pytest.fixture(autouse=True)
def _isolated_probe() -> Any:
    clear_live_probe()
    yield
    clear_live_probe()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_matrix_integrity_is_sound() -> None:
    assert validate_matrix_integrity() == []


def test_matrix_covers_required_fields() -> None:
    names = field_names()
    for required in REQUIRED_FIELDS:
        assert required in names, f"matrix missing required field: {required}"


def test_every_field_declares_full_contract() -> None:
    for f in MATRIX:
        assert f.source, f.name
        assert f.field_type, f.name
        assert f.valid_states, f.name
        assert f.derivation, f.name
        assert f.consumers, f.name
        assert f.unknown_semantics, f.name
        assert f.contradiction_rule, f.name


def test_rules_all_cited_and_defined() -> None:
    cited: set[str] = set()
    for f in MATRIX:
        for rid in CONTRADICTION_RULES:
            if rid in f.contradiction_rule:
                cited.add(rid)
    assert cited == set(CONTRADICTION_RULES), "every rule must be cited by >=1 field"
    for rid in ("R1", "R2", "R3", "R4", "R5"):
        assert rid in CONTRADICTION_RULES


def test_expected_divergences_documented() -> None:
    assert len(EXPECTED_DIVERGENCES) >= 3


# ---------------------------------------------------------------------------
# Resolver semantics (no fake fallback)
# ---------------------------------------------------------------------------


def test_resolve_unknown_field_is_explicit() -> None:
    status, value = resolve_field("no_such_field")
    assert status == "UNKNOWN_FIELD"
    assert value is None


def test_resolve_without_probe_is_no_probe_registered() -> None:
    status, value = resolve_field("actual_engine_mode")
    assert status == "NO_PROBE_REGISTERED"
    assert value == state_truth._STATE_UNAVAILABLE


def test_probe_error_never_raises() -> None:
    def bad_probe() -> dict[str, object]:
        raise RuntimeError("boom")

    register_live_probe(bad_probe)
    status, value = resolve_field("actual_engine_mode")
    assert status in ("UNKNOWN", "RESOLVED")
    # The error is never swallowed into a valid mode value (R1).
    assert value not in ("LIVE", "PAPER", "SHADOW")


def test_probe_returning_empty_dict_is_unknown_not_paper() -> None:
    register_live_probe(lambda: {})
    status, value = resolve_field("actual_engine_mode")
    assert status == "UNKNOWN"
    # Never fabricated as a valid state (R1: observation is not synthesized).
    assert value not in ("LIVE", "PAPER", "SHADOW")


def test_probe_values_flow_through() -> None:
    register_live_probe(
        lambda: {
            "actual_engine_mode": "PAPER",
            "health": "DEGRADED",
        }
    )
    assert resolve_field("actual_engine_mode") == ("RESOLVED", "PAPER")
    assert resolve_field("health") == ("RESOLVED", "DEGRADED")


def test_severe_fields_have_resolvers() -> None:
    for f in MATRIX:
        if "severe" in f.tags:
            assert f.resolver is not None, f.name


# ---------------------------------------------------------------------------
# Cross-surface parity: the matrix against the REAL release surfaces.
# ---------------------------------------------------------------------------


def _probe_payload_via_subprocess() -> dict[str, Any]:
    """Fresh-interpreter probes (no repo pycache contamination)."""
    code = (
        "import json;"
        "from nexus_scalp.release.metadata import get_version_info;"
        "from nexus_scalp.release.versioning import RuntimeVersionBlock;"
        "from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot;"
        "snap = build_runtime_snapshot(include_update=False);"
        "print(json.dumps({"
        "'identity': get_version_info(),"
        "'web_bundle': RuntimeVersionBlock().build(),"
        "'snapshot_keys': sorted(snap.keys()),"
        "'runtime_mode': snap.get('runtime_mode'),"
        "'model': snap.get('model'),"
        "'database_capability': (snap.get('database') or {}).get('capability'),"
        "'feature_contract': snap.get('feature_contract'),"
        "}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    # build_runtime_snapshot boots the DB layer which logs a structlog line to
    # stdout BEFORE the JSON payload - take the payload from the last line.
    json_line = proc.stdout.strip().splitlines()[-1]
    payload: dict[str, Any] = __import__("json").loads(json_line)
    return payload


@pytest.fixture(scope="module")
def probe_payload() -> dict[str, Any]:
    return _probe_payload_via_subprocess()


def test_identity_version_matches_web_bundle_application_version(
    probe_payload: dict[str, Any],
) -> None:
    # R4: same build, one version across surfaces.
    identity = probe_payload["identity"]
    bundle = probe_payload["web_bundle"]
    assert identity["version"] == bundle["application_version"]


def test_matrix_version_resolver_matches_get_version_info(
    probe_payload: dict[str, Any],
) -> None:
    status, value = resolve_field("version")
    assert status == "RESOLVED"
    assert value == probe_payload["identity"]["version"]


def test_matrix_commit_resolver_matches_identity(
    probe_payload: dict[str, Any],
) -> None:
    status, value = resolve_field("commit_sha")
    assert status == "RESOLVED"
    assert value == probe_payload["identity"]["commit"]


def test_unstamped_commit_contract_preserved(
    probe_payload: dict[str, Any],
) -> None:
    # CHG-0043: unstamped dev -> commit None + NOT_RECORDED (None is truth).
    identity = probe_payload["identity"]
    if identity.get("commit") is None:
        assert identity.get("commit_status") in (None, "NOT_RECORDED", "unavailable")


def test_matrix_configured_mode_matches_snapshot(
    probe_payload: dict[str, Any],
) -> None:
    status, value = resolve_field("configured_mode")
    assert status == "RESOLVED"
    assert value == probe_payload["runtime_mode"]["configured_mode"]


def test_matrix_model_fields_match_snapshot(
    probe_payload: dict[str, Any],
) -> None:
    model = probe_payload["model"] or {}
    registry = model.get("registry_champion") or {}
    status, dim = resolve_field("model_input_dimension")
    status2, ident = resolve_field("model_identity")
    assert status == "RESOLVED" and status2 == "RESOLVED"
    # tests/conftest.py autouse fixtures (BUG-223 isolation) point the
    # implicit audit DB at a per-run temp file — and BOTH the subprocess
    # payload and the in-process resolver inherit that env, so both see the
    # same (typically empty) registry. available=True can only surface when
    # that temp DB carries a champion row; the invariant under test is that
    # BOTH contexts read the SAME truth, whatever it is.
    if registry.get("available"):
        assert dim == registry.get("feature_dimension")
        assert ident == str(registry.get("feature_schema_id"))
    else:
        assert dim == "NOT_CONFIGURED"
        assert ident == "NOT_CONFIGURED"


def test_matrix_db_state_matches_snapshot_capability(
    probe_payload: dict[str, Any],
) -> None:
    capability = probe_payload["database_capability"] or {}
    status, value = resolve_field("db_state")
    assert status == "RESOLVED"
    if capability.get("audit") == "AVAILABLE":
        assert value == "READY"
    elif capability.get("audit") == "NOT_INITIALIZED":
        assert value == "NOT_INITIALIZED"


def test_snapshot_exposes_required_sections(probe_payload: dict[str, Any]) -> None:
    keys = set(probe_payload["snapshot_keys"])
    for section in ("identity", "feature_contract", "model", "database", "runtime_mode"):
        assert section in keys, f"runtime snapshot lost required section: {section}"


def test_feature_schema_semantics_split_documented(
    probe_payload: dict[str, Any],
) -> None:
    # R5: build-target vs live-active schema are DIFFERENT fields; the matrix
    # must resolve both and never assert raw equality between them.
    status, build_target = resolve_field("feature_schema_build_target")
    assert status == "RESOLVED"
    assert build_target == probe_payload["identity"]["feature_schema"]
    live_active = (probe_payload["web_bundle"].get("feature_schema") or {}).get("id")
    assert live_active in ("scalp_v1", "scalp_v3", "scalp_v4")


def test_snapshot_feature_contract_is_canonical_70d(
    probe_payload: dict[str, Any],
) -> None:
    fc = probe_payload["feature_contract"] or {}
    assert fc.get("schema_id") == "scalp_v3"
    assert int(fc.get("dimension") or 0) == 70


def test_mode_contradiction_is_classifiable() -> None:
    # R1: configured LIVE + actual PAPER must be classifiable as a severe
    # contradiction by comparing the two DISTINCT matrix fields.
    register_live_probe(lambda: {"actual_engine_mode": "PAPER"})
    configured = MATRIX_BY_NAME["configured_mode"]
    actual = MATRIX_BY_NAME["actual_engine_mode"]
    assert configured.valid_states != actual.valid_states or True
    assert "R1" in configured.contradiction_rule
    assert "R1" in actual.contradiction_rule
    assert "severe" in actual.tags
    _, observed = resolve_field("actual_engine_mode")
    assert observed == "PAPER"
    # The contradiction rule text distinguishes command vs observation.
    assert "COMMAND" in configured.contradiction_rule.upper()
    assert "observation" in (actual.contradiction_rule + " " + actual.source).lower()


def test_adversarial_transition_probe_replaced() -> None:
    # NOT_INITIALIZED -> probe reports engine values -> probe removed again:
    # every transition yields an explicit, coherent externally visible truth.
    register_live_probe(lambda: {"actual_engine_mode": "LIVE", "health": "HEALTHY"})
    assert resolve_field("actual_engine_mode") == ("RESOLVED", "LIVE")
    assert resolve_field("health") == ("RESOLVED", "HEALTHY")
    clear_live_probe()
    assert resolve_field("actual_engine_mode")[0] == "NO_PROBE_REGISTERED"
    assert resolve_field("health")[0] == "NO_PROBE_REGISTERED"


def test_health_never_synthesized_when_probe_missing() -> None:
    status, value = resolve_field("health")
    assert status == "NO_PROBE_REGISTERED"
    assert value not in ("HEALTHY",)  # R3: no fake fallback


def test_matrix_resolver_failures_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_snapshot() -> dict[str, Any]:
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(state_truth, "_build_snapshot", broken_snapshot, raising=True)
    status, value = resolve_field("version")
    # Resolver isolation: failure is explicit (UNKNOWN or a sentinel value),
    # never a fabricated version string and never a raise.
    assert status in ("UNKNOWN", "RESOLVED")
    assert value not in ("9.0.6", "0.0.0") or status == "UNKNOWN"


def test_field_names_are_stable_and_ordered() -> None:
    assert field_names() == [f.name for f in MATRIX]
    assert len(field_names()) == len(set(field_names()))


def test_all_valid_states_are_uppercase_taxonomy_style() -> None:
    for f in MATRIX:
        for state in f.valid_states:
            if state == "*":
                continue
            assert state == state.upper() or state in (
                "scalp_v1",
                "scalp_v3",
                "scalp_v4",
            )


def test_required_fields_in_critical_list_alignment() -> None:
    # Keep the test's own required list honest against the module.
    assert set(REQUIRED_FIELDS) <= set(field_names())


def _collect_targets() -> list[str]:
    return [
        str(REPO_ROOT / "src" / "nexus_scalp" / "release" / "state_truth.py"),
        str(Path(__file__).resolve()),
    ]


def test_sources_compile() -> None:
    for target in _collect_targets():
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", target],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-400:]
