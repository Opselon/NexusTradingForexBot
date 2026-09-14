/**
 * ReplayPanel — React port of Web/replay_panel.js (CHG-0043, REPLAY_API v1).
 *
 * Behavior parity with the legacy panel, at the page quality bar:
 *  - session creation via POST /api/replay/session (dataset contract + window
 *    + regime flag); the replay_id/identity come back from the reply only
 *  - transport: step / play / pause / reset / seek / checkpoint via POST
 *    /api/replay/control — every button guarded on an active session id and
 *    on in-flight commands; reset is confirm-gated; END_OF_DATA stops play
 *  - cursor strip from GET /api/replay/state (engine truth up to the cursor):
 *    clock, phase, counts, KNOWN vs UNKNOWN events, equity, price, open
 *    position, regime. Future events exist as a COUNT only — never payloads.
 *  - decision drill-down via GET /api/replay/decision?seq= (engine trace)
 *  - operator report via GET /api/replay/report
 *  - onCursorMove hands the cursor time to the Dashboard chart so the
 *    KNOWN/UNKNOWN dimming (drawKnownBoundary port) renders live
 *
 * The command result is ALWAYS the backend's own words (message/detail);
 * nothing is assumed locally. A small self-contained runner is used instead
 * of useMutationFeedback because replies here carry payloads (replay_id,
 * result.status) beyond the {success,message} shape.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { replayApi } from "@/pages/_shared/edgeApi";
import type { ReplayDecision, ReplayReport, ReplayState } from "@/pages/_shared/contracts";
import { ConfirmModal, Panel } from "@/components/primitives";
import { ApiError } from "@/types/api";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";
import "@/pages/_shared/pages.css";
import "./market-console.css";

/** Naive local ISO for <input type=datetime-local> (legacy panel parity). */
function localIso(d: Date): string {
  const off = d.getTimezoneOffset() * 60_000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
}

function defaultWindow(): { start: string; end: string } {
  const now = new Date();
  return { start: localIso(new Date(now.getTime() - 8 * 3_600_000)), end: localIso(now) };
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.status === 422 || e.status === 404 ? e.message : `${e.message}`;
  if (e instanceof Error) return e.message;
  return "unknown error";
}

const PHASE_TONE: Record<string, string> = {
  RUNNING: "good",
  PAUSED: "warn",
  PLAYING: "good",
  FINISHED: "",
  END_OF_DATA: "warn",
};

interface RunState {
  running: boolean;
  message: string | null;
  ok: boolean | null;
}

export function ReplayPanel({ onCursorMove }: { onCursorMove?: (iso: string | null) => void }) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [{ start, end }, setWin] = useState(defaultWindow);
  const [regimeEnabled, setRegimeEnabled] = useState(false);
  const [replayId, setReplayId] = useState<string | null>(null);
  const [sessionChip, setSessionChip] = useState<string | null>(null);
  const [st, setSt] = useState<ReplayState | null>(null);
  const [report, setReport] = useState<ReplayReport | null>(null);
  const [decision, setDecision] = useState<{ seq: number; row: ReplayDecision } | null>(null);
  const [seqInput, setSeqInput] = useState("0");
  const [seekTime, setSeekTime] = useState("");
  const [speed, setSpeed] = useState(2);
  const [playing, setPlaying] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [run, setRun] = useState<RunState>({ running: false, message: null, ok: null });
  const stepTimer = useRef<number | null>(null);
  const playRef = useRef(false);

  useEffect(() => {
    playRef.current = playing;
  }, [playing]);

  // Cursor → chart boundary (KNOWN/UNKNOWN) — same contract as legacy panel.
  useEffect(() => {
    onCursorMove?.(st?.clock ?? null);
  }, [st?.clock, onCursorMove]);

  const stopPlay = useCallback(() => {
    setPlaying(false);
    if (stepTimer.current !== null) {
      window.clearInterval(stepTimer.current);
      stepTimer.current = null;
    }
  }, []);

  useEffect(() => () => stopPlay(), [stopPlay]);

  const refreshState = useCallback(async (): Promise<void> => {
    try {
      setSt(await replayApi.state());
    } catch {
      /* session expired server-side: keep the last cursor, the next command
         surfaces the backend's own 404 wording — no silent clear, no fake */
    }
  }, []);

  // Poll cursor state while a session exists (cheap, cursor-bounded payload).
  useEffect(() => {
    if (!replayId) return;
    void refreshState();
    const t = window.setInterval(() => void refreshState(), 2_000);
    return () => window.clearInterval(t);
  }, [replayId, refreshState]);

  /** One command runner: reply decides the wording, payloads handled inline. */
  const runCmd = useCallback(
    async <T,>(label: string, fn: () => Promise<T & { ok?: boolean; success?: boolean; message?: string; detail?: unknown }>): Promise<T | null> => {
      setRun({ running: true, message: null, ok: null });
      try {
        const res = await fn();
        const accepted = res.ok !== false && res.success !== false;
        const msg =
          res.message ??
          (typeof res.detail === "string" ? res.detail : null) ??
          (accepted ? `${label}: accepted by backend.` : `${label}: refused by backend.`);
        setRun({ running: false, message: msg, ok: accepted });
        pushToast(accepted ? "ok" : "fail", `${label}: ${msg}`);
        return accepted ? res : null;
      } catch (e) {
        const msg = describeError(e);
        setRun({ running: false, message: `${label} failed: ${msg}`, ok: false });
        pushToast("fail", `${label}: ${msg}`);
        return null;
      }
    },
    [pushToast],
  );

  const createSession = async (): Promise<void> => {
    if (!start || !end) {
      setRun({ running: false, message: "Start/end time required.", ok: false });
      return;
    }
    const res = await runCmd("replay session", () =>
      replayApi.createSession({
        dataset_id: `UI-M1-${new Date().toISOString().slice(0, 10).replace(/-/g, "")}`,
        dataset_fingerprint: `uipick-${start}-${end}`,
        symbol: "XAUUSD",
        replay_mode: "BAR_REPLAY",
        start_time: new Date(start).toISOString(),
        end_time: new Date(end).toISOString(),
        git_commit: "",
        regime_enabled: regimeEnabled,
      }),
    );
    if (res?.replay_id) {
      setReplayId(res.replay_id);
      const ident = res.identity as { model_artifact_path?: string } | undefined;
      setSessionChip(ident?.model_artifact_path ? `70D bound: ${String(ident.model_artifact_path).split("/").slice(-1)[0]}` : null);
      setSt(null);
      setReport(null);
      setDecision(null);
    }
  };

  const control = useCallback(
    async (action: "step_tick" | "step_bar" | "play" | "pause" | "reset" | "seek" | "checkpoint", extra?: { n?: number; seek_time?: string }): Promise<void> => {
      if (!replayId) {
        setRun({ running: false, message: "Create a session first — transport is inert without a replay id.", ok: false });
        return;
      }
      const res = await runCmd(action, () => replayApi.control({ action, replay_id: replayId, ...extra }));
      if (res && (res.result as { status?: string } | undefined)?.status === "END_OF_DATA") {
        stopPlay();
        setRun({ running: false, message: "END_OF_DATA reached — playback stopped on the backend's signal.", ok: true });
      }
      if (res) await refreshState();
    },
    [replayId, runCmd, refreshState, stopPlay],
  );

  /** Playback = client-paced step_bar loop (identical to the legacy panel). */
  const startPlay = (): void => {
    if (!replayId || stepTimer.current !== null) return;
    setPlaying(true);
    const intervalMs = Math.max(60, 800 / Math.max(1, speed));
    stepTimer.current = window.setInterval(() => {
      void (async () => {
        if (!replayId) return;
        try {
          const res = await replayApi.control({ action: "step_bar", n: 1, replay_id: replayId });
          if ((res.result as { status?: string } | undefined)?.status === "END_OF_DATA") {
            stopPlay();
            setRun({ running: false, message: "END_OF_DATA reached — playback stopped on the backend's signal.", ok: true });
          }
          setSt(await replayApi.state(replayId).catch(() => null));
        } catch (e) {
          stopPlay();
          setRun({ running: false, message: `play step failed: ${describeError(e)}`, ok: false });
        }
      })();
    }, intervalMs);
  };

  const togglePlay = (): void => {
    if (playing) {
      stopPlay();
      void control("pause");
    } else {
      void startPlay();
    }
  };

  const showReport = async (): Promise<void> => {
    const res = await runCmd("replay report", () => replayApi.report(replayId));
    if (res) setReport(res.report ?? null);
  };

  const showDecision = async (): Promise<void> => {
    const seq = Number.parseInt(seqInput, 10);
    if (!Number.isFinite(seq)) {
      setRun({ running: false, message: "Decision sequence must be a number.", ok: false });
      return;
    }
    const res = await runCmd(`decision ${seq}`, () => replayApi.decision(seq, replayId));
    if (res?.decision) {
      setDecision({ seq, row: res.decision });
    } else {
      setDecision(null);
    }
  };

  const counts = st?.counts ?? {};
  const phase = (st?.phase ?? (replayId ? "READY" : "NO SESSION")).toUpperCase();
  const price = st?.last_price?.close ?? st?.last_price?.bid ?? null;
  const regimeNow = st?.regime?.regime ?? (st?.regime_enabled ? "WARMUP" : replayId ? "—" : "DISABLED");
  const knownFrac = useMemo(() => {
    const k = st?.known_events ?? 0;
    const u = st?.unknown_events ?? 0;
    return k + u > 0 ? k / (k + u) : null;
  }, [st?.known_events, st?.unknown_events]);

  return (
    <Panel
      title="Historical replay (REPLAY_API v1)"
      accent
      right={
        <>
          {sessionChip && <span className="l4-chip">{sessionChip}</span>}
          <span className={`l4-chip ${PHASE_TONE[phase] ?? ""}`}>{phase}</span>
          {playing && <span className="l4-chip good">STEPPING ×{speed}</span>}
        </>
      }
    >
      <div className="l4-replay mc-replay">
        {/* Session contract row */}
        <div className="l4-replay__grid">
          <div className="l4-replay__field">
            <label htmlFor="rp-start">window start (local)</label>
            <input id="rp-start" className="input" type="datetime-local" value={start} onChange={(e) => setWin((w) => ({ ...w, start: e.target.value }))} />
          </div>
          <div className="l4-replay__field">
            <label htmlFor="rp-end">window end (local)</label>
            <input id="rp-end" className="input" type="datetime-local" value={end} onChange={(e) => setWin((w) => ({ ...w, end: e.target.value }))} />
          </div>
          <div className="l4-replay__field">
            <label htmlFor="rp-regime">regime classifier</label>
            <span className="l4-note" style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input id="rp-regime" type="checkbox" checked={regimeEnabled} onChange={(e) => setRegimeEnabled(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
              detect regime during replay
            </span>
          </div>
          <div className="l4-replay__field">
            <label htmlFor="rp-create">session</label>
            <div className="l4-transport">
              <button id="rp-create" className="btn primary" disabled={run.running} onClick={() => void createSession()}>
                {run.running ? "working…" : "⚗ Create replay session"}
              </button>
              {replayId && (
                <span className="l4-chip accent" title={replayId}>
                  id {replayId.slice(0, 14)}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Transport row — every control guarded on session id / in-flight */}
        <div className="l4-transport">
          <button className="btn" disabled={!replayId || run.running} onClick={() => void control("step_bar", { n: 1 })} title="advance one bar">
            ⏭ step
          </button>
          <button className={`btn ${playing ? "" : "primary"}`} disabled={!replayId} onClick={togglePlay} title="client-paced step_bar playback">
            {playing ? "⏸ pause" : "▶ play"}
          </button>
          <button className="btn" disabled={!replayId || run.running} onClick={() => void control("checkpoint")} title="snapshot session state">
            ⚑ checkpoint
          </button>
          <button className="btn danger" disabled={!replayId || run.running} onClick={() => setConfirmReset(true)} title="rewind cursor to window start">
            ⟲ reset
          </button>
          <label className="l4-note" htmlFor="rp-speed">
            speed
          </label>
          <input
            id="rp-speed"
            className="input"
            style={{ inlineSize: 56 }}
            type="number"
            min={1}
            max={20}
            value={speed}
            onChange={(e) => setSpeed(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
          />
          <input className="input" type="datetime-local" value={seekTime} onChange={(e) => setSeekTime(e.target.value)} aria-label="seek time" />
          <button
            className="btn"
            disabled={!replayId || !seekTime || run.running}
            onClick={() => {
              stopPlay();
              void control("seek", { seek_time: new Date(seekTime).toISOString() });
            }}
          >
            ⤳ seek
          </button>
          <button className="btn ghost" disabled={!replayId || run.running} onClick={() => void showReport()}>
            ☰ report
          </button>
        </div>

        {run.message && (
          <div className={`cmd-result ${run.ok ? "ok" : run.ok === false ? "fail" : ""}`}>
            {run.ok ? "✓" : run.ok === false ? "✕" : "ℹ"} {run.message}
          </div>
        )}

        {/* Cursor strip — bounded engine truth only */}
        <div className="grid cols-4">
          <div className="metric">
            <div className="k">cursor clock</div>
            <div className="v" style={{ fontSize: 14 }}>{st?.clock ? st.clock.replace("T", " ").slice(0, 19) : "—"}</div>
            <div className="s">{st?.status ? `status ${st.status}` : replayId ? "awaiting first state read…" : "no session"}</div>
          </div>
          <div className="metric">
            <div className="k">known / unknown events</div>
            <div className="v" style={{ fontSize: 14 }}>{st ? `${st.known_events ?? "—"} / ${st.unknown_events ?? "—"}` : "—"}</div>
            <div className="l4-known" role="img" aria-label="known vs unknown event ratio">
              <i className="l4-known__known" style={{ inlineSize: knownFrac !== null ? `${knownFrac * 100}%` : "0%" }} />
              <i className="l4-known__unknown" style={{ flexGrow: 1 }} />
            </div>
          </div>
          <div className="metric">
            <div className="k">bars · decisions · trades</div>
            <div className="v" style={{ fontSize: 14 }}>
              {counts.bars ?? "—"} · {counts.decisions ?? "—"} · {counts.trades ?? "—"}
            </div>
            <div className="s">
              equity {typeof st?.equity === "number" ? formatMoney(st.equity) : "—"} · price{" "}
              {typeof price === "number" ? formatNumber(price, 2) : "—"}
            </div>
          </div>
          <div className="metric">
            <div className="k">position / regime</div>
            <div className="v" style={{ fontSize: 14 }}>
              {st?.open_position ? `${st.open_position.direction ?? "?"} @ ${formatNumber(st.open_position.entry_price ?? null)}` : st ? "FLAT" : "—"}
            </div>
            <div className="s">
              regime {regimeNow}
              {typeof st?.regime?.probability === "number" ? ` (${formatPct(st.regime.probability * 100, 0)})` : ""}
            </div>
          </div>
        </div>

        {/* NO_TRADE / decision drill-down */}
        <div className="l4-transport">
          <input className="input" style={{ inlineSize: 90 }} aria-label="decision sequence" value={seqInput} onChange={(e) => setSeqInput(e.target.value)} />
          <button className="btn small" disabled={!replayId || run.running} onClick={() => void showDecision()}>
            ⌕ inspect decision seq
          </button>
          {decision && (
            <span className="l4-note">
              engine trace seq {decision.seq}
            </span>
          )}
        </div>
        {decision && (
          <dl className="kv">
            <dt>ts / action</dt>
            <dd>{decision.row.ts ?? "—"} · {decision.row.action ?? "—"}</dd>
            <dt>confidence</dt>
            <dd>{typeof decision.row.confidence === "number" ? decision.row.confidence.toFixed(4) : "—"}</dd>
            <dt>reason / blocked by</dt>
            <dd>{decision.row.reason_code ?? "—"}{decision.row.blocked_by ? ` ← ${decision.row.blocked_by}` : ""}</dd>
            <dt>stage / regime</dt>
            <dd>{decision.row.decision_stage ?? "—"} · {decision.row.regime ?? "—"}</dd>
            <dt>entry / SL / TP</dt>
            <dd>{[decision.row.entry, decision.row.stop_loss, decision.row.take_profit].map((v) => (typeof v === "number" ? v.toFixed(2) : "—")).join(" / ")}</dd>
            <dt>risk accepted</dt>
            <dd>{decision.row.risk_accepted === null || decision.row.risk_accepted === undefined ? "—" : String(decision.row.risk_accepted)}</dd>
            <dt>probs (N/B/S/W)</dt>
            <dd>{decision.row.probs?.map((p) => p.toFixed(3)).join(" | ") ?? "—"}</dd>
          </dl>
        )}
        {report && (
          <dl className="kv">
            <dt>report · decisions / trades</dt>
            <dd>{String(report.decisions ?? "—")} / {String(report.trades ?? "—")}</dd>
            <dt>wins / losses</dt>
            <dd>{String(report.wins ?? "—")} / {String(report.losses ?? "—")}</dd>
            <dt>pnl usd</dt>
            <dd className={typeof report.pnl_usd === "number" && report.pnl_usd >= 0 ? "pnl-pos" : "pnl-neg"}>
              {typeof report.pnl_usd === "number" ? formatMoney(report.pnl_usd) : "—"}
            </dd>
            <dt>equity end</dt>
            <dd>{typeof report.equity_end === "number" ? formatMoney(report.equity_end) : "—"}</dd>
            {Object.entries(report.gate_distribution ?? {}).slice(0, 8).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>gate · {k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        )}

        <div className="l4-note">
          No-future-data rule: every value above is engine truth up to the cursor — future events exist as a count, never as a payload the chart or strategy can read.
          Replay is a research surface: the backend refuses it while execution_mode=LIVE and restores the pre-replay mode on exit.
        </div>
      </div>

      {confirmReset && (
        <ConfirmModal
          title="Reset replay cursor"
          danger={false}
          confirmLabel="⟲ Reset cursor"
          busy={run.running}
          onCancel={() => setConfirmReset(false)}
          onConfirm={() => {
            setConfirmReset(false);
            stopPlay();
            void control("reset");
          }}
        >
          <div>
            Rewinds the session cursor to the window start and clears the report view. Local research state only — no broker call, no ledger row, no engine side effect.
          </div>
        </ConfirmModal>
      )}
    </Panel>
  );
}
