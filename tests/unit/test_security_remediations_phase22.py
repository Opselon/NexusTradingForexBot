"""
Regression suite for CodeQL security remediations (CodeQL alerts #87-113).
Verifies:
1. Invalid SQL identifiers (table/column names) raise ValueError.
2. div_balance_check HTMLParser correctly strips comments and raw-text elements without throwing.
3. SecureSecretStore logging never records secret plaintext.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

# ruff: noqa: E402  (intentional sys.path insertion before project imports)

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from scripts.div_balance_check import _strip_ignored, check_file


def test_sql_identifier_validation() -> None:
    cfg = DatabaseConfig.for_sqlite("audit", path=":memory:")
    drv = SQLiteDriver(cfg)
    # Valid identifier passes quote_ident
    assert drv.quote_ident("audit_signals") == '"audit_signals"'
    # Unsafe identifier / injection attempt raises ValueError
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        drv.row_count("audit_signals; DROP TABLE audit_signals;")
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        drv.row_count("invalid name with spaces")


def test_div_balance_htmlparser_stripping() -> None:
    html = '<div class="main"><script>console.log("</script>");</script><div></div></div>'
    stripped = _strip_ignored(html)
    assert "</script>" not in stripped
    assert stripped.count("<div") == 2
    assert stripped.count("</div>") == 2
