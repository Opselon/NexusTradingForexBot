#!/usr/bin/env python3
"""ML contract drift guard: docs/ml-system cannot lie about executable truth.

Companion gate to the ML-System documentation suite (``docs/ml-system/``).
Past audits found the canonical docs claiming things source code contradicted:
70D described as production-active while ``ACTIVE_SCHEMA_ID`` stayed
``scalp_v1`` (50D); a 4-class head described as live while only 3 classes are
trained. "Developer must remember to update the docs" is not an enforcement
mechanism (same rationale as ``critical_manifest.py``).

This gate parses CANONICAL CONSTANTS FROM SOURCE (the single source of truth)
and asserts the documentation suite cites the same values. It is deliberately
a *value* check, not natural-language understanding: each canonical constant
is declared once in source and must appear, with a matching value, in at
least one place in the documentation contract.

Direction of truth (binding, matches docs/ml-system/01_SYSTEM_CONTRACT.md §2):
source wins. The gate therefore never fails because a doc added a new
sentence; it fails only when a doc quotes a value the source no longer holds
(or when a canonical constant disappears from source without the docs being
updated, which is the same drift viewed from the other side).

Two failure classes:
  * STALE_DOC   — a doc quotes a value that differs from the source value.
  * MISSING_DOC — a canonical constant is not cited anywhere in the suite
                  (docs drifted silent, which is how the 70D/50D confusion
                  originally survived as long as it did).

Exit codes: 0 = in sync, 1 = drift detected, 2 = tooling/error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO / "docs" / "ml-system"

#: Canonical constants the contract pins. Each entry is:
#:   (contract_key, source_module_path, value regex, doc pattern, summary)
#:
#: SOURCE_EXTRACTION is deliberately regex-on-source (not import): importing
#: the feature/model packages pulls torch+polars into a CI static lane that
#: must stay cheap and dependency-free (same design as check_torch_load_safety
#: and check_migration_safety). The patterns are anchored to the exact
#: declaration lines so a comment mentioning the symbol cannot satisfy them.
#:
#: The DOC_PATTERN is matched on lines that already contain the source value;
#: it narrows prose numerals to *value-shaped assertions* for the contract.
SOURCE_EXTRACTION: list[tuple[str, str, str, str, str]] = [
    (
        "ACTIVE_SCHEMA_ID",
        "src/nexus_scalp/features/schema.py",
        r'^\s*ACTIVE_SCHEMA_ID\s*:\s*str\s*=\s*"([^"]+)"',
        r"scalp_v1",
        "Active live feature schema id",
    ),
    (
        "FEATURE_DIMENSION",
        "src/nexus_scalp/features/schema.py",
        r"^\s*dimension\s*=\s*(\d+)\b",
        r"\b50D\b|\b50 features\b|\(N,\s*50\)|\(1,\s*50\)|NUM_FEATURES\s*=\s*50",
        "Active live feature dimension (scalp_v1 = 50)",
    ),
    (
        "TRAINED_CLASS_COUNT",
        "src/nexus_scalp/model_lifecycle/model_class_contract.py",
        r"^\s*TRAINED_CLASS_COUNT\s*:\s*int\s*=\s*(\d+)",
        r"TRAINED_CLASS_COUNT|\b3-class\b|\bthree classes\b|3 trained",
        "Trained model class count",
    ),
    (
        "TRAINED_CLASS_NAMES",
        "src/nexus_scalp/model_lifecycle/model_class_contract.py",
        r'^\s*TRAINED_CLASS_NAMES\s*:.*=\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
        r"NO_TRADE",
        "Trained class name vocabulary (3-class contract)",
    ),
    (
        "LEGACY_HEAD_CLASSES",
        "src/nexus_scalp/model_lifecycle/model_class_contract.py",
        r"^\s*LEGACY_HEAD_CLASSES\s*:.*=\s*(4)\b",
        r"\b4 logits\b|\b4-wide\b|\b4 wide\b|LEGACY_HEAD_CLASSES|4 logits",
        "Legacy 4-wide head size (dead WAIT logit compatibility)",
    ),
    (
        "WAIT_LOGIT_INDEX",
        "src/nexus_scalp/model_lifecycle/model_class_contract.py",
        r"^\s*WAIT_LOGIT_INDEX\s*:.*=\s*(3)\b",
        r"WAIT_LOGIT_INDEX|index 3|\b3rd logit\b",
        "Index of the contracturally-dead WAIT logit",
    ),
    (
        "SCALP_V3_DIMENSION",
        "src/nexus_scalp/features/schema_contract.py",
        r"^\s*DIMENSION\s*:\s*int\s*=\s*(70)\b",
        r"\b70D\b|\b70 features\b|DIMENSION\s*=\s*70|dimension.{0,12}\b70\b",
        "Canonical 70D research/candidate schema dimension",
    ),
    (
        "HARD_MAX_LOTS",
        "src/nexus_scalp/execution/order_manager.py",
        r"^\s*HARD_MAX_LOTS\s*:\s*float\s*=\s*(10(?:\.0+)?)",
        r"HARD_MAX_LOTS",
        "Hard maximum lot ceiling (execution contract)",
    ),
]

#: Documentation files that constitute the canonical contract suite. A
#: constant cited in ANY of them satisfies MISSING_DOC.
CONTRACT_DOCS: tuple[str, ...] = (
    "01_SYSTEM_CONTRACT.md",
    "02_DATA_CONTRACT.md",
    "03_MODEL_ARCHITECTURE.md",
    "05_INFERENCE_FORWARDTEST_GOVERNANCE.md",
)

#: Documentation statements that must remain present verbatim — invariants the
#: docs assert as architectural truths. Removing one silently re-opens the
#: confusion this gate exists to prevent, so the absence is itself drift.
DOC_INVARIANTS: list[tuple[str, str, str]] = [
    (
        "01_SYSTEM_CONTRACT.md",
        'ACTIVE_SCHEMA_ID = "scalp_v1"',
        "the 50D live schema id is named as the active contract",
    ),
    (
        "03_MODEL_ARCHITECTURE.md",
        "TRAINED_CLASS_COUNT",
        "the 3-class contract is cited in the architecture contract",
    ),
    (
        "02_DATA_CONTRACT.md",
        "scalp_v3",
        "the 70D candidate schema is distinguished from the live 50D one",
    ),
]

_MAX_DRIFT_MS_BUDGET = 500.0


@dataclass(frozen=True)
class Finding:
    """One drift finding (STALE_DOC or MISSING_DOC)."""

    kind: str
    contract_key: str
    detail: str
    evidence: str
    severity: str = "error"

    def public(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "contract": self.contract_key,
            "severity": self.severity,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class DriftReport:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    docs_scanned: int = 0
    elapsed_ms: float = 0.0
    tooling_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings and not self.tooling_errors

    def public(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "contracts_checked": self.checked,
            "docs_scanned": self.docs_scanned,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "findings": [f.public() for f in self.findings],
            "tooling_errors": list(self.tooling_errors),
        }

    def to_json(self) -> str:
        """Unwrapped JSON (never via a rich console — see BUG-300)."""
        return json.dumps(self.public(), indent=2)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def extract_source_value(
    repo: Path, spec: tuple[str, str, str, str, str]
) -> tuple[object, str | None]:
    """Extracts the canonical value from source via the anchored regex.

    Returns (value, error). A None value with no error means the declaration
    line moved or was renamed — reported as MISSING_DOC so the contract
    rename surfaces instead of silently passing.
    """
    _key, rel, pattern, _doc_pattern, _summary = spec
    src = repo / rel
    text = _read(src)
    if text is None:
        return None, f"source file missing: {rel}"
    m = re.search(pattern, text, re.MULTILINE)
    if m is None:
        return None, None
    if m.groups():
        return m.group(1), None
    return m.group(0).strip(), None


def _doc_corpus(repo: Path) -> dict[str, str]:
    """Reads the canonical contract docs from the repo root being checked.

    Paths are resolved against ``repo`` (never against CWD): a caller passing a
    sandbox root must get the sandbox's docs, or the gate would silently check
    the wrong tree and pass vacuously.
    """
    out: dict[str, str] = {}
    for name in CONTRACT_DOCS:
        p = repo / "docs" / "ml-system" / name
        text = _read(p)
        if text is not None:
            out[name] = text
    return out


def _doc_citations(
    corpus: dict[str, str], value: str, doc_pattern: str
) -> list[tuple[str, int, str]]:
    """Doc lines asserting this contract value.

    The needle is the SOURCE VALUE the contract pins (e.g. ``50`` for the
    live dimension, ``3`` for the class count), because that is the token a
    documentation line actually writes — docs say "50D" / "3-class", they do
    not repeat the Python identifier. ``doc_pattern`` narrows the match to
    value-shaped assertions so unrelated numerals in prose do not satisfy it.
    """
    hits: list[tuple[str, int, str]] = []
    for name, text in sorted(corpus.items()):
        for i, line in enumerate(text.splitlines(), start=1):
            if value not in line:
                continue
            if re.search(doc_pattern, line):
                hits.append((name, i, line.strip()))
    return hits


def check_drift(repo: Path | None = None) -> DriftReport:
    """Runs the full drift check. Deterministic, offline, dependency-free."""
    import time

    t0 = time.perf_counter()
    repo = Path(repo) if repo is not None else REPO
    rep = DriftReport()
    corpus = _doc_corpus(repo)
    rep.docs_scanned = len(corpus)
    if not corpus:
        rep.tooling_errors.append(
            "no canonical contract docs found under docs/ml-system/ "
            "(01_SYSTEM_CONTRACT.md .. 05_INFERENCE_FORWARDTEST_GOVERNANCE.md)"
        )
        return rep

    for spec in SOURCE_EXTRACTION:
        key, _rel, _pattern, doc_pattern, summary = spec
        rep.checked += 1
        value, src_err = extract_source_value(repo, spec)
        if src_err is not None:
            rep.findings.append(
                Finding(
                    kind="MISSING_DOC",
                    contract_key=key,
                    detail=(
                        f"{summary}: canonical source declaration is gone "
                        f"({src_err}); the contract suite no longer reflects "
                        f"executable truth"
                    ),
                    evidence=f"source: {_rel}",
                )
            )
            continue
        if value is None:
            rep.findings.append(
                Finding(
                    kind="MISSING_DOC",
                    contract_key=key,
                    detail=(
                        f"{summary}: the canonical declaration line was not "
                        f"found in source — the constant was renamed, moved or "
                        f"deleted without a matching docs update"
                    ),
                    evidence="source declaration regex did not match",
                )
            )
            continue

        citations = _doc_citations(corpus, str(value), doc_pattern)
        if not citations:
            rep.findings.append(
                Finding(
                    kind="STALE_DOC",
                    contract_key=key,
                    detail=(
                        f"{summary}: source value {value!r} is not asserted "
                        f"anywhere in the canonical contract docs — the docs "
                        f"either predate the change or never cited it"
                    ),
                    evidence=f"source value {value!r}; no doc line asserts it",
                )
            )
            continue

        # STALE_DOC: a doc line names the contract and asserts a value that is
        # NOT the current source value. Catching this (rather than trusting the
        # first citation) is what makes a source change without a docs update
        # fail the gate instead of passing on a stale doc sentence.
        stale = _find_stale_claims(corpus, key, str(value))
        if stale:
            for name, i, line in stale:
                rep.findings.append(
                    Finding(
                        kind="STALE_DOC",
                        contract_key=key,
                        detail=(
                            f"{summary}: docs cite a value that differs from "
                            f"the source value {value!r}"
                        ),
                        evidence=f"{name}:{i}: {line}",
                    )
                )

    for name, needle, why in DOC_INVARIANTS:
        text = corpus.get(name, "")
        if needle not in text:
            rep.findings.append(
                Finding(
                    kind="MISSING_DOC",
                    contract_key=f"DOC_INVARIANT:{needle}",
                    detail=(f"canonical contract invariant no longer stated in {name} — {why}"),
                    evidence=f"docs/ml-system/{name}: {needle!r} absent",
                )
            )

    rep.elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return rep


def _line_claims_value(line: str, key: str, source_value: str) -> bool:
    """A doc line names the contract AND asserts a value that is not current.

    Only assignment/copula shapes count as a value claim, so prose mentioning
    the symbol (``was``, ``in <file>``, a parenthesised reference) never fires.
    """
    if key not in line:
        return False
    if key + "_NAMES" in line:  # the vocabulary contract is a separate check
        return False
    if not _assertes_contract_value(line, key):
        return False
    return source_value not in line


def _assertes_contract_value(line: str, key: str) -> bool:
    """True when a documentation line asserts a value FOR this contract.

    Two shapes count as an assertion (both must be present-tense):
      * ``KEY = v`` / ``KEY: v`` / ``KEY: int = v``  — an assignment shape;
      * ``KEY is/equals v`` — a copula shape.
    Everything else (past tense, ``KEY was``, ``KEY in <file>``, a bare
    parenthesised reference) is prose and is NOT a value claim.
    """
    if re.search(re.escape(key) + r"\s*[:=]\s*[A-Za-z0-9_.-]+", line):
        return True
    return bool(re.search(re.escape(key) + r"\s+(?:is|equals)\s+[A-Za-z0-9_.-]+", line))


def _find_stale_claims(
    corpus: dict[str, str], key: str, source_value: str
) -> list[tuple[str, int, str]]:
    stale: list[tuple[str, int, str]] = []
    for name, text in sorted(corpus.items()):
        for i, line in enumerate(text.splitlines(), start=1):
            if _line_claims_value(line, key, source_value):
                stale.append((name, i, line.strip()))
    return stale


def _format_text(rep: DriftReport) -> str:
    lines: list[str] = []
    if rep.ok:
        lines.append(
            f"ML contract drift check clean: {rep.checked} canonical constants "
            f"verified against {rep.docs_scanned} contract docs "
            f"({rep.elapsed_ms:.1f} ms)."
        )
        return "\n".join(lines) + "\n"
    for err in rep.tooling_errors:
        lines.append(f"::error::ML contract drift tooling error: {err}")
    for f in rep.findings:
        lines.append(f"::error::ML contract drift [{f.kind}] {f.contract_key}: {f.detail}")
        lines.append(f"  evidence: {f.evidence}")
    lines.append(
        f"\n{len(rep.findings)} drift finding(s); {rep.checked} contracts "
        f"checked against {rep.docs_scanned} docs ({rep.elapsed_ms:.1f} ms)."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        default=str(REPO),
        help="repository root (default: inferred from script location)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable report instead of text",
    )
    args = ap.parse_args(argv)

    repo = Path(args.root).resolve()
    if not repo.is_dir():
        print(f"::error::repository root not found: {repo}", file=sys.stderr)
        return 2
    rep = check_drift(repo)
    if rep.elapsed_ms > _MAX_DRIFT_MS_BUDGET:
        rep.tooling_errors.append(
            f"drift check exceeded the {_MAX_DRIFT_MS_BUDGET:.0f} ms budget "
            f"({rep.elapsed_ms:.1f} ms)"
        )
    if args.json:
        # Unwrapped stdout (a rich console.print would wrap at 80 columns and
        # corrupt the payload with literal newlines — the BUG-300 CLI lesson).
        sys.stdout.write(rep.to_json() + "\n")
    else:
        sys.stdout.write(_format_text(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
