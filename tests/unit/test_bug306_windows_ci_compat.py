"""Regression tests for BUG-306: Windows CI compatibility fixes.

1. official_install._sync_file: On Windows, os.fsync calls CRT _commit(fd),
   which requires write access on the descriptor. Opening "rb" raises
   OSError: [Errno 9] Bad file descriptor (EBADF). Fixed by using "r+b" on NT
   and handling OSError gracefully.
2. test_model_studio.py: test_web_ui_assets_exist read Web/index.html without
   specifying encoding="utf-8", causing UnicodeDecodeError ('charmap' / cp1252)
   on Windows runners.
"""

from __future__ import annotations

import ast
import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus_scalp.model_provisioning import official_install as oi

REPO_ROOT = Path(__file__).parents[2]


def test_sync_file_normal_file(tmp_path: Path) -> None:
    """_sync_file synchronizes a written file without raising."""
    sample = tmp_path / "model.bin"
    sample.write_bytes(b"model bytes payload")
    oi._sync_file(sample)
    assert sample.read_bytes() == b"model bytes payload"


def test_sync_file_windows_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When os.name is 'nt', _sync_file opens with 'r+b' so CRT _commit has write access."""
    sample = tmp_path / "stage.bin"
    sample.write_bytes(b"staged content")

    opened_modes: list[str] = []
    real_open = Path.open

    def recording_open(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        mode = args[0] if args else kwargs.get("mode", "r")
        opened_modes.append(str(mode))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(Path, "open", recording_open)

    oi._sync_file(sample)

    assert "r+b" in opened_modes, f"Expected 'r+b' on Windows, got {opened_modes}"


def test_sync_file_handles_ebadf_gracefully(tmp_path: Path) -> None:
    """Simulate Windows CRT _commit returning EBADF (Bad file descriptor).

    _sync_file must swallow the OSError and not abort the install transaction.
    """
    sample = tmp_path / "journal.tmp"
    sample.write_bytes(b"journal data")

    def mock_fsync(_fd: int) -> None:
        raise OSError(errno.EBADF, "Bad file descriptor")

    with patch("os.fsync", side_effect=mock_fsync):
        # Must not raise:
        oi._sync_file(sample)


def test_sync_file_handles_missing_file_gracefully(tmp_path: Path) -> None:
    """_sync_file on an inaccessible or missing path does not raise uncaught OSError."""
    missing = tmp_path / "nonexistent.file"
    # Must not raise:
    oi._sync_file(missing)


def test_web_ui_assets_utf8_decoding_charmap_regression() -> None:
    """Web/index.html contains multi-byte UTF-8 sequences that crash cp1252/charmap."""
    index_path = REPO_ROOT / "Web" / "index.html"
    assert index_path.is_file()

    raw_bytes = index_path.read_bytes()

    # Demonstrates that default Windows charmap/cp1252 fails on index.html:
    with pytest.raises(UnicodeDecodeError):
        raw_bytes.decode("cp1252")

    # With explicit utf-8, decoding succeeds cleanly:
    text = index_path.read_text(encoding="utf-8")
    assert "tab-model-studio" in text
    assert "model_studio_ui.js" in text


def test_model_studio_test_file_ast_read_text_encoding_guard() -> None:
    """AST guard: every .read_text() in tests/unit/test_model_studio.py must specify encoding."""
    target_file = REPO_ROOT / "tests" / "unit" / "test_model_studio.py"
    tree = ast.parse(target_file.read_text(encoding="utf-8"), filename=str(target_file))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "read_text":
                # Check for encoding argument either positional or keyword
                has_encoding_kw = any(kw.arg == "encoding" for kw in node.keywords)
                has_encoding_arg = len(node.args) >= 1
                if not (has_encoding_kw or has_encoding_arg):
                    violations.append(f"line {node.lineno}: {ast.unparse(node)}")

    assert not violations, f"Found unencoded read_text calls in test_model_studio.py: {violations}"
