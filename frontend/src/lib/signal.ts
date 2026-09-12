/**
 * Decision humanizer — port of the main dashboard's NXSignal (Web/ux_signal.js,
 * CHG-0048). Presentation-layer ONLY.
 *
 * Two layers, same rules as the legacy module:
 *   LAYER 1 (simple): BUY / SELL / NO TRADE in plain language + a human "why".
 *   LAYER 2 (detail):  the raw reason code + confidence the technical operator
 *                      already had.
 *
 * TRUTH RULES (identical to the main UI):
 *   - Confidence 0.0 on a Guardian-blocked decision is NOT "0% confident of
 *     no-trade" — the model was NOT CONSULTED; say that, never render a fake
 *     0.0% as a real confidence.
 *   - Reasons translate by EXACT-MATCH table; unknown codes fall back to the
 *     raw code verbatim (never invented explanations).
 */

export interface SignalExplain {
  decision: string;
  tone: "buy" | "sell" | "hold";
  label: string;
  human: string | null;
  detail: string | null;
  confidenceKind: "value" | "not_consulted" | "none";
  confidenceText: string;
  confidenceRaw: number | null;
  freshness: string | null;
}

interface ExplainInput {
  ai_decision: string | null | undefined;
  ai_confidence: number | null | undefined;
  ai_reason: string | null | undefined;
  live_freshness?: {
    market?: { state?: string | null };
    inference?: { state?: string | null };
  } | null;
}

type Translator = (key: string, fallback: string) => string;

/** reason code -> {simple, detail} — verbatim parity with Web/ux_signal.js. */
export const REASONS: Record<string, { key?: string; simple: string; detail: string }> = {
  BLOCKED_BY_GUARDIAN_UNSAFE_REGIME: {
    key: "ux.reason.BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
    simple: "Market conditions are currently not safe enough to enter. The engine stood aside.",
    detail: "The regime guardian classified the market as unsafe; the model was not consulted (no confidence is produced).",
  },
  CONFIDENCE_GATE: {
    key: "ux.reason.CONFIDENCE_GATE",
    simple: "The model saw an opportunity but its confidence stayed below the required level.",
    detail: "The candidate was filtered by the confidence gate before reaching execution.",
  },
  NO_CANDIDATE: {
    simple: "No qualifying trade setup at this moment.",
    detail: "No setup passed the candidate filters.",
  },
  HIGH_IMPACT_NEWS: {
    simple: "High-impact news window — trading paused for safety.",
    detail: "News risk governor blocked the decision during a high-impact release.",
  },
};

const DECISION_LABEL_KEYS: Record<string, string> = {
  NO_TRADE: "ux.signal.no_trade",
  BUY: "ux.signal.buy",
  SELL: "ux.signal.sell",
  WAIT: "ux.signal.wait",
};

/** explain() — returns null when there is no decision to explain (never a
 *  fabricated "NO TRADE": absence renders as UNKNOWN upstream). */
export function explainSignal(payload: ExplainInput | undefined, t: Translator): SignalExplain | null {
  if (!payload || payload.ai_decision == null) return null;
  const decision = String(payload.ai_decision);
  const reason = payload.ai_reason != null ? String(payload.ai_reason) : "";
  const out: SignalExplain = {
    decision,
    tone: decision === "BUY" ? "buy" : decision === "SELL" ? "sell" : "hold",
    label: t(DECISION_LABEL_KEYS[decision] ?? "", decision),
    human: null,
    detail: null,
    confidenceKind: "none",
    confidenceText: "—",
    confidenceRaw: payload.ai_confidence ?? null,
    freshness: null,
  };

  const known = REASONS[reason] ?? null;
  if (known) {
    out.human = known.key ? t(known.key, known.simple) : known.simple;
    out.detail = known.detail;
  } else if (reason) {
    out.human = null; // unknown code: show it verbatim in the detail layer, invent nothing
    out.detail = reason;
  }

  // Confidence semantics: 0.0 with a non-gate block means NOT CONSULTED.
  const conf = payload.ai_confidence;
  if (conf == null) {
    out.confidenceKind = "none";
    out.confidenceText = "—";
  } else if (conf === 0 && reason && reason !== "CONFIDENCE_GATE") {
    out.confidenceKind = "not_consulted";
    out.confidenceText = t("ux.signal.not_available", "Signal not available");
  } else {
    out.confidenceKind = "value";
    out.confidenceText = `${(conf * 100).toFixed(1)}%`;
  }

  // Freshness is part of the signal experience (§16 lineage).
  const lf = payload.live_freshness ?? {};
  const infresh = lf.inference?.state ?? lf.market?.state ?? null;
  out.freshness = infresh ? String(infresh) : null;
  return out;
}
