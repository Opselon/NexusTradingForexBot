"""Connection URL parse / build / redact matrix (Lane A, contract §3.1).

Pinned here: the server-side parse is the authority the react console mirrors
by contract (frontend/.../connectionUrl.ts).  These tests fix the accepted
scheme set, every documented refusal and the round trip, so the client mirror
can be validated against real behavior instead of prose.
"""

from __future__ import annotations

import pytest

from nexus_scalp.database.connection_url import (
    PG_URL_SCHEMES,
    ParsedPgConfig,
    ParseFailure,
    build_pg_url,
    parse_pg_url,
    redact_url,
)


class TestAcceptedSchemes:
    @pytest.mark.parametrize("scheme", sorted(PG_URL_SCHEMES))
    def test_postgres_family_schemes_parse(self, scheme: str) -> None:
        got = parse_pg_url(f"{scheme}://ops@db.internal:5433/nse_audit")
        assert isinstance(got, dict)
        assert got["host"] == "db.internal"
        assert got["port"] == 5433
        assert got["database"] == "nse_audit"
        assert got["username"] == "ops"

    @pytest.mark.parametrize("scheme", ["mysql", "sqlite", "http", "ftp", "oracle"])
    def test_other_schemes_are_refused(self, scheme: str) -> None:
        got = parse_pg_url(f"{scheme}://ops@db.internal:5433/nse_audit")
        assert "reason" in got
        assert got["reason"]
        # the refusal names the offending scheme without echoing credentials
        assert scheme in got["reason"]


class TestHappyPath:
    def test_full_url_with_query(self) -> None:
        got = parse_pg_url("postgresql://ops@db.internal:5433/nse_audit?sslmode=require")
        assert got == {
            "host": "db.internal",
            "port": 5433,
            "database": "nse_audit",
            "username": "ops",
            "ssl_mode": "require",
        }

    def test_password_is_parsed_but_absent_from_fields(self) -> None:
        got = parse_pg_url("postgresql://ops:***@db.internal:5433/nse_audit")
        assert isinstance(got, dict)
        assert "password" not in got
        assert got["username"] == "ops"

    def test_port_defaults_to_5432_when_omitted(self) -> None:
        got = parse_pg_url("postgresql://ops@db.internal/nse_audit")
        assert got["port"] == 5432

    def test_empty_username_is_allowed(self) -> None:
        got = parse_pg_url("postgresql://db.internal:5432/nse_audit")
        assert got["username"] == ""
        assert got["host"] == "db.internal"

    def test_ssl_mode_key_spelling_is_accepted(self) -> None:
        for key in ("sslmode", "ssl_mode"):
            got = parse_pg_url(f"postgresql://ops@h/db?{key}=verify-full")
            assert got["ssl_mode"] == "verify-full"

    def test_unknown_query_keys_are_ignored_not_errors(self) -> None:
        got = parse_pg_url("postgresql://ops@h/db?application_name=x&sslmode=require")
        assert got["ssl_mode"] == "require"

    def test_no_sslmode_leaves_ssl_mode_empty(self) -> None:
        got = parse_pg_url("postgresql://ops@h/db")
        assert got["ssl_mode"] == ""

    def test_ipv6_host_literal(self) -> None:
        got = parse_pg_url("postgresql://ops@[::1]:5433/nse_audit")
        assert got["host"] == "::1"
        assert got["port"] == 5433

    def test_url_encoded_username_and_database(self) -> None:
        got = parse_pg_url("postgresql://o%40p@db.internal/nse%5Faudit")
        assert got["username"] == "o@p"
        assert got["database"] == "nse_audit"

    def test_trailing_slash_only_is_refused_missing_db(self) -> None:
        got = parse_pg_url("postgresql://ops@db.internal:5432/")
        assert "reason" in got
        assert "database" in got["reason"]


class TestRefusals:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "postgresql://",
        ],
    )
    def test_empty_or_scheme_only_url(self, raw: str) -> None:
        got = parse_pg_url(raw)
        assert "reason" in got
        assert got["reason"]

    def test_missing_host(self) -> None:
        got = parse_pg_url("postgresql://:5432/nse_audit")
        assert "host" in got["reason"]

    def test_missing_database(self) -> None:
        got = parse_pg_url("postgresql://ops@db.internal:5432")
        assert "database" in got["reason"]

    @pytest.mark.parametrize("port", ["abc", "0", "65536", "-1", "12x", "5432.0"])
    def test_bad_port(self, port: str) -> None:
        got = parse_pg_url(f"postgresql://ops@db.internal:{port}/nse_audit")
        assert "reason" in got
        assert "port" in got["reason"].lower()

    def test_port_boundaries_are_accepted(self) -> None:
        assert parse_pg_url("postgresql://ops@db.internal:1/nse_audit")["port"] == 1
        assert parse_pg_url("postgresql://ops@db.internal:65535/nse_audit")["port"] == 65535

    def test_unterminated_ipv6_literal(self) -> None:
        got = parse_pg_url("postgresql://ops@[::1/nse_audit")
        assert "reason" in got

    def test_no_scheme_marker(self) -> None:
        got = parse_pg_url("db.internal:5432/nse_audit")
        assert "reason" in got

    def test_non_string_input_is_refused_not_raised(self) -> None:
        got = parse_pg_url(None)  # type: ignore[arg-type]
        assert "reason" in got

    def test_parse_never_raises_on_garbage(self) -> None:
        # a URL must never raise into the route layer
        for raw in ["://", "postgresql://@@@/", "postgresql://[::1", "postgres://?"]:
            got = parse_pg_url(raw)
            assert "reason" in got


class TestBuildUrl:
    def test_round_trip_without_ssl_mode(self) -> None:
        cfg: ParsedPgConfig = {
            "host": "db.internal",
            "port": 5433,
            "database": "nse_audit",
            "username": "ops",
            "ssl_mode": "",
        }
        built = build_pg_url(cfg)
        assert built == "postgresql://ops@db.internal:5433/nse_audit"
        again = parse_pg_url(built)
        assert again == cfg

    def test_round_trip_with_ssl_mode(self) -> None:
        cfg: ParsedPgConfig = {
            "host": "db.internal",
            "port": 5432,
            "database": "nse_audit",
            "username": "ops",
            "ssl_mode": "require",
        }
        built = build_pg_url(cfg)
        assert "sslmode=require" in built
        assert parse_pg_url(built) == cfg

    def test_password_field_never_emitted(self) -> None:
        built = build_pg_url(
            {
                "host": "db.internal",
                "port": 5432,
                "database": "nse_audit",
                "username": "ops",
                "ssl_mode": "",
            }
        )
        assert "password" not in built
        assert ":" not in built.split("://", 1)[1].split("@", 1)[0]

    def test_empty_username_emits_no_userinfo(self) -> None:
        built = build_pg_url(
            {
                "host": "db.internal",
                "port": 5432,
                "database": "nse_audit",
                "username": "",
                "ssl_mode": "",
            }
        )
        assert built == "postgresql://db.internal:5432/nse_audit"

    def test_ipv6_host_is_bracketed(self) -> None:
        built = build_pg_url(
            {
                "host": "::1",
                "port": 5433,
                "database": "nse_audit",
                "username": "ops",
                "ssl_mode": "",
            }
        )
        assert built == "postgresql://ops@[::1]:5433/nse_audit"


class TestRedactUrl:
    def test_password_is_masked(self) -> None:
        assert (
            redact_url("postgresql://ops:***@db.internal:5433/nse_audit")
            == "postgresql://ops:***@db.internal:5433/nse_audit"
        )

    def test_no_password_passes_through(self) -> None:
        assert (
            redact_url("postgresql://ops@db.internal:5433/nse_audit")
            == "postgresql://ops@db.internal:5433/nse_audit"
        )

    def test_url_without_userinfo_passes_through(self) -> None:
        assert redact_url("postgresql://db.internal:5433/nse_audit") == (
            "postgresql://db.internal:5433/nse_audit"
        )

    def test_not_a_url_passes_through(self) -> None:
        assert redact_url("not a url") == "not a url"

    def test_non_string_is_returned_unchanged(self) -> None:
        assert redact_url(None) is None  # type: ignore[arg-type]

    def test_masked_url_still_parses(self) -> None:
        masked = redact_url("postgresql://ops:***@db.internal:5433/nse_audit")
        got = parse_pg_url(masked)
        assert got["host"] == "db.internal"
        assert got["username"] == "ops"


def test_failure_shape_is_a_human_sentence() -> None:
    got: ParseFailure = parse_pg_url("mysql://x/y")  # type: ignore[assignment]
    assert got["reason"].endswith(".")
    assert "Traceback" not in got["reason"]
    assert "Error" not in got["reason"] or "scheme" in got["reason"].lower()
