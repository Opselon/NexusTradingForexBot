"""Agent-12 Wave-2: exit-classification INV-012 hardening (BUG-250)."""

from nexus_scalp.experience.outcome_recovery import classify_exit_with_evidence


def _base(**o):
    k = dict(
        deal_reason_code=0,
        comment="",
        profit_usd=-12.34,
        exit_price=4392.0,
        tp_price=0.0,
        sl_price=0.0,
        final_sl=0.0,
        entry_price=4400.0,
        was_sl_modified=False,
        direction="BUY",
    )
    k.update(o)
    return k


class TestBug250ReasonZeroPromotion:
    def test_profit_alone_does_not_promote_to_manual(self):
        r, src, _d, conf = classify_exit_with_evidence(**_base())
        assert r == "UNKNOWN"
        assert src == "FALLBACK_HEURISTIC"
        assert conf <= 0.3

    def test_comment_corroboration_promotes_to_manual(self):
        r, src, *_ = classify_exit_with_evidence(**_base(comment="client close from mobile"))
        assert r == "MANUAL_CLOSE"
        assert src == "BROKER_DEAL_REASON"

    def test_sl_geometry_corroboration_promotes_to_manual(self):
        r, src, *_ = classify_exit_with_evidence(
            **_base(profit_usd=-5.0, exit_price=4388.05, sl_price=4388.0, final_sl=4388.0)
        )
        assert r == "MANUAL_CLOSE"
        assert src == "BROKER_DEAL_REASON"
