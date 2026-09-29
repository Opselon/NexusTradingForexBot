"""L7 — R-11: retire the frozen news SQLite dataset from the walked set.

`artifacts/news.db` stopped receiving writes when this box's news domain moved
to PostgreSQL, but the hygiene worker's allowlist is a literal dict, so the
cycle kept scanning the frozen 241 MB file. Measured on a copy of the live
artifact: 1,551 ms of a 1,627 ms cycle, re-deriving the same 21,553 duplicates
with delete_candidates == 0.

These pins hold the retirement to the safe shape:
  * retired under PostgreSQL (the file is frozen and duplicates PG)
  * STILL MANAGED under SQLite (there it is the live store — un-managing it
    would silently end its maintenance, which is the failure mode that matters)
  * reversible: include_retired=True reproduces the pre-retirement set exactly
  * the reason is recorded on the entry, so the decision stays auditable
"""

from __future__ import annotations

from nexus_scalp.hygiene.archive import (
    RETIRED_MANAGED_DATABASES,
    managed_sqlite_databases,
)

_PRE_R11 = {
    "audit": "artifacts/audit.db",
    "news": "artifacts/news.db",
    "candle_intel": "artifacts/candle_intel.db",
}


def test_news_is_retired_under_postgresql() -> None:
    managed = managed_sqlite_databases(provider="postgresql")
    assert "news" not in managed
    assert managed == {"audit": "artifacts/audit.db", "candle_intel": "artifacts/candle_intel.db"}


def test_news_is_still_managed_under_sqlite() -> None:
    """The default install must keep its news maintenance."""
    assert managed_sqlite_databases(provider="sqlite") == _PRE_R11


def test_provider_matching_is_case_and_space_insensitive() -> None:
    for variant in ("PostgreSQL", " postgresql ", "POSTGRESQL"):
        assert "news" not in managed_sqlite_databases(provider=variant)
    for variant in ("SQLite", " sqlite "):
        assert "news" in managed_sqlite_databases(provider=variant)


def test_retirement_is_reversible() -> None:
    """include_retired reproduces the pre-R-11 set byte-for-byte."""
    assert managed_sqlite_databases(provider="postgresql", include_retired=True) == _PRE_R11


def test_unknown_provider_fails_safe_and_keeps_the_dataset_managed() -> None:
    """An unrecognized provider must not silently un-manage a live store."""
    assert "news" in managed_sqlite_databases(provider="")
    assert "news" in managed_sqlite_databases(provider="something-new")


def test_every_retirement_records_its_evidence() -> None:
    """A retirement without a reason is an undocumented removal."""
    assert RETIRED_MANAGED_DATABASES, "a retirement entry must exist for R-11"
    for key, meta in RETIRED_MANAGED_DATABASES.items():
        assert meta.get("path"), f"{key} must name the file it retires"
        assert meta.get("reason"), f"{key} must record WHY it was retired"
        assert meta.get("retired_by"), f"{key} must record WHO retired it"
        assert meta.get("remains_valid_under") in ("sqlite",), (
            f"{key} must name the provider under which it stays valid"
        )


def test_retired_keys_are_real_managed_keys() -> None:
    """A retirement can only remove a key that was actually managed."""
    for key, meta in RETIRED_MANAGED_DATABASES.items():
        assert key in _PRE_R11, f"{key} was never in the managed set"
        assert _PRE_R11[key] == meta["path"], f"{key} path disagrees with the base set"
