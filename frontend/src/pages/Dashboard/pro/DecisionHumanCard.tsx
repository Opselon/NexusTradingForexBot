/**
 * DecisionHumanCard — the pro "what is the engine doing and why" card.
 *
 * Human layer comes from lib/signal.ts explainSignal() (the NXSignal port):
 * exact-match reason translations only, unknown codes verbatim, and the
 * "0.0 confidence on a Guardian block = NOT CONSULTED" truth rule. When there
 * is no decision at all, explainSignal returns null and the card renders
 * UNKNOWN — never a fabricated "NO TRADE". Probability bars reuse the
 * existing ProbBar primitive and are labelled available:false when the
 * backend says the vector is not available.
 */

import type { EngineSnapshot } from "@/types/domain";
import { explainSignal } from "@/lib/signal";
import { ProbBar } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { formatTime } from "@/lib/format";

export function DecisionHumanCard({ snapshot }: { snapshot: EngineSnapshot }) {
  const t = useI18n((s) => s.t);
  const ex = explainSignal(
    {
      ai_decision: snapshot.ai_decision,
      ai_confidence: snapshot.ai_confidence,
      ai_reason: snapshot.ai_reason,
      live_freshness: snapshot.live_freshness,
    },
    t,
  );

  return (
    <div className="decision-card">
      <div style={{ minWidth: 130 }}>
        {ex ? (
          <>
            <div className={`big ${ex.tone}`}>{ex.label}</div>
            <div className="tiny faint" style={{ marginTop: 4 }}>
              {t("ux.signal.confidence", "Confidence")}: {ex.confidenceText}
            </div>
          </>
        ) : (
          <>
            <div className="big hold">—</div>
            <div className="tiny faint" style={{ marginTop: 4 }}>
              no decision reported (UNKNOWN, not &quot;no trade&quot;)
            </div>
          </>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {ex?.human && <div className="why">{ex.human}</div>}
        {ex?.detail && <div className="why-detail">{ex.detail}</div>}
        {ex && ex.confidenceKind === "not_consulted" && (
          <div className="why-detail">
            {t("ux.signal.not_available", "Signal not available")} — {t("ux.signal.model_not_consulted", "the model was not consulted on this cycle")}
          </div>
        )}
        <div className="meta">
          {ex?.decision && <span className="badge neutral inline-mono">{ex.decision}</span>}
          {snapshot.ai_reason && <span className="badge unknown inline-mono" title="raw backend reason code">{snapshot.ai_reason}</span>}
          {snapshot.regime && <span className="badge neutral">{snapshot.regime}</span>}
          {ex?.freshness && <span className="badge neutral">freshness {ex.freshness}</span>}
          {snapshot.model.inference_timestamp && (
            <span className="tiny faint inline-mono">inference {formatTime(snapshot.model.inference_timestamp)}</span>
          )}
        </div>
        <div style={{ marginTop: 8 }}>
          <ProbBar
            rows={[
              { label: "P(BUY)", value: snapshot.probs.available ? snapshot.probs.buy ?? null : null, tone: "buy" },
              { label: "P(SELL)", value: snapshot.probs.available ? snapshot.probs.sell ?? null : null, tone: "sell" },
              { label: "P(NO)", value: snapshot.probs.available ? snapshot.probs.no_trade ?? null : null, tone: "flat" },
            ]}
          />
          {!snapshot.probs.available && (
            <div className="tiny faint" style={{ marginTop: 2 }}>
              probability vector: not available from backend (available=false)
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
