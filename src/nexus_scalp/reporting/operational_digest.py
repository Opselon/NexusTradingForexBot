"""Operational digest builder (mission item 5 — ONE meaningful daily message).

Composes the daily Telegram digest from CANONICAL state only:
    - P&L / trades / expectancy: the existing daily PerformanceReportEngine
      (AccountingCore-backed, PAPER rows excluded by BUG-226 provenance)
    - Champion: ChampionManager (verified artifact identity)
    - Governance health: ModelGovernanceEngine
    - Risk breakers: RiskEngine._last_breaker (CircuitBreakerEngine)
    - Drift: FeatureDriftBreaker state (canonical shadow70 monitor)
    - Paper/Demo parity: build_parity_snapshot (observational)
    - Rollbacks / critical failures: governance + incident counters

The engine already sends the deep performance report daily (BUG-057 /
MaintenanceCycle); this digest is the COMPACT OPERATIONAL one — mode,
protections, learning-safety state, parity — designed to fit in one
message with zero noise. Never calls the broker; never fabricates values
(missing inputs render as "—", matching the existing notify_* honesty).
"""

from __future__ import annotations

from typing import Any


def _esc(v: Any) -> str:
    s = str(v if v is not None else "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v: Any, kind: str = "text") -> str:
    if v is None or v == "":
        return "—"
    if kind == "usd":
        try:
            return f"${float(v):,.2f}"
        except Exception:
            return "—"
    if kind == "pct":
        try:
            return f"{float(v):.2f}%"
        except Exception:
            return "—"
    if kind == "r":
        try:
            return f"{float(v):.4f} R"
        except Exception:
            return "—"
    return _esc(v)


def build_operational_digest(self: Any, container: Any | None = None) -> str:
    """One compact operator digest (HTML). Failure-safe: always returns text."""
    lines: list[str] = [
        "🛡 <b>NEXUS OPERATIONAL DIGEST</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]

    # --- Performance block (from the canonical daily report when supplied) ---
    if container is not None:
        try:
            p = container.performance
            r_s = container.r
            lines.append(
                f"📊 P&L: <b>{_fmt(p.net_pnl, 'usd')}</b> | Trades: <code>{p.trades}</code> "
                f"(W {p.wins} / L {p.losses} / BE {p.scratches})"
            )
            lines.append(
                f"🎯 Expectancy: <code>{_fmt(getattr(r_s, 'expectancy_r', None), 'r')}</code> | "
                f"PF: <code>{_fmt(getattr(p, 'profit_factor', None))}</code>"
            )
            dd = container.drawdown
            lines.append(
                f"📉 Drawdown: <code>{_fmt(getattr(dd, 'current_drawdown_pct', None), 'pct')}</code>"
            )
        except Exception:
            lines.append("📊 Performance: <i>report unavailable</i>")
    else:
        lines.append("📊 Performance: <i>not included</i>")

    # --- Mode + account ---
    lines.append(
        f"🎛 Mode: <code>{_esc(getattr(self, '_runtime_mode', '?'))}</code> | "
        f"Running: <code>{'yes' if getattr(self, '_running', False) else 'no'}</code>"
    )

    # --- Champion + governance ---
    try:
        cm = getattr(self, "champion_manager", None)
        champ = cm.champion_or_none() if cm is not None else None
        if champ is not None and getattr(champ, "available", False):
            lines.append(
                f"🏆 Champion: <code>{_esc(champ.model_id)}@{_esc(champ.model_version)}</code>"
            )
        else:
            lines.append("🏆 Champion: <code>unavailable</code>")
    except Exception:
        lines.append("🏆 Champion: <i>error</i>")

    # --- Risk protections (breakers) ---
    risk = getattr(self, "risk_engine", None)
    if risk is not None:
        lines.append(
            f"🛑 Kill switch: <code>{'ARMED' if getattr(risk, '_kill_switch_active', False) else 'off'}</code>"
        )
        br = getattr(risk, "_last_breaker", None)
        if br is not None:
            lines.append(
                f"⚡ Breakers: <code>{_esc(br.level)}</code> "
                f"(day {_fmt(br.daily_loss_pct, 'pct')} / week {_fmt(br.weekly_loss_pct, 'pct')}, "
                f"streak <code>{br.consecutive_losses}</code>)"
            )
        else:
            lines.append("⚡ Breakers: <i>not yet evaluated</i>")
    else:
        lines.append("🛑 Risk engine: <i>unavailable</i>")

    # --- Learning safety (drift) ---
    drift = getattr(self, "_feature_drift_breaker", None)
    if drift is not None:
        try:
            lines.append(f"🌊 Drift: <code>{_esc(drift.state())}</code>")
        except Exception:
            lines.append("🌊 Drift: <i>error</i>")
    else:
        lines.append("🌊 Drift: <i>not wired</i>")

    # --- Paper/Demo parity ---
    try:
        from nexus_scalp.risk.paper_parity import build_parity_snapshot

        snap = build_parity_snapshot(audit=self.audit, lookback_days=7)
        paper = snap.get("paper") or {}
        demo = snap.get("demo") or {}
        lines.append(
            f"🔁 Parity: <code>{_esc(snap.get('status'))}</code> "
            f"(paper fills {_fmt(paper.get('fills'))}, broker trades {_fmt(demo.get('trades'))})"
        )
    except Exception:
        lines.append("🔁 Parity: <i>unavailable</i>")

    # --- Rollbacks / critical failures (governance truth) ---
    try:
        gov = getattr(self, "governance_engine", None)
        if gov is not None:
            evs = gov.store.list_events(limit=50)
            rollbacks = sum(1 for e in evs if str(e.get("event", "")) == "ROLLBACK_EXECUTED")
            lines.append(f"⏪ Rollbacks (recent 50 events): <code>{rollbacks}</code>")
    except Exception:
        lines.append("⏪ Rollbacks: <i>unavailable</i>")

    import time as _t

    lines.append(f"🕒 {_t.strftime('%Y-%m-%d %H:%M:%S UTC', _t.gmtime())}")
    return "\n".join(lines)
