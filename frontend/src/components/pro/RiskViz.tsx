/**
 * RiskViz — pro guardian/risk visual components for the alternative console.
 *
 * HARD RULE: every verdict WORD rendered here is either a backend payload
 * string echoed verbatim (upper-cased for the console face), a 1:1 restatement
 * of a backend boolean/number, or the word UNKNOWN. No component in this file
 * fetches data, no component derives SAFE / BLOCKED / HALTED / WITHIN LIMIT /
 * BREACHED that the backend did not say, and no missing limit ever renders as
 * a satisfied one. Geometry helpers live in lib/riskVizMath (unit-tested).
 *
 * Props mirror the exact payload paths:
 *  - RuntimeRiskState            (/api/debug/state -> risk section)
 *  - RiskChecks                  (/api/v1/risk/status -> risk_checks)
 *  - risk_config limits          (/api/v1/risk/status -> risk_config)
 *  - AccountState / exposure     (canonical snapshot, /api/v1/risk/summary)
 */

import type { AccountState, RiskChecks, RuntimeRiskState } from "@/types/domain";
import { formatNumber, formatPct } from "@/lib/format";
import {
  UNKNOWN_WORD,
  backendWord,
  clamp01,
  counterTone,
  type ExposureTotals,
  floorPairTone,
  gateFunnel,
  limitTone,
  limitUtilization,
} from "@/lib/riskVizMath";
import "./pro-risk.css";

type ToneClass = "ok" | "warn" | "bad" | "unknown";

const UNKNOWN_LABEL = UNKNOWN_WORD;

/** Kill-switch word: a boolean restated, never inferred. null = payload absent. */
export function killSwitchWord(killSwitchActive: boolean | null | undefined): string {
  if (killSwitchActive === true) return "ACTIVE";
  if (killSwitchActive === false) return "DISENGAGED";
  return UNKNOWN_LABEL;
}

/**
 * Colour tone from the backend's OWN state word + kill-switch boolean only
 * (mirrors the badge mapping RiskPage already uses). Tone is not a verdict:
 * no new state name is created here, and nothing missing resolves to "ok".
 */
export function guardianTone(state: RuntimeRiskState | null | undefined): ToneClass {
  if (!state) return "unknown";
  if (state.kill_switch_active === true) return "bad";
  const word = state.runtime_risk_state_effective?.trim().toUpperCase() ?? "";
  if (word === "") return "unknown";
  if (word === "RUNNING") return "ok";
  if (word === "HALTED" || word === "BLOCKED") return "bad";
  return "warn";
}

/**
 * Freshness colour tone from the backend's own status STRING (same word table
 * the existing StatusBadge uses in components/primitives.tsx — a word→colour
 * lookup over payload values, never a new state name). Missing/blank -> unknown.
 */
export function freshnessTone(accountFreshness: string | null | undefined): ToneClass {
  const word = accountFreshness?.trim().toUpperCase() ?? "";
  if (word === "") return "unknown";
  if (["FRESH", "OK", "HEALTHY", "READY", "ACTIVE"].includes(word)) return "ok";
  if (["ERROR", "FAILED", "STALE_DATA", "DISCONNECTED"].includes(word)) return "bad";
  return "warn";
}

function Tile({
  k,
  v,
  s,
  tone,
}: {
  k: string;
  v: string;
  s?: string;
  tone: ToneClass;
}) {
  return (
    <div className={`rv-tile tone-${tone}`}>
      <span className="rv-tile__k">{k}</span>
      <span className="rv-tile__v">{v}</span>
      {s !== undefined && <span className="rv-tile__s">{s}</span>}
    </div>
  );
}

/**
 * GuardianHero — the kill-switch tile.
 *
 * The big word is `runtime_risk_state_effective` echoed verbatim; when the
 * runtime state payload is missing the word is UNKNOWN — the engine may well
 * be fine, but this component will not claim it. Halt reason text is printed
 * only because the backend put it in the payload, character for character.
 */
export function GuardianHero({
  state,
  probedNote,
}: {
  /** /api/debug/state risk section — null/undefined = payload unavailable. */
  state: RuntimeRiskState | null | undefined;
  /** Optional freshness note supplied by the caller (rendered as-is). */
  probedNote?: string;
}) {
  const tone = guardianTone(state);
  const word = state ? backendWord(state.runtime_risk_state_effective) : UNKNOWN_LABEL;
  return (
    <section className="rv-hero" aria-label="Guardian state">
      <div className={`rv-hero__state tone-${tone}`}>
        <span className="rv-hero__k">Runtime risk state (backend)</span>
        <span className="rv-hero__word">{word}</span>
        <span className="rv-hero__src">
          {state
            ? `source: runtime_risk_state_effective${state.runtime_risk_state ? ` · raw=${state.runtime_risk_state}` : ""}`
            : "source: /api/debug/state — payload unavailable"}
        </span>
        {state && state.halt_reason ? (
          <span className="rv-hero__reason">backend halt_reason: {state.halt_reason}</span>
        ) : null}
      </div>
      <div className="rv-hero__side">
        <Tile
          k="Kill switch"
          v={killSwitchWord(state?.kill_switch_active)}
          s={state ? "field: kill_switch_active" : "no backend payload"}
          tone={state ? (state.kill_switch_active ? "bad" : "ok") : "unknown"}
        />
        <Tile
          k="Survival mode"
          v={state ? (state.survival_mode ? "ACTIVE" : "OFF") : UNKNOWN_LABEL}
          s={state ? "field: survival_mode" : undefined}
          tone={state ? (state.survival_mode ? "warn" : "ok") : "unknown"}
        />
        <Tile
          k="Account freshness"
          v={state ? backendWord(state.account_freshness) : UNKNOWN_LABEL}
          s={state ? "field: account_freshness" : undefined}
          tone={freshnessTone(state?.account_freshness)}
        />
        {probedNote ? <span className="rv-hero__src">{probedNote}</span> : null}
      </div>
    </section>
  );
}

/**
 * DrawdownBar — actual drawdown vs the BACKEND's max_account_drawdown_pct.
 *
 * No limit in the payload -> the bar becomes an indeterminate hatch and the
 * caption says so; the actual value is still shown because the broker sent it.
 * A missing limit is never drawn as headroom or as "within limit".
 */
export function DrawdownBar({
  actualPct,
  limitPct,
  label = "Account drawdown",
}: {
  /** AccountState.drawdown (backend-computed, same scale as the config limit). */
  actualPct: number | null | undefined;
  /** risk_config.max_account_drawdown_pct — null when the engine sent none. */
  limitPct: number | null | undefined;
  label?: string;
}) {
  const util = limitUtilization(actualPct, limitPct);
  const hasActual = typeof actualPct === "number" && Number.isFinite(actualPct);
  const hasLimit = typeof limitPct === "number" && Number.isFinite(limitPct);
  // Nothing comparable on either side -> unknown; a one-sided payload (actual
  // without a limit) stays warn: the value is real, the budget is not known.
  const tone: ToneClass = !hasActual && !hasLimit ? "unknown" : util === null ? "warn" : limitTone(util);
  const width = clamp01(util);
  return (
    <section className="rv-limit" aria-label={label}>
      <div className="rv-limit__row">
        <span className={`rv-limit__actual tone-${tone}`}>
          {hasActual ? formatPct(actualPct) : UNKNOWN_LABEL}
        </span>
        <span className="rv-limit__limit">
          {limitPct === null || limitPct === undefined
            ? "no backend limit"
            : `limit ${formatPct(limitPct)} (max_account_drawdown_pct)`}
        </span>
      </div>
      <div className={`rv-limit__track tone-${tone}`} role="img" aria-label={`${label}: ${hasActual ? formatPct(actualPct) : "unknown"}${util !== null ? ` (${(util * 100).toFixed(0)}% of backend limit)` : ""}`}>
        <i className="rv-limit__fill" style={{ width: width === null ? "100%" : `${width * 100}%` }} />
        {util !== null && util > 1 ? <span className="rv-limit__mark" style={{ left: "calc(100% - 2px)" }} /> : null}
      </div>
      <span className={`rv-limit__note${util === null ? " no-budget" : ""}`}>
        {util === null
          ? "Backend supplied no drawdown limit — the budget is unknown, never assumed."
          : `${(util * 100).toFixed(0)}% of the backend-supplied limit consumed (arithmetic on two backend values).`}
      </span>
    </section>
  );
}

/**
 * MarginArc — margin level value + backend threshold WORDS.
 *
 * The arc fraction is only computed when the payload supplies both a value and
 * a threshold; otherwise the ring renders indeterminate (dashed) — no assumed
 * stop-out level, no invented "safe" sweep. `thresholdWord` is a backend
 * string (e.g. a status or config label) echoed verbatim.
 */
export function MarginArc({
  marginLevelPct,
  thresholdPct,
  thresholdWord,
  label = "Margin level",
}: {
  /** AccountState.margin_level / exposure.account.margin_level (broker %). */
  marginLevelPct: number | null | undefined;
  /** Backend threshold on the SAME scale (e.g. a configured margin floor), if any. */
  thresholdPct?: number | null;
  /** Backend-supplied threshold wording echoed verbatim; no fallback text. */
  thresholdWord?: string | null;
  label?: string;
}) {
  const hasValue = typeof marginLevelPct === "number" && Number.isFinite(marginLevelPct);
  const hasThreshold = typeof thresholdPct === "number" && Number.isFinite(thresholdPct);
  // Floor semantics: margin level ABOVE the backend floor is the clean side.
  const floor = floorPairTone(marginLevelPct, thresholdPct);
  const tone: ToneClass = !hasValue ? "unknown" : floor === null ? "warn" : floor;
  // Geometry only: fraction of the dial = value against the backend floor
  // (capped at the far end). With no floor supplied, the ring is indeterminate.
  let sweep: number | null = null;
  if (hasValue && hasThreshold && (thresholdPct as number) > 0) {
    sweep = clamp01((marginLevelPct as number) / (thresholdPct as number));
  }
  const R = 46;
  const CIRC = 2 * Math.PI * R;
  const arcLen = CIRC * 0.75; // 270° dial
  const shown = sweep === null ? null : sweep * arcLen;
  return (
    <section className={`rv-arc tone-${tone}`} aria-label={label}>
      <svg viewBox="0 0 120 120" width={104} height={104} role="img">
        <path
          className="rv-arc__track"
          d="M 18 102 A 46 46 0 1 1 102 102"
          fill="none"
          strokeWidth={9}
          strokeLinecap="round"
        />
        <path
          className="rv-arc__value"
          d="M 18 102 A 46 46 0 1 1 102 102"
          fill="none"
          strokeWidth={9}
          strokeLinecap="round"
          strokeDasharray={`${shown === null ? arcLen : shown} ${CIRC}`}
          opacity={shown === null ? 0.35 : 1}
        />
        <text className="rv-arc__num" x="60" y="66" textAnchor="middle">
          {hasValue ? formatNumber(marginLevelPct, 0) : "?"}
        </text>
      </svg>
      <div className="rv-arc__legend">
        <span>
          <b>{hasValue ? `${marginLevelPct}` : UNKNOWN_LABEL}</b> {label}
          {hasValue ? " (broker margin_level)" : ""}
        </span>
        {thresholdWord ? (
          <span>
            backend threshold: <b>{backendWord(thresholdWord)}</b>
          </span>
        ) : null}
        {!hasThreshold ? (
          <span className="rv-limit__note no-budget">
            No backend margin threshold in payload — arc is indeterminate by design.
          </span>
        ) : (
          <span>backend floor <b>{formatPct(thresholdPct)}</b></span>
        )}
      </div>
    </section>
  );
}

const VERDICT_LABEL: Record<"pass" | "fail" | "unknown", string> = {
  pass: "PASS",
  fail: "FAIL",
  unknown: UNKNOWN_LABEL,
};

/**
 * GateFunnel — last-proposal `risk_checks` as a chip row.
 *
 * PASS/FAIL chips echo the backend `passed`/`allowed` booleans 1:1; any entry
 * whose payload carries neither boolean is an UNKNOWN chip, never FAIL (see
 * riskVizMath.gateVerdict). An empty payload says "no gates recorded" — a
 * different claim from "all unknown", so it is worded separately.
 */
export function GateFunnel({
  checks,
  emptyMessage = "No gate trace on the last proposal (risk_checks absent or empty).",
}: {
  /** V1RiskStatus.risk_checks — untrusted record, narrowed inside gateFunnel. */
  checks: RiskChecks | null | undefined;
  emptyMessage?: string;
}) {
  const funnel = gateFunnel(checks);
  if (funnel.total === 0) return <p className="rv-funnel__empty">{emptyMessage}</p>;
  return (
    <div className="rv-funnel" aria-label="Risk gate funnel">
      {funnel.rows.map((row) => (
        <span key={row.name} className={`rv-chip tone-${row.verdict}`} title={row.reason ?? undefined}>
          <span className="rv-chip__n">{row.name.replace(/_/g, " ")}</span>
          <span className="rv-chip__v">{VERDICT_LABEL[row.verdict]}</span>
        </span>
      ))}
      <span className="rv-funnel__count">
        {funnel.pass} pass · {funnel.fail} fail · {funnel.unknown} unknown — verdicts from backend booleans only
      </span>
    </div>
  );
}

/**
 * BreakerTiles — drop/failure counters from the guardian payload.
 *
 * Numbers are shown as the backend counted them; tone is derived from
 * value > 0 ONLY (counterTone). A counter that is missing or non-numeric is an
 * UNKNOWN tile in the warn colour — an unproven zero is never coloured clean.
 */
export function BreakerTiles({ state }: { state: RuntimeRiskState | null | undefined }) {
  const counters: Array<{ k: string; field: string; v: number | null | undefined }> = [
    { k: "Telemetry dropped", field: "telemetry_dropped", v: state?.telemetry_dropped },
    { k: "Audit dead-letter rows", field: "audit_dead_letter_rows", v: state?.audit_dead_letter_rows },
    { k: "Audit batch failures", field: "audit_batch_failures", v: state?.audit_batch_failures },
    { k: "Financial events failed", field: "financial_events_failed", v: state?.financial_events_failed },
  ];
  return (
    <div className="rv-breakers" aria-label="Circuit-breaker counters">
      {counters.map((c) => {
        const tone: ToneClass = state ? counterTone(c.v) : "unknown";
        const value = state ? (typeof c.v === "number" && Number.isFinite(c.v) ? String(c.v) : UNKNOWN_LABEL) : UNKNOWN_LABEL;
        return <Tile key={c.field} k={c.k} v={value} s={state ? `field: ${c.field}` : "no backend payload"} tone={tone} />;
      })}
    </div>
  );
}

/** Convenience: backend `exposureTotals(...)` + account block -> one-line caption.
 *  A null totals object (no backend rows) is worded as "no rows", never "flat". */
export function exposureCaption(exposure: AccountState | null | undefined, totals?: ExposureTotals | null): string {
  if (!totals) return "Exposure: no backend rows — not rendered as flat.";
  const equity = exposure?.equity;
  return `Exposure: ${totals.positions ?? "?"} pos · ${totals.volume ?? "?"} lots across ${totals.symbols} symbol(s)${typeof equity === "number" ? ` · equity ${formatNumber(equity)}` : ""}`;
}
