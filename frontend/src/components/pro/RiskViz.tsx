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
 *
 * OWNER:    uiux-modern-20260926 lane 2 (components/pro) — future edits here.
 * INVARIANTS: no I/O, no frontend verdict vocabulary, a missing limit renders
 *           as an indeterminate hatch (never 0, never a satisfied bar), the
 *           `rv-` prefix only, motion only under prefers-reduced-motion:
 *           no-preference.
 * EXTEND:   a new tile/bar takes a payload field plus its tone from
 *           lib/riskVizMath helpers — never a word the backend did not send.
 */

import { useMemo } from "react";
import type { AccountState, RiskChecks, RuntimeRiskState } from "@/types/domain";
import { formatNumber, formatPct } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import {
  UNKNOWN_WORD,
  backendWord,
  clamp01,
  counterTone,
  type ExposureTotals,
  floorPairTone,
  limitTone,
  limitUtilization,
} from "@/lib/riskVizMath";
import { gateEvidence, verdictWord } from "@/lib/riskGateTrace";
import "./pro-risk.css";

type ToneClass = "ok" | "warn" | "bad" | "unknown";

const UNKNOWN_LABEL = UNKNOWN_WORD;

/** t signature shared by the display helpers below (see stores/i18nStore). */
type Translate = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/**
 * Display word -> localized word for the FOUR literals this file may show on
 * its own (ACTIVE / DISENGAGED / OFF / UNKNOWN — see killSwitchWord and the
 * tiles). Every other word is a backend payload string and passes through
 * VERBATIM: this helper invents no vocabulary, it only translates the ones
 * this component already owned.
 */
function dispT(t: Translate, word: string): string {
  if (word === "ACTIVE") return t("ui.word.active", "ACTIVE");
  if (word === "DISENGAGED") return t("ui.word.disengaged", "DISENGAGED");
  if (word === "OFF") return t("ui.word.off", "OFF");
  if (word === "UNKNOWN") return t("ui.word.unknown", "UNKNOWN");
  return word;
}

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
  const t = useI18n((s) => s.t);
  const tone = guardianTone(state);
  const word = state ? backendWord(state.runtime_risk_state_effective) : UNKNOWN_LABEL;
  return (
    <section className="rv-hero" aria-label={t("ui.risk.guardian_aria", "Guardian state")}>
      <div className={`rv-hero__state tone-${tone}`}>
        <span className="rv-hero__k">{t("ui.risk.runtime_state_k", "Runtime risk state (backend)")}</span>
        <span className="rv-hero__word">{dispT(t, word)}</span>
        <span className="rv-hero__src">
          {state
            ? `${t("ui.risk.source_field", "source: runtime_risk_state_effective")}${state.runtime_risk_state ? ` · raw=${state.runtime_risk_state}` : ""}`
            : t("ui.risk.source_unavailable", "source: /api/debug/state — payload unavailable")}
        </span>
        {state && state.halt_reason ? (
          <span className="rv-hero__reason">
            {t("ui.risk.halt_reason", "backend halt_reason: {r}", { r: state.halt_reason })}
          </span>
        ) : null}
      </div>
      <div className="rv-hero__side">
        <Tile
          k={t("ui.risk.kill_switch", "Kill switch")}
          v={dispT(t, killSwitchWord(state?.kill_switch_active))}
          s={state ? t("ui.risk.field", "field: {f}", { f: "kill_switch_active" }) : t("ui.risk.no_payload", "no backend payload")}
          tone={state ? (state.kill_switch_active ? "bad" : "ok") : "unknown"}
        />
        <Tile
          k={t("ui.risk.survival_mode", "Survival mode")}
          v={dispT(t, state ? (state.survival_mode ? "ACTIVE" : "OFF") : UNKNOWN_LABEL)}
          s={state ? t("ui.risk.field", "field: {f}", { f: "survival_mode" }) : undefined}
          tone={state ? (state.survival_mode ? "warn" : "ok") : "unknown"}
        />
        <Tile
          k={t("ui.risk.account_freshness", "Account freshness")}
          v={state ? backendWord(state.account_freshness) : dispT(t, UNKNOWN_LABEL)}
          s={state ? t("ui.risk.field", "field: {f}", { f: "account_freshness" }) : undefined}
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
  label,
}: {
  /** AccountState.drawdown (backend-computed, same scale as the config limit). */
  actualPct: number | null | undefined;
  /** risk_config.max_account_drawdown_pct — null when the engine sent none. */
  limitPct: number | null | undefined;
  /** Caption override; defaults to the localized "Account drawdown". */
  label?: string;
}) {
  const t = useI18n((s) => s.t);
  const lab = label ?? t("ui.risk.drawdown_label", "Account drawdown");
  const util = limitUtilization(actualPct, limitPct);
  const hasActual = typeof actualPct === "number" && Number.isFinite(actualPct);
  const hasLimit = typeof limitPct === "number" && Number.isFinite(limitPct);
  // Nothing comparable on either side -> unknown; a one-sided payload (actual
  // without a limit) stays warn: the value is real, the budget is not known.
  const tone: ToneClass = !hasActual && !hasLimit ? "unknown" : util === null ? "warn" : limitTone(util);
  const width = clamp01(util);
  return (
    <section className="rv-limit" aria-label={lab}>
      <div className="rv-limit__row">
        <span className={`rv-limit__actual tone-${tone}`}>
          {hasActual ? formatPct(actualPct) : dispT(t, UNKNOWN_LABEL)}
        </span>
        <span className="rv-limit__limit">
          {limitPct === null || limitPct === undefined
            ? t("ui.risk.no_limit", "no backend limit")
            : t("ui.risk.limit_line", "limit {v} (max_account_drawdown_pct)", { v: formatPct(limitPct) })}
        </span>
      </div>
      {/* Budget side: whenever `util` is null the backend sent no usable
          limit, so the track is the indeterminate hatch — the value's own
          tone still colours the number above it. A missing limit never draws
          as a full or as a satisfied bar. */}
      <div
        className={`rv-limit__track ${util === null ? "tone-unknown" : `tone-${tone}`}`}
        role="img"
        aria-label={`${lab}: ${hasActual ? formatPct(actualPct) : t("ui.word.unknown", "UNKNOWN")}${
          util !== null ? ` (${t("ui.risk.of_limit", "{p}% of backend limit", { p: (util * 100).toFixed(0) })})` : ""
        }`}
      >
        <i className="rv-limit__fill" style={{ width: width === null ? "100%" : `${width * 100}%` }} />
        {util !== null && util > 1 ? <span className="rv-limit__mark" /> : null}
      </div>
      <span className={`rv-limit__note${util === null ? " no-budget" : ""}`}>
        {util === null
          ? t("ui.risk.no_limit_note", "Backend supplied no drawdown limit — the budget is unknown, never assumed.")
          : t("ui.risk.limit_consumed", "{p}% of the backend-supplied limit consumed (arithmetic on two backend values).", { p: (util * 100).toFixed(0) })}
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
  label,
}: {
  /** AccountState.margin_level / exposure.account.margin_level (broker %). */
  marginLevelPct: number | null | undefined;
  /** Backend threshold on the SAME scale (e.g. a configured margin floor), if any. */
  thresholdPct?: number | null;
  /** Backend-supplied threshold wording echoed verbatim; no fallback text. */
  thresholdWord?: string | null;
  /** Caption override; defaults to the localized "Margin level". */
  label?: string;
}) {
  const t = useI18n((s) => s.t);
  const lab = label ?? t("ui.risk.margin_label", "Margin level");
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
    <section className={`rv-arc tone-${tone}${sweep === null ? " is-indeterminate" : ""}`} aria-label={lab}>
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
          <b>{hasValue ? `${marginLevelPct}` : dispT(t, UNKNOWN_LABEL)}</b> {lab}
          {hasValue ? ` ${t("ui.risk.broker_margin", "(broker margin_level)")}` : ""}
        </span>
        {thresholdWord ? (
          <span>
            {t("ui.risk.backend_threshold", "backend threshold: ")}
            <b>{backendWord(thresholdWord)}</b>
          </span>
        ) : null}
        {!hasThreshold ? (
          <span className="rv-limit__note no-budget">
            {t("ui.risk.no_threshold_note", "No backend margin threshold in payload — arc is indeterminate by design.")}
          </span>
        ) : (
          <span>
            {t("ui.risk.backend_floor", "backend floor ")}
            <b>{formatPct(thresholdPct)}</b>
          </span>
        )}
      </div>
    </section>
  );
}

/**
 * GateFunnel — last-proposal `risk_checks` as a chip row.
 *
 * `risk_checks` is a flat EVIDENCE record of metric PAIRS (value + limit in
 * sibling keys), not a gate-verdict map, so a chip here is derived by pairing
 * the siblings and comparing them (lib/riskGateTrace) — a PASS/FAIL chip is
 * the arithmetic restatement of two backend numbers, never an invented
 * verdict. An entry whose limit is absent is an UNKNOWN chip, never FAIL; an
 * empty payload says "no gates recorded" — a different claim from "all
 * unknown", so it is worded separately.
 */
export function GateFunnel({
  checks,
  emptyMessage,
}: {
  /** V1RiskStatus.risk_checks — untrusted record, narrowed inside gateEvidence. */
  checks: RiskChecks | null | undefined;
  /** Optional override for the empty-funnel caption (localized default). */
  emptyMessage?: string;
}) {
  const t = useI18n((s) => s.t);
  // Pairing `checks` allocates rows/objects; memoizing keeps an unchanged
  // payload from re-running it on every render of the risk page.
  const funnel = useMemo(() => gateEvidence(checks), [checks]);
  if (funnel.total === 0)
    return (
      <p className="rv-funnel__empty">
        {emptyMessage ?? t("ui.risk.gate_empty", "No gate trace on the last proposal (risk_checks absent or empty).")}
      </p>
    );
  return (
    <div className="rv-funnel" aria-label={t("ui.risk.funnel_aria", "Risk gate funnel")}>
      {funnel.rows.map((row) => {
        const verdict = verdictWord(row.verdict);
        const verdictText =
          verdict === "PASS"
            ? t("ui.word.pass", "PASS")
            : verdict === "FAIL"
              ? t("ui.word.fail", "FAIL")
              : t("ui.word.unknown", "UNKNOWN");
        return (
          <span key={row.name} className={`rv-chip tone-${row.verdict}`} title={row.detail ?? undefined}>
            <span className="rv-chip__n">{row.name.replace(/_/g, " ")}</span>
            <span className="rv-chip__v">{verdictText}</span>
          </span>
        );
      })}
      <span className="rv-funnel__count">
        {t("ui.risk.gate_count", "{p} pass · {f} fail · {u} unknown — value vs its own backend limit", {
          p: funnel.pass,
          f: funnel.fail,
          u: funnel.unknown,
        })}
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
  const t = useI18n((s) => s.t);
  // Labels come from `t`, values from `state`: one array per (state, t) pair
  // instead of one per render of a live guardian payload.
  const counters = useMemo<Array<{ k: string; field: string; v: number | null | undefined }>>(() => [
    { k: t("ui.risk.counter.telemetry_dropped", "Telemetry dropped"), field: "telemetry_dropped", v: state?.telemetry_dropped },
    { k: t("ui.risk.counter.audit_dead_letter_rows", "Audit dead-letter rows"), field: "audit_dead_letter_rows", v: state?.audit_dead_letter_rows },
    { k: t("ui.risk.counter.audit_batch_failures", "Audit batch failures"), field: "audit_batch_failures", v: state?.audit_batch_failures },
    { k: t("ui.risk.counter.financial_events_failed", "Financial events failed"), field: "financial_events_failed", v: state?.financial_events_failed },
  ], [state, t]);
  return (
    <div className="rv-breakers" aria-label={t("ui.risk.breakers_aria", "Circuit-breaker counters")}>
      {counters.map((c) => {
        const tone: ToneClass = state ? counterTone(c.v) : "unknown";
        const value = state
          ? typeof c.v === "number" && Number.isFinite(c.v)
            ? String(c.v)
            : dispT(t, UNKNOWN_LABEL)
          : dispT(t, UNKNOWN_LABEL);
        return (
          <Tile
            key={c.field}
            k={c.k}
            v={value}
            s={state ? t("ui.risk.field", "field: {f}", { f: c.field }) : t("ui.risk.no_payload", "no backend payload")}
            tone={tone}
          />
        );
      })}
    </div>
  );
}

/** Convenience: backend `exposureTotals(...)` + account block -> one-line caption.
 *  A null totals object (no backend rows) is worded as "no rows", never "flat".
 *
 *  i18n: this is a plain function (not a component), so it reads `t` from the
 *  store at CALL time instead of subscribing — callers that render the caption
 *  should select `t` themselves (or re-render on language change) so the line
 *  follows a switch. It currently has no call sites in the repo. */
export function exposureCaption(exposure: AccountState | null | undefined, totals?: ExposureTotals | null): string {
  const t = useI18n.getState().t;
  if (!totals) return t("ui.risk.exposure_none", "Exposure: no backend rows — not rendered as flat.");
  const equity = exposure?.equity;
  return t("ui.risk.exposure_line", "Exposure: {pos} pos · {vol} lots across {sym} symbol(s){eq}", {
    pos: totals.positions ?? "?",
    vol: totals.volume ?? "?",
    sym: totals.symbols,
    eq:
      typeof equity === "number"
        ? ` · ${t("ui.risk.exposure_equity", "equity {e}", { e: formatNumber(equity) })}`
        : "",
  });
}
