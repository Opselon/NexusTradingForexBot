"""ML contract drift gate tests (ML-CI-002).

Pins scripts/ci/check_ml_contract_drift.py: the guard must

  1. PASS on the committed canonical contract (docs == source),
  2. FAIL when a canonical source constant changes without a docs update
     (STALE_DOC / MISSING_DOC),
  3. stay deterministic, offline, dependency-free and fast (<0.5s),
  4. not be defeated by prose (a sentence mentioning a symbol is not a value
     claim), and not be satisfied by a comment inside source.

Every assertion runs against a SANDBOXED copy of the real repo subset (docs +
the source declaration files) so the tests never mutate the working tree and
cannot pass by accident against an already-drifted main.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "ci" / "check_ml_contract_drift.py"

#: The source files the gate extracts canonical constants from. Only these are
#: copied into the sandbox (the gate reads no other source).
SOURCE_DECLARATIONS = (
    "src/nexus_scalp/features/schema.py",
    "src/nexus_scalp/features/schema_contract.py",
    "src/nexus_scalp/model_lifecycle/model_class_contract.py",
    "src/nexus_scalp/execution/order_manager.py",
)

#: The canonical contract docs the gate scans.
CONTRACT_DOCS = (
    "01_SYSTEM_CONTRACT.md",
    "02_DATA_CONTRACT.md",
    "03_MODEL_ARCHITECTURE.md",
    "05_INFERENCE_FORWARDTEST_GOVERNANCE.md",
)


def _load_gate():
    """Loads the gate module by path (no package import side effects)."""
    spec = importlib.util.spec_from_file_location("check_ml_contract_drift", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_ml_contract_drift"] = module
    spec.loader.exec_module(module)
    return module


GATE_MOD = _load_gate()


def _sandbox(tmp_path: Path) -> Path:
    """Materialises a minimal repo tree the gate can check.

    Copies the real contract docs + the real source declaration files so the
    sandbox starts GREEN; tests then mutate one knob to force drift.
    """
    root = tmp_path / "repo"
    (root / "docs" / "ml-system").mkdir(parents=True)
    for name in CONTRACT_DOCS:
        src = REPO_ROOT / "docs" / "ml-system" / name
        if src.exists():
            shutil.copy2(src, root / "docs" / "ml-system" / name)
    for rel in SOURCE_DECLARATIONS:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dst)
    return root


# ---------------------------------------------------------------------------
# 1. The committed contract is in sync (the gate cannot ship red).
# ---------------------------------------------------------------------------


def test_gate_passes_on_committed_repo() -> None:
    """The canonical docs and source must agree at HEAD."""
    rep = GATE_MOD.check_drift(REPO_ROOT)
    assert rep.ok, "ML contract drift detected on the committed tree: " + str(
        [f.public() for f in rep.findings]
    )
    assert rep.checked >= 8, f"expected >= 8 canonical contracts, got {rep.checked}"
    assert rep.docs_scanned == len(CONTRACT_DOCS)


def test_cli_exit_code_zero_on_committed_repo() -> None:
    """The CLI entry point returns 0 on the real tree."""
    rc = GATE_MOD.main(["--root", str(REPO_ROOT)])
    assert rc == 0


def test_cli_json_output_is_machine_readable(capsys) -> None:
    rc = GATE_MOD.main(["--root", str(REPO_ROOT), "--json"])
    out = capsys.readouterr().out
    import json

    payload = json.loads(out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["contracts_checked"] >= 8
    assert payload["findings"] == []
    assert payload["tooling_errors"] == []


# ---------------------------------------------------------------------------
# 2. Drift is detected (both failure classes).
# ---------------------------------------------------------------------------


def test_stale_doc_detected_when_class_count_changes(tmp_path: Path) -> None:
    """AC2: altering TRAINED_CLASS_COUNT without updating docs fails the gate."""
    root = _sandbox(tmp_path)
    contract = root / "src/nexus_scalp/model_lifecycle/model_class_contract.py"
    text = contract.read_text(encoding="utf-8")
    assert "TRAINED_CLASS_COUNT: int = 3" in text
    contract.write_text(
        text.replace("TRAINED_CLASS_COUNT: int = 3", "TRAINED_CLASS_COUNT: int = 7"),
        encoding="utf-8",
    )
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    kinds = {f.kind for f in rep.findings}
    assert "STALE_DOC" in kinds or "MISSING_DOC" in kinds
    named = [f for f in rep.findings if f.contract_key == "TRAINED_CLASS_COUNT"]
    assert named, "no finding named TRAINED_CLASS_COUNT was raised"
    assert "7" in named[0].detail or "3" in named[0].detail


def test_stale_doc_detected_when_active_schema_id_changes(tmp_path: Path) -> None:
    """AC2: altering ACTIVE_SCHEMA_ID without updating docs fails the gate."""
    root = _sandbox(tmp_path)
    schema = root / "src/nexus_scalp/features/schema.py"
    text = schema.read_text(encoding="utf-8")
    assert 'ACTIVE_SCHEMA_ID: str = "scalp_v1"' in text
    schema.write_text(
        text.replace('ACTIVE_SCHEMA_ID: str = "scalp_v1"', 'ACTIVE_SCHEMA_ID: str = "scalp_v9"'),
        encoding="utf-8",
    )
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    named = [f for f in rep.findings if f.contract_key == "ACTIVE_SCHEMA_ID"]
    assert named, "no finding named ACTIVE_SCHEMA_ID was raised"


def test_stale_doc_detected_when_live_dimension_changes(tmp_path: Path) -> None:
    """AC2: altering the live feature dimension without docs fails the gate."""
    root = _sandbox(tmp_path)
    schema = root / "src/nexus_scalp/features/schema.py"
    text = schema.read_text(encoding="utf-8")
    # The active scalp_v1 registration is the first `dimension=50` in the file.
    patched = text.replace("dimension=50,\n", "dimension=92,\n", 1)
    assert patched != text, "the active 50D declaration was not found"
    schema.write_text(patched, encoding="utf-8")
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    named = [f for f in rep.findings if f.contract_key == "FEATURE_DIMENSION"]
    assert named, "no finding named FEATURE_DIMENSION was raised"
    assert "92" in named[0].detail or "50" in named[0].detail


def test_missing_doc_detected_when_constant_vanishes_from_source(tmp_path: Path) -> None:
    """A renamed/removed canonical declaration surfaces as MISSING_DOC."""
    root = _sandbox(tmp_path)
    contract = root / "src/nexus_scalp/model_lifecycle/model_class_contract.py"
    text = contract.read_text(encoding="utf-8")
    contract.write_text(
        text.replace("TRAINED_CLASS_COUNT: int = 3", "TRAINED_CLASS_TOTAL: int = 3"),
        encoding="utf-8",
    )
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    named = [f for f in rep.findings if f.contract_key == "TRAINED_CLASS_COUNT"]
    assert named and named[0].kind == "MISSING_DOC"


def test_stale_doc_reports_new_value_and_evidence(tmp_path: Path) -> None:
    """A real STALE_DOC finding carries the new value plus a doc file:line."""
    root = _sandbox(tmp_path)
    contract = root / "src/nexus_scalp/model_lifecycle/model_class_contract.py"
    text = contract.read_text(encoding="utf-8")
    # Flip BOTH the declaration and its class-name vocabulary so a doc line
    # naming the old 3-class contract is a provable value contradiction.
    patched = text.replace("TRAINED_CLASS_COUNT: int = 3", "TRAINED_CLASS_COUNT: int = 7", 1)
    assert patched != text
    # A doc line that asserts the symbol with the OLD value must now be stale.
    docs = root / "docs" / "ml-system" / "03_MODEL_ARCHITECTURE.md"
    dtext = docs.read_text(encoding="utf-8")
    docs.write_text(
        dtext + "\n### 4.3 Class count (asserted)\nTRAINED_CLASS_COUNT: int = 3\n",
        encoding="utf-8",
    )
    contract.write_text(patched, encoding="utf-8")
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    stale = [
        f for f in rep.findings if f.kind == "STALE_DOC" and f.contract_key == "TRAINED_CLASS_COUNT"
    ]
    assert stale, "a doc asserting the old value was not flagged STALE_DOC"
    assert "7" in stale[0].detail, "the finding must name the new source value"
    assert re.search(r"\.md:\d+:", stale[0].evidence), "evidence must be a doc file:line citation"
    assert any(e.contract_key == "TRAINED_CLASS_COUNT" for e in rep.findings)


def test_missing_doc_detected_when_contract_doc_is_deleted(tmp_path: Path) -> None:
    """Removing a canonical contract doc is drift, not a free pass."""
    root = _sandbox(tmp_path)
    (root / "docs" / "ml-system" / "03_MODEL_ARCHITECTURE.md").unlink()
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    assert rep.docs_scanned == len(CONTRACT_DOCS) - 1


def test_missing_doc_detected_when_no_contract_docs_at_all(tmp_path: Path) -> None:
    """An emptied docs directory is a tooling error, never a green run."""
    root = tmp_path / "empty"
    (root / "docs" / "ml-system").mkdir(parents=True)
    rep = GATE_MOD.check_drift(root)
    assert not rep.ok
    assert rep.tooling_errors, "a docs-less repo must surface a tooling error"


# ---------------------------------------------------------------------------
# 3. The gate cannot be defeated by prose or satisfied by comments.
# ---------------------------------------------------------------------------


def test_prose_mention_is_not_a_value_claim() -> None:
    """A past-tense sentence naming the symbol is not a live value claim."""
    line = "before the sunset, TRAINED_CLASS_COUNT had been 4 for legacy artifacts"
    assert GATE_MOD._assertes_contract_value(line, "TRAINED_CLASS_COUNT") is False


def test_explicit_assignment_is_a_value_claim() -> None:
    """An asserted alternative value IS a stale-doc finding."""
    for line in (
        "TRAINED_CLASS_COUNT = 7",
        "TRAINED_CLASS_COUNT: 7",
        "TRAINED_CLASS_COUNT is 7",
        "the TRAINED_CLASS_COUNT equals 7 today",
        "TRAINED_CLASS_COUNT: int = 7",
    ):
        assert GATE_MOD._assertes_contract_value(line, "TRAINED_CLASS_COUNT") is True, line


def test_historical_prose_is_not_a_value_claim() -> None:
    """Past-tense / conditional prose must never count as a live assertion."""
    for line in (
        "Historically TRAINED_CLASS_COUNT was discussed in audits of the 3-class head.",
        "see TRAINED_CLASS_COUNT in the source for the authoritative value",
        "TRAINED_CLASS_COUNT (source of truth, not this doc)",
    ):
        assert GATE_MOD._assertes_contract_value(line, "TRAINED_CLASS_COUNT") is False, line


def test_source_comment_cannot_satisfy_extraction(tmp_path: Path) -> None:
    """A comment quoting the declaration must not be extracted as the value."""
    root = _sandbox(tmp_path)
    contract = root / "src/nexus_scalp/model_lifecycle/model_class_contract.py"
    text = contract.read_text(encoding="utf-8")
    contract.write_text(
        text.replace(
            "TRAINED_CLASS_COUNT: int = 3",
            "# TRAINED_CLASS_COUNT: int = 3 (documented)\nTRAINED_CLASS_COUNT: int = 99",
        ),
        encoding="utf-8",
    )
    value, err = GATE_MOD.extract_source_value(
        root,
        (
            "TRAINED_CLASS_COUNT",
            "src/nexus_scalp/model_lifecycle/model_class_contract.py",
            r"^\s*TRAINED_CLASS_COUNT\s*:\s*int\s*=\s*(3)\b",
            r"\b3\b",
            "",
        ),
    )
    assert err is None
    # The anchored (multi-line) regex must have found the REAL declaration,
    # not the commented one above it.
    assert value != "3"


def test_missing_root_is_a_tooling_error(tmp_path: Path) -> None:
    rc = GATE_MOD.main(["--root", str(tmp_path / "does-not-exist")])
    assert rc == 2


# ---------------------------------------------------------------------------
# 4. Determinism + performance + no-dependency contract (BENCHMARK_PLAN).
# ---------------------------------------------------------------------------


def test_check_is_deterministic() -> None:
    """Two consecutive runs over the same tree must agree exactly."""
    a = GATE_MOD.check_drift(REPO_ROOT)
    b = GATE_MOD.check_drift(REPO_ROOT)
    assert a.ok == b.ok
    assert [f.public() for f in a.findings] == [f.public() for f in b.findings]
    assert a.checked == b.checked and a.docs_scanned == b.docs_scanned


def test_benchmark_completes_under_half_second() -> None:
    """BENCHMARK_PLAN: the drift check must complete in < 0.5 seconds."""
    t0 = time.perf_counter()
    for _ in range(5):
        GATE_MOD.check_drift(REPO_ROOT)
    elapsed = (time.perf_counter() - t0) / 5.0
    assert elapsed < 0.5, f"drift check too slow: {elapsed * 1000:.1f} ms per run"


def test_gate_module_imports_no_project_dependencies() -> None:
    """The static CI lane must stay dependency-free (no torch/polars/yaml)."""
    source = GATE.read_text(encoding="utf-8")
    for forbidden in (
        "import torch",
        "import polars",
        "import yaml",
        "import pandas",
        "import numpy",
    ):
        assert forbidden not in source, f"gate imports a heavy dependency: {forbidden}"
    for required in ("import argparse", "import re", "import sys", "from pathlib import Path"):
        assert required in source


def test_source_extraction_table_is_non_empty() -> None:
    """A shrunken contract table would silently shrink the gate's coverage."""
    assert len(GATE_MOD.SOURCE_EXTRACTION) >= 8
    keys = {spec[0] for spec in GATE_MOD.SOURCE_EXTRACTION}
    assert keys == {
        "ACTIVE_SCHEMA_ID",
        "FEATURE_DIMENSION",
        "TRAINED_CLASS_COUNT",
        "TRAINED_CLASS_NAMES",
        "LEGACY_HEAD_CLASSES",
        "WAIT_LOGIT_INDEX",
        "SCALP_V3_DIMENSION",
        "HARD_MAX_LOTS",
    }
    # Every spec must name a source file the real repo ships.
    for _key, rel, _pat, _doc, _summary in GATE_MOD.SOURCE_EXTRACTION:
        assert (REPO_ROOT / rel).is_file(), f"source file missing from repo: {rel}"


def test_contract_doc_list_matches_suite_on_disk() -> None:
    """The scanned doc list must be exactly the canonical suite present."""
    on_disk = {
        p.name for p in (REPO_ROOT / "docs" / "ml-system").glob("0*.md") if p.name in CONTRACT_DOCS
    }
    assert on_disk == set(CONTRACT_DOCS), "canonical contract doc set drifted"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
