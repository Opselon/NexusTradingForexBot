"""PR Evidence Reporter — generic location-extraction tests (spec §4/§22).

Covers: pytest failure parsing, traceback file extraction, line/column/function
extraction, absolute-path + Windows-path normalization, and malformed output.
"""

from __future__ import annotations

import pytest

from nexus_scalp.pr_evidence.locations import (
    extract_error_type,
    extract_function,
    extract_location,
    extract_locations,
    extract_test_nodeid,
    normalize_repo_relative,
    parse_pytest_traceback,
)


class TestNormalizeRepoRelative:
    def test_already_relative_posix(self) -> None:
        assert normalize_repo_relative("src/nse/db/postgres.py") == "src/nse/db/postgres.py"

    def test_windows_absolute_strips_username(self) -> None:
        path = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nse\db\postgres.py"
        assert normalize_repo_relative(path) == "src/nse/db/postgres.py"

    def test_posix_absolute_strips_home(self) -> None:
        path = "/home/runner/work/NexusTradingForexBot/src/nse/db/postgres.py"
        assert normalize_repo_relative(path) == "src/nse/db/postgres.py"

    def test_repo_root_prefix_is_stripped(self) -> None:
        path = "/home/runner/work/repo/src/foo.py"
        out = normalize_repo_relative(path, "/home/runner/work/repo")
        assert out == "src/foo.py"

    def test_username_is_never_published(self) -> None:
        path = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\foo.py"
        assert "Capsizer" not in normalize_repo_relative(path)
        assert "C:" not in normalize_repo_relative(path)

    def test_empty_and_scheme_inputs_become_unknown(self) -> None:
        assert normalize_repo_relative("") == "unknown"
        assert normalize_repo_relative("postgresql://postgres:pw@localhost:5432/db") == "unknown"

    def test_non_source_paths_rejected(self) -> None:
        assert normalize_repo_relative(".git/config") == "unknown"
        assert normalize_repo_relative("node_modules/x/index.js") == "unknown"


class TestNodeidExtraction:
    def test_nodeid(self) -> None:
        out = extract_test_nodeid(
            "tests/db/test_pg_pool_config.py::test_pg_pool_config_reaches_pool"
        )
        assert out == ("tests/db/test_pg_pool_config.py", "test_pg_pool_config_reaches_pool")

    def test_parametrized_nodeid_keeps_bracket(self) -> None:
        out = extract_test_nodeid("tests/unit/test_x.py::test_y[1]")
        assert out is not None and out[1] == "test_y[1]"

    def test_no_nodeid(self) -> None:
        assert extract_test_nodeid("nothing here") is None


class TestLocationExtraction:
    def test_traceback_frame(self) -> None:
        text = (
            'File "/home/runner/work/repo/tests/db/test_pg_pool_config.py", '
            "line 184, in test_pg_pool_config_reaches_pool\n"
            "    assert cfg.password == expected"
        )
        loc = extract_location(text)
        assert loc is not None
        assert loc.path == "tests/db/test_pg_pool_config.py"
        assert loc.line == 184
        assert loc.function == "test_pg_pool_config_reaches_pool"

    def test_path_line_column(self) -> None:
        loc = extract_location("src/foo.py:42:17 error here")
        assert loc is not None
        assert loc.path == "src/foo.py"
        assert loc.line == 42
        assert loc.column == 17

    def test_path_line_only(self) -> None:
        loc = extract_location("src/foo.py:42 error here")
        assert loc is not None
        assert loc.line == 42 and loc.column is None

    def test_js_frame(self) -> None:
        loc = extract_location("    at render (frontend/src/App.tsx:12:34)")
        assert loc is not None
        assert loc.path == "frontend/src/App.tsx"
        assert loc.line == 12 and loc.column == 34
        assert loc.function == "render"

    def test_nodeid_preferred_for_function_name(self) -> None:
        text = "FAILED tests/db/test_pg_pool_config.py::test_pg_pool_config_reaches_pool"
        loc = extract_location(text)
        assert loc is not None
        assert loc.function == "test_pg_pool_config_reaches_pool"

    def test_prefer_test_bias(self) -> None:
        text = (
            'File "/home/runner/repo/src/nse/db/postgres.py", line 214, in build_pool_config\n'
            'File "/home/runner/repo/tests/db/test_pg_pool_config.py", line 184, in test_x'
        )
        test_loc = extract_location(text, prefer_test=True)
        prod_loc = extract_location(text, prefer_test=False)
        assert test_loc is not None and test_loc.path.endswith("test_pg_pool_config.py")
        assert prod_loc is not None and prod_loc.path.endswith("postgres.py")

    def test_no_location_in_text(self) -> None:
        assert extract_location("no file references at all") is None


class TestTracebackSplit:
    def test_test_and_production_split(self) -> None:
        text = (
            'File "/home/runner/repo/tests/db/test_pg_pool_config.py", line 184, '
            "in test_pg_pool_config_reaches_pool\n"
            "    pool = build_pool_config()\n"
            'File "/home/runner/repo/src/nse/db/postgres.py", line 214, in build_pool_config\n'
            "    raise ValueError('configured password does not match pool password')"
        )
        test_loc, prod_loc, err = parse_pytest_traceback(text)
        assert test_loc is not None and test_loc.path.endswith("test_pg_pool_config.py")
        assert test_loc.line == 184
        assert prod_loc is not None and prod_loc.path.endswith("postgres.py")
        assert prod_loc.line == 214
        assert prod_loc.function == "build_pool_config"
        assert err == "ValueError"

    def test_test_only_traceback(self) -> None:
        text = (
            'File "/home/runner/repo/tests/unit/test_a.py", line 12, in test_a\n    assert 1 == 2'
        )
        test_loc, prod_loc, _ = parse_pytest_traceback(text)
        assert test_loc is not None
        assert prod_loc is None

    def test_malformed_returns_unknown(self) -> None:
        test_loc, prod_loc, err = parse_pytest_traceback("complete garbage")
        assert test_loc is None
        assert prod_loc is None
        assert err == "unknown"

    def test_empty(self) -> None:
        assert parse_pytest_traceback("") == (None, None, "unknown")


class TestErrorType:
    def test_prefix(self) -> None:
        assert extract_error_type("AssertionError: x != y") == "AssertionError"

    def test_dotted(self) -> None:
        assert extract_error_type("psycopg.errors.OperationalError: could not connect") == (
            "psycopg.errors.OperationalError"
        )

    def test_none(self) -> None:
        assert extract_error_type("just a message") == "unknown"

    def test_function(self) -> None:
        assert extract_function('File "a.py", line 3, in do_thing') == "do_thing"
        assert extract_function("nothing") == "unknown"


class TestExtractLocations:
    def test_multiple_unique(self) -> None:
        text = "src/a.py:1 error\nsrc/a.py:1 again\nsrc/b.py:2 ok"
        locs = extract_locations(text)
        assert len(locs) == 2
        assert {l.path for l in locs} == {"src/a.py", "src/b.py"}
