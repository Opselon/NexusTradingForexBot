"""L7 — archive-before-delete must be RESTORABLE (regression guard).

The hygiene contract is "ACTIVE DB -> ARCHIVE -> VERIFY -> REMOVE FROM HOT
STORE". The existing coverage proves an archive is WRITTEN and its checksum
MATCHES. It does not prove the archive can be READ BACK — and an archive with
no reader is not a backup, it is a deferred delete. This file pins the restore
half so any future retirement (a news.db retirement, a purge of archived rows)
has a verifiable undo.

Pins:
  * a written archive round-trips to the exact rows that went in
  * a corrupt byte raises rather than returning partial/empty rows
  * a missing archive is distinguishable from a corrupt one
  * the on-disk line count is an independent check of the manifest's claim
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.hygiene.archive import ArchiveManager


def _rows(n: int) -> list[dict[str, object]]:
    return [
        {"id": i, "ts": f"2026-09-{i + 1:02d}T00:00:00+00:00", "payload": f"row-{i}"}
        for i in range(n)
    ]


def _write(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[ArchiveManager, dict[str, Any]]:
    am = ArchiveManager(tmp_path)
    manifest = am.archive_rows(
        "news",
        "news_articles",
        rows,
        retention_reason="L7 round-trip pin",
        software_version="test",
    )
    return am, manifest


def test_archive_round_trips_exactly(tmp_path: Path) -> None:
    """The restore path returns every archived row, byte-for-byte equal."""
    rows = _rows(25)
    am, manifest = _write(tmp_path, rows)

    restored = am.read_archive(manifest)

    assert len(restored) == len(rows)
    assert restored == rows


def test_manifest_row_count_matches_lines_on_disk(tmp_path: Path) -> None:
    """The writer's row_count claim is independently checkable."""
    rows = _rows(17)
    am, manifest = _write(tmp_path, rows)

    assert manifest["row_count"] == 17
    assert am.row_count_on_disk(manifest) == 17


def test_corrupt_archive_raises_and_never_returns_partial_rows(tmp_path: Path) -> None:
    """A tampered archive must NOT silently yield a short row list.

    Returning the rows that happened to parse is the dangerous outcome: the
    caller cannot tell a corrupt archive from a small one, so a restore would
    report success while losing data.
    """
    rows = _rows(30)
    am, manifest = _write(tmp_path, rows)

    archive_file = tmp_path / manifest["path"]
    original = archive_file.read_bytes()
    # Flip a byte in the middle: the sha256 no longer matches.
    mid = len(original) // 2
    archive_file.write_bytes(original[:mid] + bytes([original[mid] ^ 0xFF]) + original[mid + 1 :])

    assert am.verify_archive(manifest) is False
    with pytest.raises(RuntimeError, match="MISMATCH"):
        am.read_archive(manifest)


def test_truncated_archive_is_detected_by_the_line_count(tmp_path: Path) -> None:
    """A truncated write leaves fewer lines than the manifest claims."""
    rows = _rows(40)
    am, manifest = _write(tmp_path, rows)

    archive_file = tmp_path / manifest["path"]
    lines = archive_file.read_text(encoding="utf-8").splitlines()
    archive_file.write_text("\n".join(lines[:10]) + "\n", encoding="utf-8")

    # The checksum catches it first; the line count is the second, independent
    # witness (it stays right even if a future writer changes the hash input).
    assert am.row_count_on_disk(manifest) == 10
    assert am.verify_archive(manifest) is False
    with pytest.raises(RuntimeError):
        am.read_archive(manifest)


def test_missing_archive_is_distinguishable_from_a_corrupt_one(tmp_path: Path) -> None:
    """absent -> [] only when asked; corrupt -> always raises."""
    am, manifest = _write(tmp_path, _rows(5))
    (tmp_path / manifest["path"]).unlink()

    # Default is fail-closed: a caller that did not opt into tolerance must
    # learn the archive is gone.
    with pytest.raises(RuntimeError, match="missing"):
        am.read_archive(manifest)
    assert am.read_archive(manifest, missing_ok=True) == []
    assert am.row_count_on_disk(manifest) == 0


def test_empty_manifest_is_not_an_error_when_tolerated(tmp_path: Path) -> None:
    """archive_rows returns {} for zero rows; restoring that must be safe."""
    am = ArchiveManager(tmp_path)
    manifest = am.archive_rows(
        "news", "news_articles", [], retention_reason="nothing", software_version="test"
    )

    assert manifest == {}
    assert am.read_archive(manifest, missing_ok=True) == []
    with pytest.raises(RuntimeError):
        am.read_archive(manifest)
