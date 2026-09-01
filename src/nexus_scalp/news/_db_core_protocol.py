"""News DB shared structural protocol (Agent-5 modularization).

The mixins in db_schema/db_articles/db_analysis/db_queries are stateless
method carriers; this Protocol gives type-checkers the connection core
surface (``_connect``/``_now``/``_config``) without introducing runtime
inheritance or extra state. No behavior; typing only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class _NewsDbCoreProto(Protocol):
    """Structural view of ``_NewsDatabaseCore`` for mixin type-checking.

    ``initialize_schema`` is declared here because ``__init__`` calls it;
    the runtime implementation lives in ``SchemaMixin``.
    """

    def initialize_schema(self) -> None: ...

    _config: Any
    _driver: Any
    db_path: Path | None

    def _connect(self, timeout: float = 5.0) -> Any: ...

    @staticmethod
    def _now() -> str: ...
