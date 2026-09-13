/**
 * AiStatusLine — one honest line about LLM readiness for news analysis.
 *
 * The backend exposes `state` (AVAILABLE / NOT_CONFIGURED / UNAVAILABLE /
 * MISCONFIGURED / UNKNOWN) plus provider/model only. Nothing else is shown and
 * nothing is inferred: a missing state word renders UNKNOWN, not "OK".
 */

import { StatusBadge } from "@/components/primitives";
import type { NewsAiStatusResponse } from "../types";

export function ArticleAiStatusLine({ status }: { status: NewsAiStatusResponse["ai_status"] }) {
  if (!status) {
    return <div className="small muted">AI status not reported yet — the endpoint may be unavailable on this build.</div>;
  }
  const hint =
    status.state === "NOT_CONFIGURED" || status.state === "MISCONFIGURED"
      ? "Configure an LLM provider in Strategy Factory settings to enable AI analysis; deterministic analysis and manual analyze still work."
      : status.state === "UNAVAILABLE"
        ? "Provider is configured but not currently reachable — local fallback analysis continues."
        : status.state === "AVAILABLE"
          ? "LLM analysis available — AI analyze and auto-analysis produce AI summaries."
          : null;
  return (
    <div className="statline" style={{ alignItems: "center" }}>
      <StatusBadge status={status.state ?? "UNKNOWN"} />
      {status.provider && <span>provider: <b className="inline-mono">{status.provider}</b></span>}
      {status.model && <span>model: <b className="inline-mono">{status.model}</b></span>}
      {status.detail && <span className="faint">{status.detail}</span>}
      {hint && <span className="muted">{hint}</span>}
    </div>
  );
}
