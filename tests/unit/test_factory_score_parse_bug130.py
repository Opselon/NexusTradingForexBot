"""Regression: _load_elite / build_memory must tolerate registry rows whose
score column is a JSON TEXT string (BUG-130 AttributeError 'str' object has
no attribute 'get').

list_registry row-safe normalization keeps JSON columns as text; the factory
orchestrator previously called (e.get("score") or {}).get("verdict") which
crashed on str. The _score_dict helper normalizes both shapes.
"""

from __future__ import annotations

import importlib

from nexus_scalp.strategies.factory.orchestrator import _score_dict


class TestScoreDictNormalization:
    def test_dict_score_passthrough(self) -> None:
        assert _score_dict({"score": {"verdict": "VALIDATED", "final_score": 0.7}}) == {
            "verdict": "VALIDATED",
            "final_score": 0.7,
        }

    def test_json_string_score_parsed(self) -> None:
        entry = {"score": '{"verdict": "VALIDATED", "final_score": 0.75}'}
        assert _score_dict(entry)["verdict"] == "VALIDATED"
        assert float(_score_dict(entry)["final_score"]) >= 0.6

    def test_null_and_missing_are_empty(self) -> None:
        assert _score_dict({"score": None}) == {}
        assert _score_dict({}) == {}
        assert _score_dict({"score": "null"}) == {}
        assert _score_dict({"score": ""}) == {}

    def test_bad_json_returns_empty(self) -> None:
        assert _score_dict({"score": "{not-json"}) == {}

    def test_orchestrator_module_imports_clean(self) -> None:
        # The helper lives at module level; importing must not raise.
        importlib.import_module("nexus_scalp.strategies.factory.orchestrator")
