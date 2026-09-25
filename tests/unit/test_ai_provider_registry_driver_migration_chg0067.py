"""ai_providers/registry.py must not touch sqlite3 directly (CHG-0067, Phase 32).

The registry store was the one domain module added AFTER the fabric guards
landed that still did raw ``sqlite3.connect`` + ``PRAGMA journal_mode=WAL``
in domain code (introduced in PR #430). The repo's own Phase-32 guard caught
it, but the guard was never wired into the CI critical suite, so the
violation shipped to ``main`` undetected (Wave 3 post-merge canary, 2026-09).

These tests pin the migration: the store routes every statement through the
driver contract and the module no longer imports sqlite3.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REGISTRY = (
    Path(__file__).resolve().parents[2] / "src" / "nexus_scalp" / "ai_providers" / "registry.py"
)

#: The only modules allowed to touch sqlite3 directly (mirrors the guard).
INFRA_PREFIXES = ("database/", "adapters/database/")


def _module_source() -> str:
    return REGISTRY.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Guard-level: the module no longer depends on sqlite3 at all
# ---------------------------------------------------------------------------


def test_registry_module_does_not_import_sqlite3() -> None:
    """The Phase-32 guard regex: no ``import sqlite3`` in domain code.

    This is the exact assertion ``test_fabric_guards.py`` made and the reason
    the module failed CI's own guard — it is repeated here because the guard
    is still not in the critical suite.
    """
    text = _module_source()
    hits = re.findall(r"^\s*(?:import\s+sqlite3|from\s+sqlite3\s+import\s+.+)$", text, re.MULTILINE)
    assert not hits, "ai_providers/registry.py must not import sqlite3 (domain code)"


def test_registry_module_has_no_raw_connect() -> None:
    """No ``sqlite3.connect(...)`` outside the driver layer."""
    text = _module_source()
    hits = re.findall(r"sqlite3\.connect\s*\(", text)
    assert not hits, "ai_providers/registry.py must not call sqlite3.connect"


def test_registry_module_has_no_pragma() -> None:
    """No ``PRAGMA`` in domain code — the driver owns SQLite-specific setup."""
    text = _module_source()
    hits = re.findall(r"\bPRAGMA\b", text, re.IGNORECASE)
    # The one allowed mention is documentation of what the driver does.
    real = [h for h in hits if h]
    assert not real, "ai_providers/registry.py must not emit PRAGMA"


def test_registry_module_imports_the_driver_contract() -> None:
    """The migration's positive control: the driver is the SQL boundary now."""
    text = _module_source()
    assert "from nexus_scalp.database.drivers import get_driver" in text, (
        "the store must resolve SQL through the driver factory"
    )
    assert "from nexus_scalp.database.config import DatabaseConfig" in text, (
        "the store must build a DatabaseConfig (provider selection)"
    )


# ---------------------------------------------------------------------------
# Behavioral: the store round-trips real data through the driver
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> object:
    """A store on an isolated temp DB — no provider switch needed to exercise it."""
    from nexus_scalp.ai_providers.registry import ProviderRegistryStore

    db = tmp_path / "ai_registry.db"
    return ProviderRegistryStore(db)


def test_store_owns_its_schema(store: object) -> None:
    """The driver bootstraps both tables (additive, IF NOT EXISTS)."""
    tables = set(store._driver.list_tables())
    assert {"ai_provider_config", "ai_provider_activation"} <= tables


def test_upsert_then_read_round_trips(store: object) -> None:
    from nexus_scalp.ai_providers.registry import ProviderConfig

    cfg = ProviderConfig(
        provider_id="openrouter",
        provider_name="OpenRouter",
        endpoint="https://api.openrouter.ai",
        default_model="anthropic/claude-3.5-sonnet",
        configuration_version="1",
    )
    assert store.upsert(cfg) is True
    assert store.list_provider_ids() == ["openrouter"]
    back = store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "anthropic/claude-3.5-sonnet"
    assert len(store.list_configs()) == 1


def test_upsert_updates_in_place_via_on_conflict(store: object) -> None:
    """The portable ON CONFLICT path: a second write updates, not duplicates.

    ``INSERT OR REPLACE`` (the driver's generic upsert) would trip the
    activation table's ``CHECK (id = 1)``, so the store emits the portable
    ``ON CONFLICT ... DO UPDATE`` form directly.
    """
    from nexus_scalp.ai_providers.registry import ProviderConfig

    store.upsert(
        ProviderConfig(
            provider_id="openrouter",
            provider_name="OpenRouter",
            endpoint="https://api.openrouter.ai",
            default_model="v1",
            configuration_version="1",
        )
    )
    store.upsert(
        ProviderConfig(
            provider_id="openrouter",
            provider_name="OpenRouter",
            endpoint="https://api.openrouter.ai",
            default_model="v2",
            configuration_version="2",
        )
    )
    ids = store.list_provider_ids()
    assert ids.count("openrouter") == 1, "upsert must not duplicate rows"
    back = store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "v2"
    assert back.configuration_version == "2"


def test_activation_round_trip_survives_the_check_constraint(store: object) -> None:
    """The single-row activation table: two writes must not violate CHECK(id=1)."""
    from nexus_scalp.ai_providers.registry import ActivationState, DecisionMode

    assert store.set_activation(
        ActivationState(primary_provider="openrouter", decision_mode=DecisionMode.EXTERNAL_ONLY)
    )
    assert store.get_activation() is not None
    assert store.get_activation().primary_provider == "openrouter"

    # second write takes the ON CONFLICT path — a REPLACE would raise here
    assert store.set_activation(
        ActivationState(
            primary_provider="internal_nse_ml", decision_mode=DecisionMode.INTERNAL_ONLY
        )
    )
    act = store.get_activation()
    assert act is not None
    assert act.primary_provider == "internal_nse_ml"
    assert act.decision_mode == DecisionMode.INTERNAL_ONLY


def test_delete_removes_a_row_and_protects_builtins(store: object) -> None:
    from nexus_scalp.ai_providers.registry import (
        BUILTIN_PROVIDER_IDS,
        ProviderRegistryStore,
    )

    # ``upsert`` only accepts the known built-in ids and ``delete`` refuses all
    # of those, so a deletable row cannot exist through the public API. Seed
    # one through the same driver the store uses — the point is the DELETE
    # statement works and returns a truthful rowcount through the driver.
    store._driver.execute(
        "INSERT INTO ai_provider_config (provider_id, blob, configuration_version, updated_at)"
        " VALUES (?, ?, ?, ?)",
        ("custom_gateway", "{}", "1", "2026-01-01T00:00:00Z"),
    )
    assert store.get_config("custom_gateway") is None  # blob is not a valid config
    assert store.delete("custom_gateway") is True
    # the row is gone even though it never deserialized to a ProviderConfig
    rows = store._driver.query(
        "SELECT provider_id FROM ai_provider_config WHERE provider_id = ?",
        ("custom_gateway",),
    )
    assert rows == []
    # built-ins are never removable, even when present
    for builtin in BUILTIN_PROVIDER_IDS:
        assert store.delete(builtin) is False


def test_delete_reports_no_row_as_false(store: object) -> None:
    """A missing row deletes nothing: the post-check must be truthful."""
    # A row that does not exist: delete() must report False, not silently True
    assert store.delete("custom_gateway") is False


def test_unknown_provider_id_is_refused(store: object) -> None:
    from nexus_scalp.ai_providers.registry import ProviderConfig

    cfg = ProviderConfig(
        provider_id="not_a_real_provider",
        provider_name="x",
        endpoint="",
        configuration_version="1",
    )
    assert store.upsert(cfg) is False
    assert store.get_config("not_a_real_provider") is None
