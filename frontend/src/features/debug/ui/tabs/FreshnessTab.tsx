/**
 * Freshness tab — /api/debug/freshness: live engine view beside the
 * server-side no-cache re-diagnosis (frozen-at localization). Split layout;
 * both payloads are rendered verbatim by JsonView.
 */

import { FreshnessCaption, JsonView, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import { useI18n } from "@/stores/i18nStore";
import type { DebugFreshness } from "../../api";
import { useDebugFreshnessQuery } from "../../hooks";

export function FreshnessTab() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(60_000);
  const query = useDebugFreshnessQuery(poll.paused);
  return (
    <QuerySection<DebugFreshness>
      title={t("debug.freshness.title", "Live-inference frozen-state diagnostic (/api/debug/freshness)")}
      accent
      query={query}
      skeletonRows={4}
      emptyMessage={t("debug.freshness.empty", "Freshness diagnostic unavailable.")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={60_000} note={t("debug.freshness.note", "runs a live no-cache re-diagnosis server-side")} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => {
        if (!data.available) {
          return <div className="l3-note warn">{t("debug.freshness.unavailable", "available=false — reason: {reason} · frozen_at {at}", { reason: String(data.reason ?? "UNKNOWN"), at: String(data.frozen_at ?? "UNKNOWN") })}</div>;
        }
        return (
          <div className="l3-split dbg-split">
            <div className="dbg-pane">
              <div className="section-title">{t("debug.freshness.live_section", "live freshness (engine view)")}</div>
              <div tabIndex={0} className="l3-scroll dbg-json">
                <JsonView value={data.live_freshness ?? null} name="live_freshness" />
              </div>
            </div>
            <div className="dbg-pane">
              <div className="section-title">{t("debug.freshness.diag_section", "no-cache diagnostic (frozen-at localization)")}</div>
              <div tabIndex={0} className="l3-scroll dbg-json">
                <JsonView value={data.diagnostic ?? null} name="diagnostic" />
              </div>
              <div className="tiny faint" style={{ marginTop: 6 }}>
                {t("debug.freshness.checked_at", "checked_at {at}", { at: String(data.checked_at ?? "—") })}
              </div>
            </div>
          </div>
        );
      }}
    </QuerySection>
  );
}
