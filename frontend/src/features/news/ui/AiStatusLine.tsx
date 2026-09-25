/**
 * AiStatusLine — one honest line about LLM readiness for news analysis.
 *
 * The backend exposes `state` (AVAILABLE / NOT_CONFIGURED / UNAVAILABLE /
 * MISCONFIGURED / UNKNOWN) plus provider/model only. Nothing else is shown and
 * nothing is inferred: a missing state word renders UNKNOWN, not "OK".
 */

import { StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { NewsAiStatusResponse } from "../types";

export function ArticleAiStatusLine({ status }: { status: NewsAiStatusResponse["ai_status"] }) {
  const t = useI18n((s) => s.t);
  if (!status) {
    return (
      <div className="small muted">
        {t(
          "news.ai.not_reported",
          "AI status not reported yet — the endpoint may be unavailable on this build.",
        )}
      </div>
    );
  }
  const hint =
    status.state === "NOT_CONFIGURED" || status.state === "MISCONFIGURED"
      ? t(
          "news.ai.hint_configure",
          "Configure an LLM provider in Strategy Factory settings to enable AI analysis; deterministic analysis and manual analyze still work.",
        )
      : status.state === "UNAVAILABLE"
        ? t(
            "news.ai.hint_unreachable",
            "Provider is configured but not currently reachable — local fallback analysis continues.",
          )
        : status.state === "AVAILABLE"
          ? t(
              "news.ai.hint_available",
              "LLM analysis available — AI analyze and auto-analysis produce AI summaries.",
            )
          : null;
  return (
    <div className="statline" style={{ alignItems: "center" }}>
      <StatusBadge status={status.state ?? "UNKNOWN"} />
      {status.provider && (
        <span>
          {t("news.ai.provider", "provider:")} <b className="inline-mono">{status.provider}</b>
        </span>
      )}
      {status.model && (
        <span>
          {t("news.ai.model", "model:")} <b className="inline-mono">{status.model}</b>
        </span>
      )}
      {status.detail && <span className="faint">{status.detail}</span>}
      {hint && <span className="muted">{hint}</span>}
    </div>
  );
}
