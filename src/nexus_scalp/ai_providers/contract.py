"""Canonical AI decision contract for position management (ECOSYSTEM-001).

One request contract, one response contract, versioned. Every provider speaks
this and nothing else, so the ProviderOrchestrator never knows an HTTP detail
about any of them (Section 2: Position Manager knows PROVIDER, not SYSTEMONE
HTTP DETAILS).

DATA LEAKAGE AUDIT (Section 9) -- the most important property of this file.
Every field below must be *provably* available at the decision timestamp. A
build-time validator rejects the whole request if a future-derived name is ever
added. Never extend :class:`PositionDecisionRequest` without recording causal
provenance:

    entry/current/bid/ask/spread       live quote stream
    volume/age/unrealized_pnl/tp/sl    broker position record (authoritative)
    atr/volatility/momentum/trend/     closed bars ONLY -- this is the horizon
      regime/market_structure            boundary; a forming bar's future
                                         high/low is future data
    session/liquidity/news_state       calendar + decision clock (no free text)
    balance/equity/margin/leverage     MT5 account, sole source of truth
    model_confidence/prediction        internal model output at this tick
    tick_size/digits/stops_level       symbol session contract
    timestamp/data_freshness           the decision clock itself

FORBIDDEN in this contract (build-time rejection, not a review note):
    future_return, future_r, best/worst_future_r, mae_usd, mfe_usd,
    time_to_mfe/mae, continuation_*, optimal_action, close_now_net_r,
    realized_pnl, final_outcome, and any post-close information.

Those names belong to the position_replay LABEL vocabulary: the trainer reads
them to produce labels; the serving contract may never send them.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CONTRACT_VERSION",
    "AIProviderAction",
    "DecisionEvidence",
    "PositionDecisionRequest",
    "PositionDecisionResponse",
    "RISK_EXPANDING_ACTIONS",
    "SlProposal",
    "TpProposal",
    "is_finite_prob",
]

#: Version of the request/response contract itself. Bumping this is breaking:
#: persisted responses carry it and replay refuses a mismatched one.
CONTRACT_VERSION = "1.0.0"

#: Names no provider may ever see, and no response may ever claim to compute.
_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "future_return", "future_r", "future_r_net", "best_future_r",
        "worst_future_r", "mae_usd", "mfe_usd", "time_to_mfe", "time_to_mae",
        "continuation_value", "continuation_net_r", "continuation_mfe_r",
        "continuation_mae_r", "horizon_continuation_value",
        "optimal_action", "close_now_net_r", "realized_pnl", "final_outcome",
    }
)


class AIProviderAction(StrEnum):
    """The six mutually-exclusive actions a provider may recommend.

    ``NO_ACTION`` is deliberately distinct from ``HOLD`` (Section 18): HOLD
    means "continuation is favourable", NO_ACTION means "insufficient evidence
    to opine". The policy scores them differently, so a provider declining to
    opine is never silently converted into an endorsement of the status quo.
    """

    HOLD = "HOLD"
    CLOSE = "CLOSE"
    REDUCE = "REDUCE"
    NO_ACTION = "NO_ACTION"
    ADJUST_TP = "ADJUST_TP"
    ADJUST_SL = "ADJUST_SL"


#: Actions whose only effect could be to increase realized risk. The risk
#: engine refuses these unless an explicit policy override exists (Section 17).
RISK_EXPANDING_ACTIONS: frozenset[AIProviderAction] = frozenset(
    {AIProviderAction.REDUCE, AIProviderAction.ADJUST_SL}
)


def is_finite_prob(value: object) -> bool:
    """True only for a real, non-bool, finite number within [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    f = float(value)
    return not (math.isnan(f) or math.isinf(f)) and 0.0 <= f <= 1.0


class PositionDecisionRequest(BaseModel):
    """Canonical, minimal, provider-agnostic position decision request.

    MINIMAL-DATA PRINCIPLE (Section 8): only what a provider needs in order to
    opine on hold/close/reduce/TP/SL -- not a dump of the internal database.
    Carries no credentials, no filesystem paths, no broker secrets, no raw DB
    rows; the orchestrator serializes this model and nothing else on the wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(default=CONTRACT_VERSION)

    # -- POSITION (broker is the source of truth; Section 51) ----------------
    position_id: str = Field(..., description="Stable internal position identifier")
    ticket: int = Field(..., ge=0, description="Broker ticket, 0 when synthetic")
    symbol: str = Field(..., min_length=1, max_length=64)
    side: str = Field(..., pattern="^(BUY|SELL)$")
    entry_price: float = Field(..., gt=0.0)
    current_price: float = Field(..., gt=0.0)
    average_price: float = Field(..., gt=0.0)
    volume: float = Field(..., gt=0.0)
    position_age_sec: float = Field(..., ge=0.0)
    unrealized_pnl: float
    unrealized_r: float
    current_tp: float | None = None
    current_sl: float | None = None

    # -- MARKET (the horizon boundary: closed bars only) ---------------------
    bid: float = Field(..., gt=0.0)
    ask: float = Field(..., gt=0.0)
    spread: float = Field(..., ge=0.0)
    spread_relative: float = Field(..., ge=0.0)
    volatility: float = Field(..., ge=0.0)
    atr: float = Field(..., ge=0.0)
    momentum: float
    trend: str
    market_structure: str
    regime: str
    regime_confidence: float = Field(..., ge=0.0, le=1.0)
    session: str
    liquidity_state: str
    #: Scheduled calendar facts only -- NEVER scraped free text, which would be
    #: an unguarded prompt-injection surface (Section 39).
    news_state: str = ""

    # -- ACCOUNT (MT5 authoritative; a provider cannot set these) ------------
    balance: float
    equity: float
    margin: float = Field(..., ge=0.0)
    free_margin: float
    margin_level: float = Field(..., ge=0.0)
    leverage: int = Field(..., ge=1)
    account_currency: str = Field(..., min_length=3, max_length=3)

    # -- RISK ----------------------------------------------------------------
    current_risk: float = Field(..., ge=0.0)
    max_allowed_risk: float = Field(..., ge=0.0)
    risk_budget: float = Field(..., ge=0.0)
    distance_to_sl: float = Field(..., ge=0.0)
    distance_to_tp: float = Field(..., ge=0.0)
    reward_to_risk: float
    exposure: float = Field(..., ge=0.0)

    # -- MODEL (internal evidence, sent so a provider can *dis*agree) --------
    model_confidence: float = Field(..., ge=0.0, le=1.0)
    model_prediction: str = ""

    # -- EXECUTION CONSTRAINTS (proposals must respect these) ----------------
    broker: str = ""
    server: str = ""
    tick_size: float = Field(..., gt=0.0)
    digits: int = Field(..., ge=0, le=8)
    volume_step: float = Field(..., gt=0.0)
    stops_level: float = Field(..., ge=0.0)
    freeze_level: float = Field(..., ge=0.0)

    # -- SYSTEM --------------------------------------------------------------
    timestamp: datetime
    data_freshness_sec: float = Field(..., ge=0.0)
    provider_request_id: str = Field(..., min_length=1)
    decision_context_version: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _audit_future_information(self) -> PositionDecisionRequest:
        """Refuse to build a request carrying future-derived information.

        Build-time half of the leakage defence; the wire-level half is the
        ``extra='forbid'`` schema that stops an undeclared field reaching a
        provider even if one were added internally.
        """
        leaked = sorted(f for f in _FORBIDDEN_FIELDS if f in type(self).model_fields)
        if leaked:
            raise ValueError(f"PositionDecisionRequest leaks future information: {leaked}")
        return self

    def to_minimal_payload(self) -> dict[str, Any]:
        """The wire payload: frozen fields only, nothing extra, nothing secret."""
        return self.model_dump(mode="json")


class TpProposal(BaseModel):
    """A take-profit candidate. A *proposal*, never an instruction."""

    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(..., min_length=1)
    candidate_price: float | None = Field(default=None, gt=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SlProposal(BaseModel):
    """A stop-loss candidate, treated more conservatively than TP (Section 17).

    ``risk_change`` is signed: positive means the proposal would INCREASE the
    dollars at risk. The risk engine rejects a positive ``risk_change`` unless
    an explicit policy override exists -- confidence 0.99 never justifies a
    wider stop, because confidence is not evidence about broker-side consequences.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(..., min_length=1)
    candidate_price: float | None = Field(default=None, gt=0.0)
    #: Signed change in dollar risk this proposal implies. >0 = risk expands.
    risk_change: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DecisionEvidence(BaseModel):
    """The normalized probability/expectancy view of one provider.

    All slots are validated finite-and-bounded (Section 10). A contradictory
    response -- action HOLD with p_hold 0.02 and p_close 0.97 -- is REJECTED by
    :meth:`PositionDecisionResponse.validate_consistency`, never silently
    scored, because scoring it would launder a broken response into a decision.
    """

    model_config = ConfigDict(extra="forbid")

    action: AIProviderAction
    confidence: float = Field(..., ge=0.0, le=1.0)
    p_hold: float = Field(default=0.0, ge=0.0, le=1.0)
    p_close: float = Field(default=0.0, ge=0.0, le=1.0)
    p_reduce: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_remaining_r: float = 0.0
    expected_downside_r: float = 0.0
    expected_upside_r: float = 0.0
    regime_change_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Free-text rationale. UNTRUSTED (Section 39): rendered for the operator,
    #: never parsed for control flow, never allowed to override the schema.
    rationale: str = Field(default="", max_length=4000)


class PositionDecisionResponse(BaseModel):
    """Canonical normalized provider response (Section 10).

    Built ONLY by adapter classes, never hand-assembled from a raw payload.
    Failing validation records ``SCHEMA_VIOLATION`` and engages the fallback
    chain -- the provider being reachable does not make its answer usable.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=CONTRACT_VERSION)
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    decision: DecisionEvidence
    tp: TpProposal
    sl: SlProposal
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # -- Provenance / non-repudiation ----------------------------------------
    #: Provider's own generation id when exposed (System One: ``id``).
    provider_generation_id: str = ""
    request_id: str = Field(..., min_length=1)
    latency_ms: float = Field(..., ge=0.0)
    template_version: str = Field(..., min_length=1)
    config_version: str = Field(..., min_length=1)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_estimate: float | None = None
    #: True only when the response came from a real provider round trip.
    from_live_provider: bool = True
    raw_response_summary: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_consistency(self) -> PositionDecisionResponse:
        """Reject contradictory, impossible, or out-of-contract responses.

        Section 40: missing required fields, an invalid action, NaN, values
        outside their ranges, or self-contradictory probabilities are all
        rejected. This is where the ecosystem stops trusting a live provider.
        """
        d = self.decision

        # 1. Action must be one of the six declared values.
        if not isinstance(d.action, AIProviderAction):
            raise ValueError(f"invalid action in response: {d.action!r}")

        # 2. NaN/Inf sweep across every numeric slot (Section 10 explicitly
        #    names NaN and Infinity as rejectable defects).
        for name in (
            "confidence", "p_hold", "p_close", "p_reduce",
            "expected_remaining_r", "expected_downside_r", "expected_upside_r",
            "regime_change_probability", "uncertainty",
        ):
            v = getattr(d, name)
            if isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError(f"non-numeric value in response field {name!r}: {v!r}")
            if math.isnan(float(v)) or math.isinf(float(v)):
                raise ValueError(f"non-finite value in response field {name!r}: {v!r}")

        # 3. Probability simplex: p_hold + p_close + p_reduce must be ~1.0.
        #    A model that emits 0.9/0.9/0.9 has not produced probabilities.
        total = d.p_hold + d.p_close + d.p_reduce
        if not math.isfinite(total) or abs(total - 1.0) > 0.05:
            raise ValueError(
                f"response probabilities do not sum to 1 (got {total:.4f}): "
                f"p_hold={d.p_hold}, p_close={d.p_close}, p_reduce={d.p_reduce}"
            )

        # 4. The named action must be compatible with the simplex. This is the
        #    Section-40 contradiction test: HOLD with p_close=0.97 is a broken
        #    response, not a differing opinion, and must not be scored.
        probs = {
            AIProviderAction.HOLD: d.p_hold,
            AIProviderAction.CLOSE: d.p_close,
            AIProviderAction.REDUCE: d.p_reduce,
        }
        if d.action in probs:
            argmax = max(probs, key=probs.get)
            if d.action is not argmax:
                raise ValueError(
                    f"contradictory response: action={d.action.value} but argmax(p)="
                    f"{argmax.value} (p_hold={d.p_hold}, p_close={d.p_close}, "
                    f"p_reduce={d.p_reduce})"
                )
            # 5. An endorsement must be a real endorsement: a named action with
            #    a token probability is an uncalibrated guess wearing a label.
            need = {AIProviderAction.CLOSE: 0.34, AIProviderAction.HOLD: 0.34,
                    AIProviderAction.REDUCE: 0.20}[d.action]
            got = probs[d.action]
            if got < need:
                raise ValueError(
                    f"{d.action.value} recommended with probability {got:.4f} "
                    f"(needs >= {need})"
                )

        # 6. An ADJUST_* action must carry the proposal it is about.
        if d.action is AIProviderAction.ADJUST_TP and self.tp.candidate_price is None:
            raise ValueError("ADJUST_TP without a TP candidate price")
        if d.action is AIProviderAction.ADJUST_SL and self.sl.candidate_price is None:
            raise ValueError("ADJUST_SL without a SL candidate price")

        # 7. TP and SL cannot be the same price -- that is not a bracket.
        if self.tp.candidate_price is not None and self.sl.candidate_price is not None:
            if abs(self.tp.candidate_price - self.sl.candidate_price) < 1e-12:
                raise ValueError("TP and SL candidate prices are identical")

        return self
