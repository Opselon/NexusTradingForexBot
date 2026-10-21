"""Per-call SQLite session scope: commit-or-rollback, then CLOSE.

``with sqlite3.connect(path) as conn`` manages only the TRANSACTION — the
connection handle itself stays open until the garbage collector collects it.
That is what the thousands of ``ResourceWarning: unclosed database`` lines in
the suite were: every news/accounting/model-lab call leaked one handle into
GC (one real FD/lock churn cycle per call in production).

So every ``with self._connect() as conn:`` site becomes::

    with session(self._connect()) as conn:

which preserves the exact commit/rollback contract of
``sqlite3.Connection.__exit__`` and then closes the handle deterministically.
Pooled backends (the fabric's news/audit write planes) implement ``close`` as
"release the check-out", so the same helper is safe under PostgreSQL too.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@contextlib.contextmanager
def session(conn: Any) -> Iterator[Any]:
    """Transactional scope: commit on success, rollback + reraise on error,
    close in every case."""
    try:
        yield conn
        conn.commit()
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()
