/**
 * PURPOSE:  Hero header + KPI strip for /alt/trading — layered translucent
 *           command-status surface with grid texture, kicker hairline rule,
 *           endpoint-provenance chips (title tooltips), an
 *           availability-pulsed engine pill, a refresh-all button with a
 *           spinner state, and eight styled KPI cards (4 original facts +
 *           4 counter facts), all presentation-only.
 * OWNER:    ui/tr-a  (future edits belong to this lane)
 * CONSUMES: EngineSnapshot.engine_running, .state_version, .symbol,
 *           .runtime_mode, .execution_mode, .data_source, .adapter_class,
 *           .health.details.mt5, .health.subsystems.engine,
 *           .account.trade_allowed, plus page-computed props mt5Positions
 *           (mt5Query.data.positions.length), pendingOrders
 *           (mt5Query.data.orders.length), matched / drift (ledger OPEN
 *           reconciliation), nowMs (clock), refreshAll / anyFetching and
 *           engineCmd.state.lastMessage / .lastResult (last command reply).
 * PROVIDES: default TradingHero — rendered at the top of TradingPage.
 * INVARIANTS: backend-guarantee (raw value next to every scaled visual;
 *             missing -> em dash; tone words restate backend words
 *             RUNNING/STOPPED/LIVE/PAPER/SHADOW/ALLOWED/RESTRICTED/MATCHED;
 *             no new fetches/mutations; reduced-motion honored)
 * EXTEND:   new hero figures = new backend fields rendered verbatim; never
 *           compute a verdict here.
 */
import type { ReactNode } from "react";
import type { EngineSnapshot } from "@/types/domain";
import "./tradingHero.css";

/** Provenance of every figure this hero can show — endpoints only, never decoration. */
const SOURCES = [
  { id: "socket", label: "socket snapshot", tip: "Canonical engine snapshot — SSE `state` event / /api/status. Renders engine_running, state_version, runtime_mode/execution_mode, data_source, adapter_class, health.*, account.trade_allowed." },
  { id: "mt5", label: "/api/mt5/status", tip: "MT5 broker read — renders broker positions and pending orders (mt5Query.data.positions/orders length)." },
  { id: "ledger", label: "ledger OPEN", tip: "/api/account/trades?status=OPEN matched by ticket against /api/mt5/status — renders the matched / total reconciliation figure." },
  { id: "orders", label: "/api/operator/orders", tip: "Dispatch audit rows + latency stats (ordersQuery) — part of the refresh-all set." },
] as const;

export interface TradingHeroProps {
  snapshot: EngineSnapshot;
  mt5Positions: number;
  pendingOrders: number;
  matched: number;
  drift: number;
  nowMs: number;
  refreshAll: () => void;
  anyFetching: boolean;
  engineCmd: { state: { running: boolean; lastMessage?: string | null; lastResult?: boolean | null } };
}

const clockFmt = new Intl.DateTimeFormat(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function TradingHero({ snapshot, mt5Positions, pendingOrders, matched, drift, nowMs, refreshAll, anyFetching, engineCmd }: TradingHeroProps) {
  const running = snapshot.engine_running;
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
  const tradeAllowed = snapshot.account.trade_allowed;
  const engineStatus = snapshot.health.subsystems.engine ?? "UNKNOWN";
  const lastMessage = engineCmd.state.lastMessage ?? null;

  /** Original fact 1/4 — engine_running verbatim (RUNNING / STOPPED). */
  const engineCard: KpiCard = {
    icon: "◉",
    label: "Engine loop",
    value: running ? "RUNNING" : "STOPPED",
    tone: running ? "good" : "dim",
    tip: "snapshot.engine_running (canonical socket snapshot) — displayed verbatim, never inferred. state_version climbs while the loop runs.",
    cap: "snapshot.engine_running · verbatim",
    sub: (
      <div className="tr-facts">
        <Chip tone="neutral" tip="health.subsystems.engine — the backend guardian's own status word for the engine subsystem, verbatim.">{`engine health: ${engineStatus}`}</Chip>
        <Chip tone="neutral" tip="state_version — the snapshot counter the backend increments on every engine update (raw field, verbatim).">{`v${snapshot.state_version}`}</Chip>
        <Chip tone="neutral" tip="symbol — the symbol this engine snapshot is bound to (raw field).">{snapshot.symbol ?? "—"}</Chip>
      </div>
    ),
  };

  /** Original fact 2/4 — execution mode verbatim (LIVE / PAPER / SHADOW / …). */
  const modeCard: KpiCard = {
    icon: "◐",
    label: "Execution mode",
    value: currentMode || "—",
    tone: !currentMode ? "dim" : currentMode.startsWith("LIVE") ? "bad" : currentMode.startsWith("PAPER") ? "good" : "accent",
    tip: "snapshot.runtime_mode ?? snapshot.execution_mode — the backend's own mode word, upper-cased for display only. Tone restates that word (LIVE / PAPER / SHADOW), never a verdict.",
    cap: "runtime_mode ?? execution_mode · verbatim",
    sub: (
      <div className="tr-facts">
        <Chip tone="neutral" tip="data_source — where this snapshot's market data comes from (raw field, verbatim).">{`data_source: ${snapshot.data_source ?? "—"}`}</Chip>
        <Chip tone={snapshot.mode_source_mismatch ? "warn" : "neutral"} tip="mode_source_mismatch — the backend's own flag that runtime_mode and the persisted mode disagree (verbatim boolean).">{`mode_source_mismatch: ${String(snapshot.mode_source_mismatch)}`}</Chip>
      </div>
    ),
  };

  /** Original fact 3/4 — broker adapter verbatim. */
  const adapterCard: KpiCard = {
    icon: "⬡",
    label: "Broker adapter",
    value: snapshot.adapter_class ?? "—",
    tone: snapshot.adapter_class ? "accent" : "dim",
    tip: "snapshot.adapter_class — the adapter class the backend instantiates for the broker bridge (raw field, verbatim).",
    cap: "snapshot.adapter_class · verbatim",
    sub: (
      <div className="tr-facts">
        <Chip tone="neutral" tip="health.details.mt5 — the backend's own MT5 detail string from the health section (raw value, verbatim).">
          {`health.mt5: ${String(snapshot.health.details.mt5 ?? "—")}`}
        </Chip>
      </div>
    ),
  };

  /** Original fact 4/4 — account.trade_allowed verbatim (ALLOWED / RESTRICTED / —). */
  const terminalCard: KpiCard = {
    icon: "⇄",
    label: "Terminal trading",
    value: tradeAllowed === null ? "—" : tradeAllowed ? "ALLOWED" : "RESTRICTED",
    tone: tradeAllowed === null ? "dim" : tradeAllowed ? "good" : "bad",
    tip: "account.trade_allowed — the broker terminal's own permission flag (raw boolean). null renders an em dash; it is never shown as a zero or a verdict.",
    cap: "broker terminal trade_allowed · verbatim",
    sub: (
      <div className="tr-facts">
        <Chip tone="neutral" tip="account.available — whether the backend could read the broker account at all (raw flag).">{`account.available: ${String(snapshot.account.available)}`}</Chip>
        <Chip tone="neutral" tip="account.login — the broker account login reported by the terminal (raw field).">{`login: ${String(snapshot.account.login ?? "—")}`}</Chip>
        <Chip tone="neutral" tip="account.server — the broker server name reported by the terminal (raw field).">{snapshot.account.server ?? "—"}</Chip>
      </div>
    ),
  };

  /** Counter fact 5/8 — broker positions count from /api/mt5/status. */
  const positionsCard: KpiCard = {
    icon: "▤",
    label: "Broker positions",
    value: String(mt5Positions),
    tone: "good",
    tip: "mt5Query.data.positions.length — open positions the broker reports (page-computed count of a backend list, raw count shown, never scaled).",
    cap: "open in MT5 · /api/mt5/status",
    sub: "broker-side open positions",
  };

  /** Counter fact 6/8 — pending orders count from /api/mt5/status. */
  const ordersCard: KpiCard = {
    icon: "⌛",
    label: "Pending orders",
    value: String(pendingOrders),
    tone: pendingOrders > 0 ? "warn" : "dim",
    tip: "mt5Query.data.orders.length — unfilled orders waiting at the broker (page-computed count of a backend list).",
    cap: "unfilled at the broker · /api/mt5/status",
    sub: "unfilled at the broker",
  };

  /** Counter fact 7/8 — reconciliation matched / total, raw numbers side by side. */
  const reconCard: KpiCard = {
    icon: "⧉",
    label: "Reconciliation",
    value: `${matched} / ${matched + drift}`,
    tone: drift > 0 ? "warn" : matched > 0 ? "good" : "dim",
    tip: "Ticket match of ledger OPEN rows against broker positions — matched / total rows. The unmatched count is printed beside the value; nothing is scaled.",
    cap: "matched / total ledger rows",
    sub: (
      <div className="tr-facts">
        <Chip tone={drift > 0 ? "warn" : "neutral"} tip="Unmatched rows = total − matched (engine-ledger rows without a broker twin, or broker rows the ledger never opened). The raw count, never a verdict.">{`unmatched: ${drift}`}</Chip>
        <Chip tone="neutral" tip="Reconciliation runs client-side over two backend lists: /api/account/trades?status=OPEN (engine) and /api/mt5/status (broker), matched by ticket.">{drift > 0 ? "MATCHED + UNMATCHED" : matched > 0 ? "MATCHED" : "—"}</Chip>
      </div>
    ),
  };

  /** Counter fact 8/8 — local session clock (explicitly labeled client-side). */
  const clockCard: KpiCard = {
    icon: "◔",
    label: "Session clock",
    value: clockFmt.format(nowMs),
    tone: "accent",
    tip: "Browser clock at render time (props.nowMs) — labeled local on purpose: it is not a backend field.",
    cap: "local render time",
    sub: "browser clock · not a backend field",
  };

  const primaryCards = [engineCard, modeCard, adapterCard, terminalCard];
  const counterCards = [positionsCard, ordersCard, reconCard, clockCard];

  return (
    <header className="tr-hero tr-hero-pro">
      <span className="tr-hero-mesh" aria-hidden="true" />

      <div className="tr-hero-head">
        <div className="tr-hero-title">
          <div className="tr-kicker" aria-hidden="true">
            <span className="tr-kicker-dot" />
            LIVE CONTROL DECK
            <span className="tr-kicker-rule" />
          </div>
          <h1 className="tr-h1">
            <span className="tr-h1-glyph" aria-hidden="true">⌁</span>
            <span className="tr-h1-word">Trading</span>
          </h1>
          <p className="tr-sub">
            Engine readout, broker bridge, execution mode and forensic flow — every value below is the backend reply, rendered verbatim.
          </p>

          <div className="tr-chiprow">
            <span
              className={`tr-engine-pill ${running ? "is-on" : "is-off"}`}
              title={`snapshot.engine_running (socket snapshot) = ${running ? "RUNNING" : "STOPPED"} — displayed verbatim, never inferred. The dot pulses only while the backend reports RUNNING.`}
            >
              <span className="tr-engine-dot" aria-hidden="true" />
              <span className="tr-engine-state">{running ? "RUNNING" : "STOPPED"}</span>
              <span className="tr-engine-meta">engine · v{snapshot.state_version}</span>
            </span>

            <Chip
              tone={currentMode.startsWith("LIVE") ? "bad" : currentMode ? "accent" : "dim"}
              tip={`runtime_mode ?? execution_mode = ${currentMode || "—"} — the backend's own mode word (verbatim).`}
            >
              {`mode ${currentMode || "—"}`}
            </Chip>

            {lastMessage !== null && lastMessage !== "" && (
              <Chip
                tone={engineCmd.state.lastResult ? "good" : "warn"}
                tip={`engineCmd.state.lastMessage — the backend's own reply to the last engine command (lastResult=${String(engineCmd.state.lastResult)}).`}
              >
                {engineCmd.state.lastResult ? "✓" : "✕"} {`last command: ${lastMessage}`}
              </Chip>
            )}
          </div>

          <div className="tr-provenance" aria-label="backend endpoints surfaced by this hero">
            {SOURCES.map((s) => (
              <span className="tr-src" key={s.id} title={`Provenance — ${s.tip}`}>
                <span className="tr-src-dot" aria-hidden="true" />
                {s.label}
              </span>
            ))}
          </div>
        </div>

        <div className="tr-hero-side">
          <button
            type="button"
            className="tr-refresh"
            onClick={refreshAll}
            disabled={anyFetching}
            aria-label="Refresh all trading panels"
            title="Refetch the existing queries only (/api/mt5/status, /api/operator/orders, ledger OPEN, executions) — no new endpoint is ever called."
          >
            {anyFetching ? <span className="tr-refresh-spin" aria-hidden="true" /> : <span className="tr-refresh-ico" aria-hidden="true">⟳</span>}
            <span>{anyFetching ? "Refreshing…" : "Refresh all"}</span>
          </button>
        </div>
      </div>

      {/* Primary KPI strip — the 4 original hero facts, redesigned. */}
      <div className="tr-kpi-strip">
        {primaryCards.map((c) => (
          <KpiCardView key={c.label} card={c} />
        ))}
      </div>

      {/* Secondary strip — broker/ledger counters + session clock. */}
      <div className="tr-kpi-strip tr-kpi-strip--compact">
        {counterCards.map((c) => (
          <KpiCardView key={c.label} card={c} />
        ))}
      </div>
    </header>
  );
}

interface KpiCard {
  icon: string;
  label: string;
  value: string;
  tone: "good" | "bad" | "warn" | "accent" | "dim";
  tip: string;
  cap: string;
  sub: ReactNode;
}

function KpiCardView({ card }: { card: KpiCard }) {
  return (
    <article className="tr-kpi-card" title={card.tip}>
      <div className="tr-kpi-head">
        <span className="tr-kpi-ico" aria-hidden="true">{card.icon}</span>
        <span className="tr-kpi-lab">{card.label}</span>
      </div>
      <div className={`tr-kpi-val tone-${card.tone}`}>{card.value}</div>
      <div className="tr-kpi-sub">
        <span className="tr-kpi-subline">{card.sub}</span>
        <span className="tr-kpi-cap">{card.cap}</span>
      </div>
    </article>
  );
}

/** Small info chip: the `tip` becomes both a native tooltip and a CSS tooltip. */
function Chip({ tone, tip, children }: { tone: "good" | "bad" | "warn" | "accent" | "neutral" | "dim"; tip: string; children: ReactNode }) {
  return (
    <span className={`tr-chip tone-${tone}`} title={tip} aria-label={tip}>
      <span className="tr-chip-body">{children}</span>
      <span className="tr-chip-tip" aria-hidden="true">{tip}</span>
    </span>
  );
}
