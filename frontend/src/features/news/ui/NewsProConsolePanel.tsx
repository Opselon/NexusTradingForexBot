/**
 * Pro Auto Console — React parity with the legacy `#news-pro-console` block
 * (Web/news_intelligence.js `_pollProStatus` / `_pollProConsole` /
 * `proAnalyzeAll` / `proPurge` + the tab-news markup in Web/index.html).
 *
 * Endpoints wired (all verified against news_intelligence_routes.py):
 *   GET  /api/news/pro/status          -> counts, provider, latest_ai, console ring
 *   GET  /api/news/pro/console         -> live pass/answer/error trace (cursor poll 1.5s)
 *   GET  /api/news/pro/latest-answers  -> newest news_ai_analysis rows (table, verbatim)
 *   POST /api/news/pro/analyze-all     -> run_pro_cycle drain (15s server cooldown)
 *   POST /api/news/pro/purge           -> soft IRRELEVANT report / bounded hard delete
 *   POST /api/news/auto-prune          -> recoverable IRRELEVANT classification
 *
 * Invariants: every number on screen comes from a polled response. "Analyzing"
 * is the real mutation pending flag, never a fake progress bar. Refusals
 * (NEWS_UNAVAILABLE / COOLDOWN / INTERNAL_ERROR) render the backend's own
 * code + message + request_id.
 *
 * i18n: status notes resolve at render (lazy (t) => string closures re-translate
 * on a language switch). Backend enum words
 * (AUTO route LLM/LOCAL, kind codes, sentiment, status) stay verbatim.
 */

import { useEffect, useRef, useState } from "react";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import {
  useAutoPruneSafe,
  useNewsAutoState,
  useNewsProAnswers,
  useNewsProStatus,
  useProAnalyzeAll,
  useProConsoleLog,
  useProPurge,
} from "../hooks";
import {
  analyzeAllVerdict,
  autoPruneSafeVerdict,
  proKindLabel,
  proKindTone,
  purgeVerdict,
} from "../model";
import type { ProConsoleEntry } from "../proTypes";
import type { NewsAiAnalysisRow } from "../types";
import { FreshnessNote, asErrorText, noteText, type NoteText } from "./shared";

type ConfirmKind = "analyze-all" | "purge-soft" | "purge-hard" | "auto-prune" | null;

/** One command note: lazy closures re-translate on render, plain strings keep
 *  the language they were built with (verdict helpers applied at the call site). */
type ProNote = { text: NoteText; err: boolean };

const ANSWERS_LIMIT = 8;
const LOG_LIMIT = 200;

export function NewsProConsolePanel() {
  const t = useI18n((s) => s.t);
  const status = useNewsProStatus();
  const answers = useNewsProAnswers(ANSWERS_LIMIT);
  const autoState = useNewsAutoState();
  const log = useProConsoleLog({ limit: LOG_LIMIT });

  const analyzeAll = useProAnalyzeAll();
  const purge = useProPurge();
  const prune = useAutoPruneSafe();

  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [note, setNote] = useState<ProNote | null>(null);

  /* Legacy startProConsole polls while the tab is visible; the React section
   * mounts per route, so poll from mount to unmount only. */
  useEffect(() => {
    log.start();
    return () => log.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const counts = status.data?.counts;
  const prov = status.data?.provider;
  const latestAi = status.data?.latest_ai ?? null;
  const consoleRing = status.data?.console;
  const autoOn = autoState.data?.enabled ?? null;

  const autoWord =
    autoOn === null
      ? t("news.pro.auto_unknown", "AUTO ?")
      : autoOn
        ? t("news.pro.auto_on", "AUTO ON")
        : t("news.pro.auto_off", "AUTO OFF");
  const routeWord = prov?.provider_available ? "LLM" : "LOCAL";
  const badge = t("news.pro.badge", "{auto} · {route}", { auto: autoWord, route: routeWord });

  const providerLine = prov
    ? prov.provider_available
      ? [prov.provider_name, prov.model].filter(Boolean).join(" ") ||
        t("news.pro.provider_ready", "LLM provider ready")
      : prov.ai_status?.state
        ? t("news.pro.provider_fallback", "{state} → local fallback", { state: prov.ai_status.state })
        : t("news.pro.unavailable_fallback", "LLM unavailable → local fallback")
    : t("news.pro.provider_none", "provider —");

  const busy = analyzeAll.isPending || purge.isPending || prune.isPending;
  const logRef = useAutoScroll(log.entries.length);

  const runAnalyzeAll = (): void => {
    analyzeAll.mutate({ limit: 200 }, {
      onSuccess: (res) => {
        const v = analyzeAllVerdict(res);
        setNote({ err: !v.ok, text: v.message(t) });
        setConfirm(null);
        void log.pollNow();
        void status.refetch();
      },
      onError: (e) => {
        setNote({
          err: true,
          text: (t) => t("news.pro.analyze_all_failed", "Analyze ALL failed: {e}", { e: asErrorText(e, t) }),
        });
        setConfirm(null);
      },
    });
  };

  const runPurge = (hard: boolean): void => {
    purge.mutate({ hardDelete: hard, limit: 5000 }, {
      onSuccess: (res) => {
        const v = purgeVerdict(res);
        setNote({ err: !v.ok, text: v.message(t) });
        setConfirm(null);
        void log.pollNow();
        void status.refetch();
      },
      onError: (e) => {
        setNote({
          err: true,
          text: (t) => t("news.pro.purge_failed", "Purge failed: {e}", { e: asErrorText(e, t) }),
        });
        setConfirm(null);
      },
    });
  };

  const runPrune = (): void => {
    prune.mutate(undefined, {
      onSuccess: (res) => {
        const v = autoPruneSafeVerdict(res);
        setNote({ err: !v.ok, text: v.message(t) });
        setConfirm(null);
        void log.pollNow();
        void status.refetch();
      },
      onError: (e) => {
        setNote({
          err: true,
          text: (t) => t("news.pro.prune_failed", "Auto-prune failed: {e}", { e: asErrorText(e, t) }),
        });
        setConfirm(null);
      },
    });
  };

  const cmdError =
    analyzeAll.error ?? purge.error ?? prune.error ?? null;

  return (
    <Panel
      title={t("news.pro.title", "Pro auto console")}
      right={
        <>
          <span
            className={`news-pro-badge ${autoOn ? "on" : ""}`}
            title={t("news.pro.badge_title", "auto-analysis toggle (backend /api/news/auto-analysis) + LLM routing")}
          >
            {badge}
          </span>
          <button className="btn small" onClick={() => { void log.pollNow(); void status.refetch(); void answers.refetch(); }}>
            {t("common.refresh", "Refresh")}
          </button>
          <button className="btn small primary" onClick={() => setConfirm("analyze-all")} disabled={busy}>
            {analyzeAll.isPending ? t("news.pro.draining", "draining…") : t("news.pro.analyze_all", "Analyze ALL")}
          </button>
          <button className="btn small" onClick={log.clear} disabled={log.entries.length === 0}>
            {t("news.pro.clear", "Clear")}
          </button>
          <button className="btn small" onClick={() => setConfirm("auto-prune")} disabled={busy}>
            {prune.isPending ? t("news.pro.pruning", "pruning…") : t("news.pro.prune", "Auto-prune")}
          </button>
          <button className="btn small" onClick={() => setConfirm("purge-soft")} disabled={busy}>
            {t("news.pro.purge_junk", "Purge junk (count)")}
          </button>
          <button className="btn small danger" onClick={() => setConfirm("purge-hard")} disabled={busy}>
            {purge.isPending ? t("news.pro.purging", "purging…") : t("news.pro.hard_purge", "Hard purge")}
          </button>
        </>
      }
    >
      <div className="news-pro-meta">
        <span className="inline-mono tiny faint">
          {t("news.pro.meta", "GET /api/news/pro/status · /console · /latest-answers — polled live (log {n}-row window)", { n: LOG_LIMIT })}
        </span>
        <span className="spacer" />
        <FreshnessNote updatedAtMs={status.dataUpdatedAt ?? null} label="pro-status" staleAfterMs={60_000} />
      </div>

      {status.isPending ? (
        <Skeleton count={2} height={54} />
      ) : status.isError ? (
        <ErrorState message={asErrorText(status.error, t)} onRetry={() => status.refetch()} />
      ) : (
        <div className="grid cols-4">
          <MetricCard
            label={t("news.pro.pending", "Pending analysis")}
            value={counts?.pending ?? "—"}
            tone="dim"
            sub={t("news.pro.pending_sub", "total articles {n}", { n: counts?.total ?? "—" })}
          />
          <MetricCard
            label={t("news.pro.route", "Worker route")}
            value={<span className={prov?.provider_available ? "news-pro-ok" : "news-pro-warn"}>{prov?.provider_available ? "LLM" : "LOCAL"}</span>}
            tone="dim"
            sub={providerLine}
          />
          <MetricCard
            label={t("news.pro.ring", "Console ring")}
            value={consoleRing?.size ?? "—"}
            tone="dim"
            sub={t("news.pro.ring_sub", "latest seq {n} · cursor {c}", { n: consoleRing?.latest_seq ?? "—", c: log.cursor })}
          />
          <MetricCard
            label={t("news.pro.counts", "Status counts")}
            value={<span className="news-pro-counts">{jsonish(counts?.status_counts)}</span>}
            tone="dim"
            sub={
              autoOn === null
                ? t("news.pro.auto_not_reported", "auto toggle not reported")
                : autoOn
                  ? t("news.pro.auto_on_sub", "auto-analysis ON (worker drains each cycle)")
                  : t("news.pro.auto_off_sub", "auto-analysis OFF (manual drains only)")
            }
          />
        </div>
      )}

      {latestAi && (
        <div className="news-pro-last" title={latestAi.summary ?? ""}>
          <span className="tiny uppercase faint">{t("news.pro.last_answer", "last PRO answer")}</span>{" "}
          {truncate(latestAi.summary, 180) || "—"}
          {latestAi.sentiment ? ` · ${latestAi.sentiment}` : ""}
          {latestAi.analyzed_at ? ` · ${formatDateTime(latestAi.analyzed_at)}` : ""}
        </div>
      )}

      {note && <div className={`news-status-line ${note.err ? "err" : ""}`}>{noteText(note.text, t)}</div>}

      <div className="section-title" style={{ marginTop: 10 }}>
        {t("news.pro.live_title", "Live pass / answer trace")}
        <span className="tiny faint inline-mono" style={{ marginInlineStart: 8, textTransform: "none", letterSpacing: 0 }}>
          {log.polling
            ? t("news.pro.polling", "polling /api/news/pro/console every 1.5s")
            : t("news.pro.polling_paused", "polling paused")}
        </span>
      </div>
      {log.error && (
        <div className="news-status-line err">{t("news.pro.console_prefix", "console: ")}{log.error}</div>
      )}
      <div tabIndex={0} className="news-pro-log" ref={logRef}>
        {log.entries.length === 0 ? (
          <div className="news-pro-log-empty">
            {log.polling
              ? t(
                  "news.pro.log_idle",
                  "Console idle — every worker pass and LLM answer will appear here (toggle AUTO or press Analyze ALL).",
                )
              : t("news.pro.log_not_started", "Polling not started yet.")}
          </div>
        ) : (
          log.entries.map((e, i) => <ConsoleRow key={`${e.seq ?? "n"}-${i}`} entry={e} />)
        )}
      </div>

      <div className="section-title" style={{ marginTop: 14 }}>
        {t("news.pro.answers_title", "Latest PRO answers")}
        <span className="tiny faint inline-mono" style={{ marginInlineStart: 8, textTransform: "none", letterSpacing: 0 }}>
          GET /api/news/pro/latest-answers?limit={ANSWERS_LIMIT}
        </span>
      </div>
      {answers.isPending ? (
        <Skeleton count={3} height={30} />
      ) : answers.isError ? (
        <ErrorState message={asErrorText(answers.error, t)} onRetry={() => answers.refetch()} />
      ) : (answers.data ?? []).length === 0 ? (
        <EmptyState
          message={t("news.pro.answers_empty", "No AI answers stored yet.")}
          hint={t(
            "news.pro.answers_empty_hint",
            "Analyze an article or enable auto-analysis — rows come from news_ai_analysis verbatim.",
          )}
        />
      ) : (
        <DataTable
          headers={[
            { label: t("news.pro.h_analyzed", "analyzed") },
            { label: t("news.pro.h_summary", "summary") },
            { label: t("news.pro.h_sentiment", "sentiment") },
            { label: t("news.pro.h_provider", "provider / model") },
            { label: t("news.pro.h_status", "status") },
          ]}
        >
          {(answers.data ?? []).map((a: NewsAiAnalysisRow, i) => (
            <tr key={a.ai_analysis_id ?? `${a.article_id}-${i}`}>
              <td>{a.analyzed_at ? formatDateTime(a.analyzed_at) : "—"}</td>
              <td className="news-pro-summary" title={a.summary ?? ""}>{truncate(a.summary, 160) || "—"}</td>
              <td>{a.sentiment || "—"}</td>
              <td className="inline-mono tiny">{[a.provider, a.model].filter(Boolean).join(" ") || "—"}</td>
              <td><StatusBadge status={a.analysis_status ?? "UNKNOWN"} /></td>
            </tr>
          ))}
        </DataTable>
      )}

      {confirm && (
        <ConfirmModal
          title={
            confirm === "analyze-all"
              ? t("news.pro.confirm_analyze_title", "Drain ALL pending articles (PRO cycle)")
              : confirm === "purge-hard"
                ? t("news.pro.confirm_hard_title", "Hard purge — DELETE IRRELEVANT articles")
                : confirm === "purge-soft"
                  ? t("news.pro.confirm_soft_title", "Purge junk — count only")
                  : t("news.pro.confirm_prune_title", "Auto-prune unrelated news")
          }
          danger={confirm === "purge-hard"}
          confirmLabel={
            confirm === "analyze-all"
              ? t("news.pro.confirm_analyze_label", "Run full drain")
              : confirm === "purge-hard"
                ? t("news.pro.confirm_hard_label", "Delete IRRELEVANT rows")
                : confirm === "purge-soft"
                  ? t("news.pro.confirm_soft_label", "Count candidates")
                  : t("news.pro.confirm_prune_label", "Mark unrelated IRRELEVANT")
          }
          busy={busy}
          onConfirm={() => {
            if (confirm === "analyze-all") runAnalyzeAll();
            else if (confirm === "purge-hard") runPurge(true);
            else if (confirm === "purge-soft") runPurge(false);
            else runPrune();
          }}
          onCancel={() => setConfirm(null)}
        >
          {confirm === "analyze-all" && (
            <div>
              {t("news.pro.cycle_body_1", "Runs the same ")}
              <b className="inline-mono">run_pro_cycle</b>
              {t(
                "news.pro.cycle_body_2",
                " the worker uses: analyzes every unanalyzed article (gold-first, limit 200 per press) then junk-prunes. The backend enforces a ",
              )}
              <b>{t("news.pro.cycle_cooldown", "15s cooldown")}</b>
              {t(
                "news.pro.cycle_body_3",
                " between drains — a refused press shows the COOLDOWN code verbatim. Progress appears in the console trace, never as a fake bar.",
              )}
            </div>
          )}
          {confirm === "purge-soft" && (
            <div>
              {t("news.pro.soft_body_1", "Reports how many ")}
              <b className="inline-mono">IRRELEVANT</b>
              {t("news.pro.soft_body_2", " rows exist and how many fall inside the limit window (5000). ")}
              <b>{t("news.pro.soft_body_3", "Nothing is deleted")}</b>
              {t("news.pro.soft_body_4", " by a soft purge.")}
            </div>
          )}
          {confirm === "purge-hard" && (
            <div>
              <b>{t("news.pro.hard_body_1", "HARD DELETE")}</b>
              {t(
                "news.pro.hard_body_2",
                ": removes up to 5000 IRRELEVANT articles and their derived rows (analysis, AI answers, entities, topics, impacts, consensus) from the news DB. ",
              )}
              <b>{t("news.pro.hard_body_3", "This cannot be undone.")}</b>
            </div>
          )}
          {confirm === "auto-prune" && (
            <div>
              {t("news.pro.prune_body_1", "Marks low-importance / non-XAUUSD stories ")}
              <b className="inline-mono">IRRELEVANT</b>
              {t(
                "news.pro.prune_body_2",
                " using importance + relevance — originals stay preserved and recoverable from the Irrelevant filter. Idempotent: a second pass changes nothing.",
              )}
            </div>
          )}
          {cmdError && <div className="cmd-result fail">{asErrorText(cmdError, t)}</div>}
        </ConfirmModal>
      )}
    </Panel>
  );
}

/** One console entry — mirrors legacy `_proRow`: ts · LABEL · msg + answer/via/id extras. */
function ConsoleRow({ entry: e }: { entry: ProConsoleEntry }) {
  const t = useI18n((s) => s.t);
  const kind = e.kind ?? "log";
  const msg = e.msg ?? e.summary ?? "";
  const ts = String(e.ts ?? "").slice(11, 19);
  const answer = e.answer ? JSON.stringify(e.answer).slice(0, 220) : "";
  return (
    <div className={`news-pro-row ${proKindTone(kind)}`}>
      <span className="ts">{ts || "—"}</span>
      <span className="label">{proKindLabel(t, kind)}</span>
      <span className="msg" title={String(msg)}>{truncate(String(msg), 260)}</span>
      {answer && <span className="answer">{answer}</span>}
      {e.via && <span className="extra">{t("news.pro.via", "via: {v}", { v: String(e.via) })}</span>}
      {e.article_id && <span className="extra">#{String(e.article_id).slice(0, 10)}</span>}
      {e.sentiment && <span className="sent">{String(e.sentiment)}</span>}
      {e.provider && <span className="extra">{String(e.provider)}</span>}
    </div>
  );
}

/** status_counts dict -> compact "ACTIVE=210 · IRRELEVANT=90" text (backend keys only). */
function jsonish(counts: Record<string, number> | undefined): string {
  if (!counts) return "—";
  const parts = Object.entries(counts).map(([k, v]) => `${k}=${v}`);
  return parts.length ? parts.join(" · ") : "—";
}

function truncate(s: string | null | undefined, n: number): string {
  const t = String(s ?? "");
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** Keep the log pinned to the newest entry unless the user scrolled up to read. */
function useAutoScroll(count: number): (node: HTMLDivElement | null) => void {
  const elRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    const onScroll = (): void => {
      stickRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    const el = elRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [count]);
  return (node) => {
    elRef.current = node;
  };
}
