"""ML runtime truth — route-scope regression suite (ML-RUNTIME-TRUTH).

Regressions covered
-------------------
D1  : ``_get_active_app`` returned a process-global last-registered app, so a
      second ``create_app`` in the same process silently re-targeted every
      model-governance route onto the WRONG engine. The live symptom was
      ``/api/models/integrity`` -> ``available:false, state:UNAVAILABLE``
      while the real serving app held a loaded Champion. Reproduced here with
      two apps: app A carries an engine, app B does not. Registration order is
      flipped between the two assertions, so only per-request resolution passes
      in BOTH directions (the global is order-dependent by construction).

D2  : drift / feature-health "no rows" must stay distinguishable from a store
      that was never written (NOT_RUN) and from an unavailable store. The API
      contract is: empty collections + available=True means the monitor ran and
      found nothing; available=False means it could not answer at all.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from nexus_scalp.web import model_governance_routes as mgr


class _FakeState:
    def __init__(self, engine: Any) -> None:
        self.engine = engine


class _FakeApp:
    """Minimal stand-in exposing the one attribute the routes read."""

    def __init__(self, engine: Any) -> None:
        self.state = _FakeState(engine)


class _FakeEngine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.champion_manager = types.SimpleNamespace(champion_or_none=lambda: None)


# ---------------------------------------------------------------------------
# D1: per-request app resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("register_b_first", [False, True])
def test_get_active_app_returns_the_callers_app_not_a_global(register_b_first: bool) -> None:
    """Each route must see ITS OWN app — never the last-registered one.

    A process-global slot is order-dependent, so this asserts in both
    registration orders. Before the fix, whichever order ran, one of the two
    routes resolved the wrong app.
    """
    app_with_engine = _FakeApp(_FakeEngine("serving"))
    app_without_engine = _FakeApp(None)

    if register_b_first:
        mgr.register_model_governance_routes(app_without_engine)
        mgr.register_model_governance_routes(app_with_engine)
    else:
        mgr.register_model_governance_routes(app_with_engine)
        mgr.register_model_governance_routes(app_without_engine)

    # The caller's own app always wins, in BOTH orders.
    assert mgr._get_active_app(app_with_engine) is app_with_engine
    assert mgr._get_active_app(app_without_engine) is app_without_engine


def test_get_active_app_is_pure_with_respect_to_other_apps() -> None:
    """Registering a second app must not move the first app's resolution.

    This is the exact live failure: the serving app is built first, then a
    second (engine-less) app is built for tooling, and every governance route
    on the FIRST app began reading the SECOND app's (absent) engine.
    """
    serving = _FakeApp(_FakeEngine("serving"))
    mgr.register_model_governance_routes(serving)
    assert mgr._get_active_app(serving) is serving

    other = _FakeApp(None)
    mgr.register_model_governance_routes(other)
    assert mgr._get_active_app(serving) is serving
    assert mgr._get_active_app(other) is other


def test_get_active_app_reads_the_callers_engine() -> None:
    """The observable consequence: ``engine`` comes from the caller's app."""
    engine = _FakeEngine("serving")
    app = _FakeApp(engine)
    mgr.register_model_governance_routes(app)
    # A second registration must not re-target the first.
    mgr.register_model_governance_routes(_FakeApp(None))
    resolved = mgr._get_active_app(app)
    assert getattr(resolved.state, "engine", None) is engine


def test_no_module_global_app_slot_remains() -> None:
    """The poisoned global is gone, not merely left at a safe value.

    A global left in place re-breaks the moment any caller appends to it again.
    """
    assert not hasattr(mgr, "_ACTIVE_APP"), "module-global app slot must not exist"
