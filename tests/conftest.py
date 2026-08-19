"""Repository-wide pytest fixture registration (TASK-06-70D-LIQUIDITY-OPTIMIZATION).

The 70D shadow suites (TASK-05-70D-SHADOW, parallel agent) declare pytest
fixtures in ``tests/helpers/shadow70_fixtures.py`` (``contract``,
``tmp_artifacts``). Without a conftest, pytest never discovers fixtures
defined in plain helper modules, so every test requesting ``contract``
ERRORs with "fixture 'contract' not found".

This conftest imports + registers those helpers fixtures so the standard
repo gate (``pytest tests/unit``) can collect them. Purely additive; no
production code touched.
"""

from __future__ import annotations

import pytest

# Pull fixture definitions into this conftest's namespace so pytest's
# fixture manager sees them repo-wide (testpaths=tests).
from tests.helpers.shadow70_fixtures import contract, tmp_artifacts  # noqa: F401

pytest.register_assert_rewrite("tests.helpers.shadow70_fixtures")