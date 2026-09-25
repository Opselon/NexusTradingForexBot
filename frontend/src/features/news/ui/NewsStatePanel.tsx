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
            ? t("news.state.auto_refused", "Auto-analysis refused: {e}", { e: res.error ?? t("news.state.backend_rejection", "backend rejection") })
            : t("news.state.auto_result", "Auto analysis {state} (worker={w}, runtime v{v}) — backend-confirmed.", { state: res.enabled ? t("news.state.enabled", "ENABLED") : t("news.state.disabled", "DISABLED"), w: res.worker_enabled === null || res.worker_enabled === undefined ? t("news.state.worker_unknown", "unknown") : res.worker_enabled ? t("news.state.worker_on", "on") : t("news.state.worker_off", "off"), v: res.runtime_version ?? "?" }),
        );
      },
      onError: (e) => {
        setCmdErr(true);
        setCmdNote(t("news.state.auto_failed", "Auto-analysis toggle failed: {e}", { e: asErrorText(e) }));
      },
    });
  };

  return (
    <Panel
      title={t("news.state.title", "News intelligence state")}
      accent
      right={
        <>
          <button className="btn small" onClick={runRefresh} disabled={refreshMut.isPending}>
            {refreshMut.isPending ? t("news.state.fetching", "fetching…") : t("news.state.fetch", "Fetch news")}
          </button>
          <button className="btn small danger" onClick={() => setHealOpen(true)} disabled={healMut.isPending}>
            {healMut.isPending ? t("news.state.healing", "healing…") : t("news.state.self_heal", "Self-heal")}
          </button>
        </>
      }
    >
      {stateQuery.isPending ? (
        <Skeleton count={2} height={54} />
      ) : stateQuery.isError ? (
        <>
          <ErrorState
            message={stateQuery.error instanceof ApiError || stateQuery.error instanceof Error ? stateQuery.error.message : t("news.state.endpoint_failed", "News state endpoint failed")}
            onRetry={() => stateQuery.refetch()}
          />
          <div className="small muted">{t("news.state.engine_maybe_off", "The news engine may be disabled — check the engine toggle below.")}</div>
        </>
      ) : (
        <>
          <div className="grid cols-4">
            <MetricCard
              label={t("news.state.metric_state", "State (backend)")}
              value={<StatusBadge status={ns?.state ?? "UNKNOWN"} />}
              tone={highImpact ? "neg" : "dim"}
              sub={ns?.stale ? t("news.state.stale_banner", "⚠ context marked STALE by backend") : t("news.state.freshness", "freshness {p}", { p: ns?.freshness !== null && ns?.freshness !== undefined ? formatPct(ns.freshness * 100, 0) : "—" })}
            />
            <MetricCard
              label={t("news.state.metric_bull_bear", "Bullish / Bearish")}
              value={`${ns?.bullish_score != null ? formatPct(ns.bullish_score * 100, 0) : "—"} / ${ns?.bearish_score != null ? formatPct(ns.bearish_score * 100, 0) : "—"}`}
              tone="dim"
              sub={t("news.state.confidence", "confidence {p}", { p: ns?.confidence != null ? formatPct(ns.confidence * 100, 0) : "—" })}
            />
            <MetricCard
              label={t("news.state.metric_xauusd", "XAUUSD relevance")}
              value={ns?.xauusd_relevance != null ? formatPct(ns.xauusd_relevance * 100, 0) : "—"}
              tone="dim"
              sub={t("news.state.usd_relevance", "USD relevance {p}", { p: ns?.usd_relevance != null ? formatPct(ns.usd_relevance * 100, 0) : "—" })}
            />
            <MetricCard
              label={t("news.state.metric_events", "Active events")}
              value={ns?.active_event_count ?? "—"}
              tone="dim"
              sub={t("news.state.context_at", "context {when}", { when: ns?.timestamp ? formatDateTime(ns.timestamp) : "—" })}
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
          {toggleMut.isPending ? t("news.state.applying", "applying…") : enabled ? t("news.state.engine_on", "NEWS ENGINE ON") : t("news.state.engine_off", "NEWS ENGINE OFF")}
        </button>
        <button
          type="button"
          className={`news-switch ${autoEnabled ? "on" : ""}`}
          onClick={() => setAuto(!autoEnabled)}
          disabled={autoMut.isPending}
          aria-pressed={autoEnabled}
        >
          <span className={`conn-dot ${autoEnabled ? "connected" : "failed"}`} style={{ background: autoEnabled ? "var(--green)" : "var(--text-faint)", boxShadow: "none" }} />
          {autoMut.isPending ? t("news.state.applying", "applying…") : autoEnabled ? t("news.state.auto_on", "AUTO ANALYSIS ON") : t("news.state.auto_off", "AUTO ANALYSIS OFF")}
        </button>
        <span className="timestamp-note">
          {t("news.state.engine_meta", "engine v{v} · auto worker {w}", { v: toggleQuery.data?.runtime_version ?? "—", w: autoQuery.data?.worker_enabled === null || autoQuery.data?.worker_enabled === undefined ? t("news.state.worker_unknown", "unknown") : autoQuery.data.worker_enabled ? t("news.state.worker_on", "on") : t("news.state.worker_off", "off") })}
        </span>
        <span className="spacer" />
        <FreshnessNote updatedAtMs={stateQuery.dataUpdatedAt ?? null} label={t("news.fresh.state", "state")} />
      </div>
      {cmdNote && <div className={`news-status-line ${cmdErr ? "err" : ""}`}>{cmdNote}</div>}
      <div className="tiny muted">
        {t("news.state.hot_reload_note", "Hot-reload note: toggles persist through runtime_config (validated, restart-persistent). News can never force a trade — the gate is bounded regardless of this switch.")}
      </div>

      {healOpen && (
        <ConfirmModal
          title={t("news.state.heal_title", "Self-heal news derived state")}
          confirmLabel={t("news.state.heal_confirm", "Rebuild derived state")}
          busy={healMut.isPending}
          onConfirm={runHeal}
          onCancel={() => setHealOpen(false)}
        >
          <div>
            {t("news.state.heal_body_1", "Rebuilds the ")}<b className="inline-mono">{t("news.state.heal_body_2", "derived")}</b>{t("news.state.heal_body_3", " news tables (impacts / consensus / entities / topics) from the stored raw articles and re-derives the live context. Raw article history is ")}<b>{t("news.state.heal_body_4", "not altered")}</b>{t("news.state.heal_body_5", ". Use it when the feed and the derived panels disagree.")}
          </div>
          {healMut.isError && <div className="cmd-result fail">{asErrorText(healMut.error)}</div>}
        </ConfirmModal>
      )}
      {!stateQuery.isPending && !stateQuery.data && !stateQuery.isError && <EmptyState message={t("news.state.empty", "No news state yet.")} />}
    </Panel>
  );
}
