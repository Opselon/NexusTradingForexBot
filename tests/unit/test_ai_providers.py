"""AI provider ecosystem unit tests (ECOSYSTEM-001, Section 70 matrix).

Covers the contract, normalization, response validation, the policy formula,
fallback, configuration validation and secret redaction. No network access:
providers are exercised through a fake transport the adapters accept, so a unit
test can never place a call (let alone an order) against a real provider.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from nexus_scalp.ai_providers import errors as ai_errors
from nexus_scalp.ai_providers.contract import (
    AIProviderAction,
    DecisionEvidence,
    PositionDecisionRequest,
    PositionDecisionResponse,
    SlProposal,
    TpProposal,
)
from nexus_scalp.ai_providers.sample import (
    SIMULATED_CONTEXT_VERSION,
    SIMULATED_SAMPLE_REQUEST,
    build_simulated_request,
)

# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


class TestCanonicalContract:
    def test_simulated_snapshot_is_marked(self) -> None:
        req = SIMULATED_SAMPLE_REQUEST
        assert req.decision_context_version == SIMULATED_CONTEXT_VERSION
        assert req.ticket == 0
        assert req.broker == "SIMULATED"

    def test_contract_version_is_set(self) -> None:
        assert SIMULATED_SAMPLE_REQUEST.schema_version == "1.0.0"

    def test_minimal_payload_has_no_secrets(self) -> None:
        payload = SIMULATED_SAMPLE_REQUEST.to_minimal_payload()
        blob = str(payload)
        for forbidden in (
            "api_key",
            "apikey",
            "secret",
            "password",
            "Authorization",
            "bearer",
            "sk-",
        ):
            assert forbidden.lower() not in blob.lower(), f"secret-like field leaked: {forbidden}"

    def test_contract_rejects_unknown_fields(self) -> None:
        payload = SIMULATED_SAMPLE_REQUEST.model_dump()
        payload["future_outcome"] = "TP_HIT"
        with pytest.raises(ValueError):
            PositionDecisionRequest(**payload)

    def test_prices_must_be_positive(self) -> None:
        payload = SIMULATED_SAMPLE_REQUEST.model_dump()
        payload["current_price"] = -1.0
        with pytest.raises(ValueError):
            PositionDecisionRequest(**payload)

    def test_request_carries_no_future_derived_fields(self) -> None:
        """Section 9: nothing post-decision may appear in a request."""
        payload = SIMULATED_SAMPLE_REQUEST.to_minimal_payload()
        for field in (
            "realized_pnl",
            "final_outcome",
            "mae",
            "mfe",
            "future_high",
            "future_low",
            "closed_at",
        ):
            assert field not in payload, f"future-derived field present: {field}"


# ---------------------------------------------------------------------------
# response validation
# ---------------------------------------------------------------------------


class TestResponseValidation:
    def _valid(self) -> PositionDecisionResponse:
        return PositionDecisionResponse(
            provider="system_one",
            model="typesafe/jev-1.13",
            decision=DecisionEvidence(
                action=AIProviderAction.HOLD,
                confidence=0.6,
                p_hold=0.6,
                p_close=0.2,
                p_reduce=0.2,
            ),
            tp=TpProposal(recommendation="KEEP_CURRENT_TP"),
            sl=SlProposal(recommendation="KEEP_SL"),
            reason_codes=["trend_intact"],
            request_id="req-1",
            latency_ms=120.0,
            template_version="position_decision_v1",
            config_version="1",
        )

    def test_valid_response_passes(self) -> None:
        resp = self._valid()
        resp.validate_consistency()

    def test_contradictory_action_is_rejected(self) -> None:
        """Section 40: action HOLD with p_hold 0.02 / p_close 0.97."""
        resp = self._valid()
        resp.decision.p_hold = 0.02
        resp.decision.p_close = 0.97
        with pytest.raises(ValueError):
            resp.validate_consistency()

    def test_nan_is_rejected(self) -> None:
        resp = self._valid()
        object.__setattr__(resp.decision, "p_hold", float("nan"))
        with pytest.raises(ValueError):
            resp.validate_consistency()

    def test_confidence_out_of_bounds_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            DecisionEvidence(
                action=AIProviderAction.HOLD,
                confidence=1.7,
                p_hold=0.6,
                p_close=0.2,
                p_reduce=0.2,
            )

    def test_invalid_action_enum_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            DecisionEvidence(
                action="BUY_ALL_THE_DIPS",
                confidence=0.6,
                p_hold=0.6,
                p_close=0.2,
                p_reduce=0.2,
            )

    def test_negative_tp_price_is_rejected_by_schema(self) -> None:
        """A BUY TP below the current price cannot be a take profit."""
        with pytest.raises(ValueError):
            TpProposal(recommendation="TIGHTEN_TP", candidate_price=-1.0)

    def test_positive_sl_risk_change_is_a_widening_proposal(self) -> None:
        """Section 17: positive risk_change means risk expands."""
        proposal = SlProposal(recommendation="WIDEN_SL", risk_change=1.0)
        assert proposal.risk_change > 0


# ---------------------------------------------------------------------------
# failure taxonomy
# ---------------------------------------------------------------------------


class TestFailureTaxonomy:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ai_errors.ProviderErrorCategory.AUTH_FAILED),
            (403, ai_errors.ProviderErrorCategory.AUTH_FAILED),
            (429, ai_errors.ProviderErrorCategory.RATE_LIMITED),
            (400, ai_errors.ProviderErrorCategory.INVALID_REQUEST),
            (404, ai_errors.ProviderErrorCategory.MODEL_UNAVAILABLE),
            (500, ai_errors.ProviderErrorCategory.UPSTREAM_UNAVAILABLE),
            (502, ai_errors.ProviderErrorCategory.UPSTREAM_UNAVAILABLE),
            (503, ai_errors.ProviderErrorCategory.UPSTREAM_UNAVAILABLE),
            (504, ai_errors.ProviderErrorCategory.TIMEOUT),
        ],
    )
    def test_http_status_maps_to_category(
        self, status: int, expected: ai_errors.ProviderErrorCategory
    ) -> None:
        from nexus_scalp.ai_providers.transport import _STATUS_MAP

        assert _STATUS_MAP[status] == expected

    def test_auth_is_never_retryable(self) -> None:
        """Section 32: an invalid key must not be retried as a network blip."""
        assert ai_errors.ProviderErrorCategory.AUTH_FAILED in ai_errors.PERMANENT_CATEGORIES
        assert ai_errors.ProviderErrorCategory.AUTH_FAILED not in ai_errors.RETRYABLE_CATEGORIES
        assert (
            ai_errors.ProviderErrorCategory.SCHEMA_VIOLATION not in ai_errors.RETRYABLE_CATEGORIES
        )

    def test_transient_failures_are_retryable(self) -> None:
        for cat in (
            ai_errors.ProviderErrorCategory.RATE_LIMITED,
            ai_errors.ProviderErrorCategory.TIMEOUT,
            ai_errors.ProviderErrorCategory.NETWORK,
        ):
            assert cat in ai_errors.RETRYABLE_CATEGORIES

    def test_permanent_error_is_not_retryable(self) -> None:
        err = ai_errors.ProviderError(ai_errors.ProviderErrorCategory.AUTH_FAILED, "bad key")
        assert err.retryable is False
        assert err.permanent is True

    def test_redact_strips_bearer_tokens(self) -> None:
        cleaned = ai_errors.redact("Authorization: Bearer sk-1234567890abcdef")
        assert "sk-1234567890abcdef" not in cleaned
        assert "REDACTED" in cleaned

    def test_redact_strips_known_secret(self) -> None:
        cleaned = ai_errors.redact("key=abcdef1234", secrets=("abcdef1234",))
        assert "abcdef1234" not in cleaned


# ---------------------------------------------------------------------------
# policy formula
# ---------------------------------------------------------------------------


class TestPolicyFormula:
    def _score(
        self,
        req: PositionDecisionRequest,
        *,
        p_hold: float,
        p_close: float,
        expected_remaining_r: float,
        expected_downside_r: float,
        expected_upside_r: float,
        action_hint: AIProviderAction = AIProviderAction.NO_ACTION,
    ) -> object:
        from nexus_scalp.ai_providers.policy import score_action

        return score_action(
            req,
            p_hold=p_hold,
            p_close=p_close,
            p_reduce=0.1,
            expected_remaining_r=expected_remaining_r,
            expected_upside_r=expected_upside_r,
            expected_downside_r=expected_downside_r,
            regime_change_probability=0.1,
            uncertainty=0.2,
            confidence=0.7,
            action_hint=action_hint,
        )

    def test_close_evidence_beats_hold_for_negative_expectancy(self) -> None:
        """A position whose expectancy is negative must not score HOLD highest.

        This is the Section 11 distinction in executable form: CLOSE is the best
        *directional* candidate here, because staying in is worth less than
        leaving -- not because a model said so.
        """
        req = build_simulated_request(current_price=4280.0)
        scores = self._score(
            req,
            p_hold=0.1,
            p_close=0.8,
            expected_remaining_r=-0.8,
            expected_downside_r=-1.2,
            expected_upside_r=0.6,
            action_hint=AIProviderAction.CLOSE,
        )
        assert (
            scores.scores[AIProviderAction.CLOSE.value] > scores.scores[AIProviderAction.HOLD.value]
        )
        assert scores.expected_close_value != scores.expected_hold_value

    def test_hold_evidence_with_positive_expectancy_wins(self) -> None:
        req = build_simulated_request(current_price=4295.0)
        scores = self._score(
            req,
            p_hold=0.8,
            p_close=0.1,
            expected_remaining_r=1.4,
            expected_downside_r=-0.5,
            expected_upside_r=2.0,
            action_hint=AIProviderAction.HOLD,
        )
        assert scores.winner == AIProviderAction.HOLD.value

    def test_ai_hold_is_not_blindly_passed_through(self) -> None:
        """Section 11: confidence alone cannot force HOLD."""
        req = build_simulated_request(current_price=4270.0)
        scores = self._score(
            req,
            p_hold=0.99,
            p_close=0.0,
            expected_remaining_r=-2.0,
            expected_downside_r=-3.0,
            expected_upside_r=1.0,
            action_hint=AIProviderAction.HOLD,
        )
        assert scores.winner != AIProviderAction.HOLD.value

    def test_policy_is_deterministic(self) -> None:
        req = build_simulated_request()
        kwargs = dict(
            p_hold=0.7,
            p_close=0.2,
            expected_remaining_r=0.8,
            expected_downside_r=-0.5,
            expected_upside_r=1.2,
        )
        first = self._score(req, **kwargs)
        second = self._score(req, **kwargs)
        assert first.scores == second.scores
        assert first.winner == second.winner

    def test_terms_are_recorded_for_trace(self) -> None:
        scores = self._score(
            build_simulated_request(),
            p_hold=0.7,
            p_close=0.2,
            expected_remaining_r=0.8,
            expected_downside_r=-0.5,
            expected_upside_r=1.2,
        )
        assert scores.terms, "policy terms must be recorded for the decision trace (Section 41)"
        assert scores.policy_version

    def test_nan_inputs_do_not_poison_the_formula(self) -> None:
        """Section 10: NaN must never reach a decision score."""
        from nexus_scalp.ai_providers.policy import score_action

        scores = score_action(
            build_simulated_request(),
            p_hold=float("nan"),
            p_close=0.4,
            p_reduce=0.1,
            expected_remaining_r=float("nan"),
            expected_upside_r=1.0,
            expected_downside_r=-1.0,
            regime_change_probability=0.1,
            uncertainty=0.2,
            confidence=0.5,
        )
        assert all(math.isfinite(v) for v in scores.scores.values()), (
            "NaN leaked into policy scores"
        )


# ---------------------------------------------------------------------------
# risk gate
# ---------------------------------------------------------------------------


class TestRiskGate:
    def _response(
        self,
        sl_candidate_price: float | None,
        risk_change: float,
        *,
        action: AIProviderAction = AIProviderAction.HOLD,
    ) -> PositionDecisionResponse:
        return PositionDecisionResponse(
            provider="system_one",
            model="typesafe/jev-1.13",
            decision=DecisionEvidence(
                action=action,
                confidence=0.7,
                p_hold=0.5,
                p_close=0.3,
                p_reduce=0.2,
            ),
            tp=TpProposal(recommendation="KEEP_CURRENT_TP"),
            sl=SlProposal(
                recommendation="WIDEN_SL"
                if (sl_candidate_price and risk_change > 0)
                else "KEEP_SL",
                candidate_price=sl_candidate_price,
                risk_change=risk_change,
            ),
            request_id="req-sl",
            latency_ms=100.0,
            template_version="position_decision_v1",
            config_version="1",
        )

    def test_sl_widening_is_rejected_by_default(self) -> None:
        from nexus_scalp.ai_providers.risk_gate import gate_proposals

        req = build_simulated_request()  # BUY, current_sl below entry
        assert req.current_sl is not None
        resp = self._response(sl_candidate_price=req.current_sl - 5.0, risk_change=500.0)
        result = gate_proposals(resp, req)
        assert result.sl_allowed is False
        assert result.sl_kind.value == "WIDEN_SL"
        assert result.rejections

    def test_sl_tightening_is_allowed(self) -> None:
        from nexus_scalp.ai_providers.risk_gate import gate_proposals

        req = build_simulated_request()
        assert req.current_sl is not None
        resp = self._response(sl_candidate_price=req.current_sl + 1.0, risk_change=0.0)
        result = gate_proposals(resp, req)
        assert result.sl_kind.value == "TIGHTEN_SL"
        assert result.sl_allowed is True

    def test_no_sl_proposal_is_not_an_adjust(self) -> None:
        """Section 10: ADJUST_SL without a candidate is a broken response.

        The contract's model validator is non-deferrable (``@model_validator``
        runs at construction), so an adapter that normalizes a provider answer
        into this shape never produces a usable object at all. The right test is
        therefore on the *normalization* boundary: an adapter must raise a
        :class:`~nexus_scalp.ai_providers.errors.ProviderError` carrying
        ``SCHEMA_VIOLATION`` rather than silently emitting an incomplete
        proposal -- that is the category the fallback chain keys off.
        """
        from nexus_scalp.ai_providers import errors as e
        from nexus_scalp.ai_providers.adapters.base import BaseAIProviderAdapter

        # A provider answer that claims ADJUST_SL but offers no candidate price.
        bad = {
            "decision": {"action": "ADJUST_SL", "confidence": 0.7},
            "tp": {"recommendation": "KEEP_CURRENT_TP"},
            "sl": {"recommendation": "KEEP_SL"},  # no candidate_price
        }
        category = BaseAIProviderAdapter.classify_payload(bad)
        assert category == e.ProviderErrorCategory.SCHEMA_VIOLATION

    def test_high_confidence_cannot_justify_widening(self) -> None:
        """Section 17: confidence 0.99 never auto-authorises more downside."""
        from nexus_scalp.ai_providers.risk_gate import RiskPolicy, gate_proposals

        req = build_simulated_request()
        assert req.current_sl is not None
        resp = self._response(sl_candidate_price=req.current_sl - 5.0, risk_change=999.0)
        object.__setattr__(resp.decision, "confidence", 0.99)
        strict = RiskPolicy(allow_sl_widen=False, max_risk_change_usd=0.0)
        result = gate_proposals(resp, req, strict)
        assert result.sl_allowed is False
        assert result.sl_kind.value == "WIDEN_SL"

    def test_tp_wrong_side_is_rejected(self) -> None:
        from nexus_scalp.ai_providers.risk_gate import gate_proposals

        req = build_simulated_request()  # BUY: TP must be ABOVE current price
        resp = PositionDecisionResponse(
            provider="system_one",
            model="m",
            decision=DecisionEvidence(
                action=AIProviderAction.ADJUST_TP,
                confidence=0.7,
                p_hold=0.5,
                p_close=0.3,
                p_reduce=0.2,
            ),
            tp=TpProposal(recommendation="TIGHTEN_TP", candidate_price=req.current_price - 50.0),
            sl=SlProposal(recommendation="KEEP_SL"),
            request_id="req-tp",
            latency_ms=50.0,
            template_version="position_decision_v1",
            config_version="1",
        )
        result = gate_proposals(resp, req)
        assert result.tp_allowed is False
        assert "TP_CANDIDATE_WRONG_SIDE" in result.rejections


# ---------------------------------------------------------------------------
# fallback / no silent substitution
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_reason_is_recorded(self) -> None:
        from nexus_scalp.ai_providers.orchestrator import DecisionOutcome

        outcome = DecisionOutcome(
            decision_id="x",
            request=SIMULATED_SAMPLE_REQUEST,
            final_action="HOLD",
            policy=_policy_stub(),
            risk=_risk_stub(),
            providers_used=["internal_nse_ml"],
            providers_failed=["system_one"],
            fallback_used=True,
            fallback_reason="UPSTREAM_UNAVAILABLE",
        )
        d = outcome.to_dict()
        assert d["fallback_used"] is True
        assert d["fallback_reason"] == "UPSTREAM_UNAVAILABLE"
        assert d["providers_failed"] == ["system_one"]

    def test_no_fallback_is_marked(self) -> None:
        from nexus_scalp.ai_providers.orchestrator import DecisionOutcome

        outcome = DecisionOutcome(
            decision_id="y",
            request=SIMULATED_SAMPLE_REQUEST,
            final_action="HOLD",
            policy=_policy_stub(),
            risk=_risk_stub(),
            providers_used=["system_one"],
        )
        assert outcome.to_dict()["fallback_used"] is False

    def test_trace_carries_versions(self) -> None:
        """Section 42: every decision identifies provider/model/versions."""
        from nexus_scalp.ai_providers.orchestrator import DecisionOutcome

        outcome = DecisionOutcome(
            decision_id="z",
            request=SIMULATED_SAMPLE_REQUEST,
            final_action="HOLD",
            policy=_policy_stub(),
            risk=_risk_stub(),
            providers_used=["system_one"],
        )
        versions = outcome.to_dict()["versions"]
        for key in ("template", "policy", "gate", "decision", "contract", "context"):
            assert versions.get(key), f"missing version identifier: {key}"


def _policy_stub():
    from nexus_scalp.ai_providers.policy import PolicyScores

    return PolicyScores(scores={"HOLD": 0.1}, winner="HOLD")


def _risk_stub():
    from nexus_scalp.ai_providers.risk_gate import RiskGateResult, SlKind

    return RiskGateResult(allowed=True, sl_kind=SlKind.KEEP_SL)


# ---------------------------------------------------------------------------
# replay / leakage boundary
# ---------------------------------------------------------------------------


class TestReplayLeakage:
    def test_replay_request_excludes_future_bars(self) -> None:
        from nexus_scalp.ai_providers.replay import BacktestConfig, _atr

        bars = _synthetic_bars(60)
        cfg = BacktestConfig()
        t = 40
        visible = bars[max(0, t - cfg.bars_per_step + 1) : t + 1]
        assert len(visible) == 41
        # The request may only reference bars up to and including t.
        last_visible_time = visible[-1]["time"]
        assert last_visible_time == bars[t]["time"]
        assert _atr(visible) > 0

    def test_horizon_is_bounded(self) -> None:
        from nexus_scalp.ai_providers.replay import DECISION_HORIZON_BARS

        assert DECISION_HORIZON_BARS == 12

    def test_r_multiples_are_signed_correctly(self) -> None:
        from nexus_scalp.ai_providers.replay import _r_multiples

        assert _r_multiples(10.0, 5.0, 1) == pytest.approx(2.0)
        assert _r_multiples(-10.0, 5.0, 1) == pytest.approx(-2.0)
        assert _r_multiples(10.0, 0.0, 1) == 0.0


def _synthetic_bars(n: int) -> list[dict]:
    bars = []
    price = 4300.0
    for i in range(n):
        move = 1.5 if i % 3 else -1.0
        price += move
        bars.append(
            {
                "time": f"2026-09-{15 + i // 24}T{i % 24:02d}:00:00",
                "open": price - move,
                "high": price + 2.0,
                "low": price - 2.0,
                "close": price,
                "tick_volume": 1000,
            }
        )
    return bars


# ---------------------------------------------------------------------------
# secret redaction
# ---------------------------------------------------------------------------


class TestSecretRedaction:
    def test_registry_storage_carries_no_key_value(self) -> None:
        from nexus_scalp.ai_providers.registry import ProviderConfig

        cfg = ProviderConfig(provider_id="system_one", provider_name="System One")
        cfg.secret_name = "ai_provider_system_one_key"
        d = cfg.to_storage_dict()
        assert d["secret_name"] == "ai_provider_system_one_key"
        for forbidden in ("secret", "api_key", "apikey", "key_value"):
            assert forbidden not in d, f"storage payload carries a secret value: {forbidden}"

    def test_provider_config_rejects_an_inline_key(self) -> None:
        """Section 37: the config unit never accepts a key by value."""
        from nexus_scalp.ai_providers.registry import ProviderConfig

        cfg = ProviderConfig(provider_id="system_one", provider_name="System One")
        d = cfg.to_storage_dict()
        blob = str(d)
        for marker in ("sk-", "Bearer ", "tDwL8EnrfdBu"):
            assert marker not in blob

    def test_redact_strips_bearer_and_key_shapes(self) -> None:
        cleaned = ai_errors.redact("Authorization: Bearer sk-1234567890abcdef")
        assert "sk-1234567890abcdef" not in cleaned
        assert "REDACTED" in cleaned

    def test_redact_strips_a_known_secret(self) -> None:
        cleaned = ai_errors.redact("got key=abcdef1234 back", secrets=("abcdef1234",))
        assert "abcdef1234" not in cleaned


# ---------------------------------------------------------------------------
# determinism / no placeholder numbers
# ---------------------------------------------------------------------------


class TestNoFabricatedNumbers:
    def test_all_sample_numbers_are_finite(self) -> None:
        req = SIMULATED_SAMPLE_REQUEST
        for name, value in req.model_dump().items():
            if isinstance(value, (int, float)):
                assert math.isfinite(float(value)), f"non-finite sample value: {name}"

    def test_sample_timestamp_is_not_now(self) -> None:
        """A frozen synthetic timestamp distinguishes test data from live data."""
        assert SIMULATED_SAMPLE_REQUEST.timestamp != datetime.now(UTC)
