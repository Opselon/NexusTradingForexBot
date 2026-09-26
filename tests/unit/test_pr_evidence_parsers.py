"""PR Evidence Reporter — pytest/ruff/mypy parsing + classification tests (§22)."""

from __future__ import annotations

from nexus_scalp.pr_evidence.models import FailureCategory
from nexus_scalp.pr_evidence.parsers import (
    classify_failure,
    parse_mypy_output,
    parse_pytest_output,
    parse_ruff_output,
    parse_short_test_summary,
)


class TestShortSummary:
    def test_counts(self) -> None:
        out = parse_short_test_summary("2 failed, 13 passed, 1 skipped, 3 warnings in 4.2s")
        assert out.get("failed") == 2
        assert out.get("passed") == 13
        assert out.get("skipped") == 1

    def test_empty(self) -> None:
        assert parse_short_test_summary("no line here") == {}


class TestPytestLongForm:
    TEXT = """\
_____ test_pg_pool_config_reaches_pool _____

tests/db/test_pg_pool_config.py:184: in test_pg_pool_config_reaches_pool
    assert cfg.password == expected
E   AssertionError: configured password does not reach pool configuration

src/nse/db/postgres.py:214: in build_pool_config
    raise ValueError('configured password does not match pool password')
"""

    def test_parses_test_and_production(self) -> None:
        failures, skipped, counts = parse_pytest_output(self.TEXT)
        assert len(failures) == 1
        f = failures[0]
        assert f.test == "test_pg_pool_config_reaches_pool"
        assert f.location.path == "tests/db/test_pg_pool_config.py"
        assert f.location.line == 184
        assert f.production_location is not None
        assert f.production_location.path == "src/nse/db/postgres.py"
        assert f.production_location.line == 214
        assert f.production_location.function == "build_pool_config"
        assert f.error_type == "AssertionError"
        assert "password" in f.message
        assert len(skipped) == 0

    def test_category_is_test_failure(self) -> None:
        failures, _, _ = parse_pytest_output(self.TEXT)
        assert failures[0].category == FailureCategory.TEST_FAILURE


class TestPytestShortForm:
    def test_failed_line(self) -> None:
        text = "FAILED tests/db/test_pg_pool_config.py::test_x - AssertionError: boom"
        failures, _, _ = parse_pytest_output(text)
        assert len(failures) == 1
        assert failures[0].test == "test_x"
        assert failures[0].location.path == "tests/db/test_pg_pool_config.py"

    def test_skipped_line(self) -> None:
        text = "SKIPPED [1] tests/integration/test_pg.py:91: NSE_PG_TEST_URL not configured"
        _, skipped, _ = parse_pytest_output(text)
        assert len(skipped) == 1
        s = skipped[0]
        assert s.test == "unknown"  # file:line form carries no test name
        assert s.location.path == "tests/integration/test_pg.py"
        assert s.reason == "NSE_PG_TEST_URL not configured"
        assert s.category == FailureCategory.ENVIRONMENT_FAILURE

    def test_skipped_nodeid_form(self) -> None:
        text = "SKIPPED tests/integration/test_pg.py::test_pg_real_connection: missing env"
        _, skipped, _ = parse_pytest_output(text)
        assert len(skipped) == 1
        assert skipped[0].test == "test_pg_real_connection"
        assert skipped[0].location.path == "tests/integration/test_pg.py"

    def test_malformed_does_not_raise(self) -> None:
        failures, skipped, counts = parse_pytest_output("\n\n".join(["???", "", "  "]))
        assert isinstance(failures, list)
        assert isinstance(skipped, list)
        assert isinstance(counts, dict)

    def test_empty(self) -> None:
        assert parse_pytest_output("") == ([], [], {})


class TestRuffParsing:
    def test_lint(self) -> None:
        text = "src/nse/db/postgres.py:214:17: E501 Line too long (110 > 100)"
        out = parse_ruff_output(text, kind="lint")
        assert len(out) == 1
        assert out[0].location.path == "src/nse/db/postgres.py"
        assert out[0].location.line == 214
        assert out[0].location.column == 17
        assert out[0].error_type == "E501"
        assert out[0].category == FailureCategory.LINT_FAILURE

    def test_format(self) -> None:
        text = "src/foo.py:1:1: I001 Import block is un-sorted"
        out = parse_ruff_output(text, kind="format")
        assert out[0].category == FailureCategory.FORMAT_FAILURE

    def test_dedupe(self) -> None:
        text = "src/foo.py:1:1: E501 x\nsrc/foo.py:1:1: E501 x\nsrc/foo.py:2:1: E501 y"
        assert len(parse_ruff_output(text)) == 2


class TestMypyParsing:
    def test_error(self) -> None:
        text = "src/nse/db/postgres.py:214:12: error: Unsupported operand types for +"
        out = parse_mypy_output(text)
        assert len(out) == 1
        assert out[0].location.path == "src/nse/db/postgres.py"
        assert out[0].location.line == 214
        assert out[0].category == FailureCategory.MYPY_FAILURE

    def test_notes_ignored(self) -> None:
        text = "src/foo.py:1:1: note: blah"
        assert parse_mypy_output(text) == []


class TestClassification:
    def test_type_error(self) -> None:
        assert classify_failure("TypeError", "m", "", None, "pytest") == FailureCategory.TYPE_ERROR

    def test_import_error(self) -> None:
        assert (
            classify_failure("ModuleNotFoundError", "m", "", None, "pytest")
            == FailureCategory.IMPORT_FAILURE
        )

    def test_ruff_tool(self) -> None:
        assert classify_failure("unknown", "m", "", None, "ruff") == FailureCategory.LINT_FAILURE

    def test_mypy_tool(self) -> None:
        assert classify_failure("unknown", "m", "", None, "mypy") == FailureCategory.MYPY_FAILURE

    def test_unknown_error(self) -> None:
        assert classify_failure("unknown", "m", "", None, "pytest") == FailureCategory.UNKNOWN

    def test_database(self) -> None:
        assert (
            classify_failure("OperationalError", "could not connect", "", None, "pytest")
            == FailureCategory.DATABASE_FAILURE
        )
