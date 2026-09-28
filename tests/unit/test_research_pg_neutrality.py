"""Regression guards: research persistence must not silently no-op off SQLite.

The campaign found that ExperienceLedger / StrategyRegistry /
ResearchObservabilityStore / ResearchDatasetBuilder each guarded public
methods on ``audit_repo._is_sqlite`` and returned an empty/False sentinel
when the provider was NOT SQLite — while the provider-portable helpers
(provider_store.queue_write / query_rows / query_one / query_scalar) were
already provider-neutral. The effect on a PostgreSQL box: DATA wrote nothing,
the dataset was empty, backtest aborted on an empty dataset, and the registry
reported REJECTED — all silently, with no error.

These guards pin the contract: those sentinels must not exist, and the
portable helpers must be used.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "nexus_scalp"

#: Modules that must be provider-neutral after the campaign.
_PROVIDER_NEUTRAL_MODULES = (
    "experience/ledger.py",
    "research/registry.py",
    "research/observability.py",
    "research/dataset.py",
)


def _module_path(rel: str) -> Path:
    return SRC_ROOT / rel


@pytest.mark.parametrize("rel", _PROVIDER_NEUTRAL_MODULES)
def test_no_is_sqlite_sentinel_in_research_persistence(rel: str) -> None:
    """A leading ``if not repo._is_sqlite: return <sentinel>`` guard is the
    exact silent-no-op the campaign removed. It must not come back.

    ``_is_sqlite`` may still appear in annotations/comments and in the
    repository's own provider detection — what is banned is a domain store
    *gating a persistence method on it*.
    """
    path = _module_path(rel)
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, str(path))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # `not self.audit_repo._is_sqlite`  /  `not repo._is_sqlite`
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            subject = test.operand
        elif isinstance(test, ast.BoolOp):  # `not x or not repo._is_sqlite`
            continue  # compound preconditions keep their real clause
        else:
            continue
        if isinstance(subject, ast.Attribute) and subject.attr == "_is_sqlite":
            offenders.append(f"line {node.lineno}")
    assert not offenders, f"{rel}: _is_sqlite sentinel guard at {offenders}"


def test_registry_uses_portable_write_helper() -> None:
    """The registry must route its upsert through provider_store.queue_write,
    not the repository's private background queue (SQLite-only surface).
    """
    src = _module_path("research/registry.py").read_text(encoding="utf-8")
    assert "provider_store.queue_write(" in src
    assert "_queue.put_nowait" not in src


def test_observability_uses_portable_write_helper() -> None:
    """ResearchObservabilityStore._queue must persist through the ACTIVE
    provider, not the repository's private queue.
    """
    src = _module_path("research/observability.py").read_text(encoding="utf-8")
    assert "provider_store.queue_write(" in src
    assert "_queue.put_nowait" not in src


def test_observability_no_raw_sqlite_connect() -> None:
    """An *ungated* raw ``sqlite3.connect(repo._db_path)`` on a PostgreSQL box
    silently creates a junk file and reads nothing.

    Upstream #534 keeps a ``_connect`` helper that is explicitly provider-aware
    (sqlite on SQLite, the pooled READ plane otherwise), so the guard is on the
    unguarded call shape, not the helper's existence.
    """
    path = _module_path("research/observability.py")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Any sqlite3.connect call must sit inside the `if repo._is_sqlite:` branch
    # of _connect — i.e. unreachable on a PostgreSQL provider.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "connect":
            continue
        qualifier = node.func.value
        if not (isinstance(qualifier, ast.Name) and qualifier.id == "sqlite3"):
            continue
        line = node.lineno
        for ifnode in ast.walk(tree):
            if not isinstance(ifnode, ast.If):
                continue
            if ifnode.lineno > line:
                continue
            cond = ifnode.test
            if isinstance(cond, ast.Attribute) and cond.attr == "_is_sqlite":
                if ifnode.body and ifnode.body[0].lineno <= line:
                    break
        else:
            raise AssertionError(f"ungated sqlite3.connect at line {line}")


def test_dataset_builder_no_raw_sqlite_connect() -> None:
    src = _module_path("research/dataset.py").read_text(encoding="utf-8")
    assert "sqlite3.connect(" not in src, "raw sqlite3.connect reintroduced"


def test_ledger_write_path_is_not_provider_gated() -> None:
    """record_experience/record_outcome must return False ONLY on a real write
    failure, never because the provider is PostgreSQL.
    """
    src = _module_path("experience/ledger.py").read_text(encoding="utf-8")
    assert "if not self.audit_repo._is_sqlite:" not in src


def test_provider_store_exposes_read_connection() -> None:
    """The portable read seam the dataset builder now relies on must exist."""
    src = _module_path("adapters/database/provider_store.py").read_text(encoding="utf-8")
    assert "def read_connection(" in src
