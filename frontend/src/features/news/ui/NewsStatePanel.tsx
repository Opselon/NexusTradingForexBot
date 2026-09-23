/**
 * News state panel — backend state word, scores, engine/auto toggles, refresh
 * and the guarded self-heal command.
 *
 * Toggle truth is re-read from the POST response and the polled toggle-state
 * query — the UI never keeps its own idea of "enabled". Self-heal rebuilds
 * derived tables, so it goes through a confirm modal first.
 */

import { useMemo, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { ConfirmModal, EmptyState, ErrorState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { formatDateTime, formatPct } from "@/lib/format";
import { ApiError } from "@/types/api";
import {
  useNewsAutoState,
  useNewsAutoToggle,
  useNewsRefresh,
  useNewsSelfHeal,
  useNewsState,
  useNewsToggle,
  useNewsToggleState,
} from "../hooks";
import { newsStateTone, refreshVerdict, selfHealVerdict } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

export function NewsStatePanel() {
  const t = useI18n((s) => s.t);
  const stateQuery = useNewsState();
  const toggleQuery = useNewsToggleState();
  const autoQuery = useNewsAutoState();
  const refreshMut = useNewsRefresh();
  const healMut = useNewsSelfHeal();
  const toggleMut = useNewsToggle();
  const autoMut = useNewsAutoToggle();
  const [healOpen, setHealOpen] = useState(false);
  const [cmdNote, setCmdNote] = useState<string | null>(null);
  const [cmdErr, setCmdErr] = useState(false);

  const ns = stateQuery.data;
  const enabled = toggleQuery.data?.enabled ?? ns?.available ?? false;
  const autoEnabled = autoQuery.data?.enabled ?? false;

  // perf: the banner visibility derivation (backend state word → tone) is a
  // pure model fn over the polled state payload; memo once per identity.
  const highImpact = useMemo(
    () => newsStateTone(ns?.state) === "bad",
    [ns?.state],
  );

  const runRefresh = (): void => {
    refreshMut.mutate(undefined, {
      onSuccess: (res) => {
        const v = refreshVerdict(res);
        setCmdErr(!v.ok);
        setCmdNote(v.message(t));
      },
      onError: (e) => {
        setCmdErr(true);
        setCmdNote(`Refresh failed: ${asErrorText(e)}`);
      },
    });
  };

  const runHeal = (): void => {
    healMut.mutate(undefined, {
      onSuccess: (res) => {
        const v = selfHealVerdict(res);
        setCmdErr(!v.ok);
        setCmdNote(v.message(t));
        setHealOpen(false);
      },
      onError: (e) => {
        setCmdErr(true);
        setCmdNote(`Self-heal failed: ${asErrorText(e)}`);
        setHealOpen(false);
      },
    });
  };

  const setEngine = (next: boolean): void => {
    toggleMut.mutate(next, {
      onSuccess: (res) => {
        setCmdErr(res.success === false);
        setCmdNote(
          res.success === false
            ? `Engine toggle refused: ${res.error ?? "backend rejection"}`
            : `News engine ${res.enabled ? "ENABLED" : "DISABLED"} (runtime v${res.runtime_version ?? "?"}) — backend-confirmed.`,
        );
      },
      onError: (e) => {
        setCmdErr(true);
        setCmdNote(`Engine toggle failed: ${asErrorText(e)}`);
      },
    });
  };

  const setAuto = (next: boolean): void => {
    autoMut.mutate(next, {
      onSuccess: (res) => {
        setCmdErr(res.success === false);
        setCmdNote(
          res.success === false
            ? `Auto-analysis refused: ${res.error ?? "backend rejection"}`
            : `Auto analysis ${res.enabled ? "ENABLED" : "DISABLED"} (worker=${res.worker_enabled === null || res.worker_enabled === undefined ? "unknown" : res.worker_enabled ? "on" : "off"}, runtime v${res.runtime_version ?? "?"}) — backend-confirmed.`,
        );
      },
      onError: (e) => {
        setCmdErr(true);
        setCmdNote(`Auto-analysis toggle failed: ${asErrorText(e)}`);
      },
    });
  };

  return (
    <Panel
      title="News intelligence state"
      accent
      right={
        <>
          <button className="btn small" onClick={runRefresh} disabled={refreshMut.isPending}>
            {refreshMut.isPending ? "fetching…" : "Fetch news"}
          </button>
          <button className="btn small danger" onClick={() => setHealOpen(true)} disabled={healMut.isPending}>
            {healMut.isPending ? "healing…" : "Self-heal"}
          </button>
        </>
      }
    >
      {stateQuery.isPending ? (
        <Skeleton count={2} height={54} />
      ) : stateQuery.isError ? (
        <>
          <ErrorState
            message={stateQuery.error instanceof ApiError || stateQuery.error instanceof Error ? stateQuery.error.message : "News state endpoint failed"}
            onRetry={() => stateQuery.refetch()}
          />
          <div className="small muted">The news engine may be disabled — check the engine toggle below.</div>
        </>
      ) : (
        <>
          <div className="grid cols-4">
            <MetricCard
              label="State (backend)"
              value={<StatusBadge status={ns?.state ?? "UNKNOWN"} />}
              tone={highImpact ? "neg" : "dim"}
              sub={ns?.stale ? "⚠ context marked STALE by backend" : `freshness ${ns?.freshness !== null && ns?.freshness !== undefined ? formatPct(ns.freshness * 100, 0) : "—"}`}
            />
            <MetricCard
              label="Bullish / Bearish"
              value={`${ns?.bullish_score != null ? formatPct(ns.bullish_score * 100, 0) : "—"} / ${ns?.bearish_score != null ? formatPct(ns.bearish_score * 100, 0) : "—"}`}
              tone="dim"
              sub={`confidence ${ns?.confidence != null ? formatPct(ns.confidence * 100, 0) : "—"}`}
            />
            <MetricCard
              label="XAUUSD relevance"
              value={ns?.xauusd_relevance != null ? formatPct(ns.xauusd_relevance * 100, 0) : "—"}
              tone="dim"
              sub={`USD relevance ${ns?.usd_relevance != null ? formatPct(ns.usd_relevance * 100, 0) : "—"}`}
            />
            <MetricCard
              label="Active events"
              value={ns?.active_event_count ?? "—"}
              tone="dim"
              sub={`context ${ns?.timestamp ? formatDateTime(ns.timestamp) : "—"}`}
            />
          </div>
          {highImpact && ns?.state ? (
            <div className="banner down" style={{ marginTop: 10 }}>
              Backend news state is {ns.state} — high-impact evidence is live; the bounded gate may veto entries.
            </div>
          ) : null}
        </>
      )}

      <div className="news-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className={`news-switch ${enabled ? "on" : ""}`}
          onClick={() => setEngine(!enabled)}
          disabled={toggleMut.isPending}
          aria-pressed={enabled}
        >
          <span className={`conn-dot ${enabled ? "connected" : "disconnected"}`} />
          {toggleMut.isPending ? "applying…" : enabled ? "NEWS ENGINE ON" : "NEWS ENGINE OFF"}
        </button>
        <button
          type="button"
          className={`news-switch ${autoEnabled ? "on" : ""}`}
          onClick={() => setAuto(!autoEnabled)}
          disabled={autoMut.isPending}
          aria-pressed={autoEnabled}
        >
          <span className={`conn-dot ${autoEnabled ? "connected" : "failed"}`} style={{ background: autoEnabled ? "var(--green)" : "var(--text-faint)", boxShadow: "none" }} />
          {autoMut.isPending ? "applying…" : autoEnabled ? "AUTO ANALYSIS ON" : "AUTO ANALYSIS OFF"}
        </button>
        <span className="timestamp-note">
          engine v{toggleQuery.data?.runtime_version ?? "—"} · auto worker {autoQuery.data?.worker_enabled === null || autoQuery.data?.worker_enabled === undefined ? "unknown" : autoQuery.data.worker_enabled ? "on" : "off"}
        </span>
        <span className="spacer" />
        <FreshnessNote updatedAtMs={stateQuery.dataUpdatedAt ?? null} label="state" />
      </div>
      {cmdNote && <div className={`news-status-line ${cmdErr ? "err" : ""}`}>{cmdNote}</div>}
      <div className="tiny muted">
        Hot-reload note: toggles persist through runtime_config (validated, restart-persistent). News can never force a trade — the gate is bounded regardless of this switch.
      </div>

      {healOpen && (
        <ConfirmModal
          title="Self-heal news derived state"
          confirmLabel="Rebuild derived state"
          busy={healMut.isPending}
          onConfirm={runHeal}
          onCancel={() => setHealOpen(false)}
        >
          <div>
            Rebuilds the <b className="inline-mono">derived</b> news tables (impacts / consensus / entities / topics) from the stored raw articles and
            re-derives the live context. Raw article history is <b>not altered</b>. Use it when the feed and the derived panels disagree.
          </div>
          {healMut.isError && <div className="cmd-result fail">{asErrorText(healMut.error)}</div>}
        </ConfirmModal>
      )}
      {!stateQuery.isPending && !stateQuery.data && !stateQuery.isError && <EmptyState message="No news state yet." />}
    </Panel>
  );
}
