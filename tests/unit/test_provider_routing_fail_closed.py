from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nexus_scalp.database.config import DatabaseConfigError
from nexus_scalp.database.ops_provider import active_provider_is_postgresql, resolve_audit_db_url
from nexus_scalp.database.provider import DatabaseProvider, ProviderConfigurationError


@pytest.mark.parametrize("raw", [None, "", " ", "mysql"])
def test_provider_parse_rejects_unknown_or_empty(raw: str | None) -> None:
    with pytest.raises(ProviderConfigurationError):
        DatabaseProvider.parse(raw)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("sqlite", DatabaseProvider.SQLITE),
        ("sqlite3", DatabaseProvider.SQLITE),
        ("postgres", DatabaseProvider.POSTGRESQL),
        ("pgsql", DatabaseProvider.POSTGRESQL),
    ],
)
def test_provider_parse_preserves_aliases(raw: str, expected: DatabaseProvider) -> None:
    assert DatabaseProvider.parse(raw) is expected


def test_ops_provider_does_not_fallback_on_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise DatabaseConfigError("invalid provider")

    monkeypatch.setattr("nexus_scalp.database.ops_provider.load_database_config", fail)
    with pytest.raises(DatabaseConfigError):
        resolve_audit_db_url()
    with pytest.raises(DatabaseConfigError):
        active_provider_is_postgresql()


def test_reverse_migration_rejects_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_scalp.web import db_provider_routes

    monkeypatch.setattr(db_provider_routes, "load_database_config", lambda domain: DatabaseConfigError())
    assert True
