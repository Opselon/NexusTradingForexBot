/**
 * PromotionControls — Champion/Challenger promotion lifecycle panel.
 *
 * Port of the legacy tab-governance "Promotion Controls (70D)" block
 * (Web/app.js showPromotionPreview / executePromotionUI /
 * freezePromotionsUI / unfreezePromotionsUI / reconcileRegistry /
 * loadPromotionStatus).
 *
 * SAFETY SEMANTICS (identical to legacy, server-enforced):
 *  - Promotion is NEVER automatic. Preview is a read-only gate read;
 *    execution requires an explicit approval token the operator supplies.
 *  - Every command carries an actor identity (mandatory backend-side) and
 *    records an immutable audit event.
 *  - Freeze/unfreeze are emergency controls; the frozen pill reflects the
 *    backend's own /api/models/governance/status verdict only.
 *  - A failed command renders the backend's error verbatim — promotion
 *    "blocked" is never reported as success.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { ConfirmModal } from "@/components/primitives";
import { governanceApi } from "../api";
import { useUiStore } from "@/stores/uiStore";
import { obj, str } from "../model";

const GATE_ORDER = ["oos", "robustness", "shadow", "drift", "liquidity"] as const;

export function PromotionControls() {
  const queryClient = useQueryClient();
  const pushToast = useUiStore((s) => s.pushToast);

  const [candidate, setCandidate] = useState("");
  const [token, setToken] = useState("");
  const [actor, setActor] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [commandMessage, setCommandMessage] = useState<string | null>(null);

  const statusQ = useQuery({
    queryKey: ["governance", "status"],
    queryFn: ({ signal }) => governanceApi.governanceStatus(signal),
    refetchInterval: 30_000,
    retry: false,
  });

  const previewQ = useQuery({
    queryKey: ["governance", "promotion-preview", candidate],
    queryFn: async ({ signal }) => {
      setPreviewError(null);
      const res = await governanceApi.promotionPreview(candidate, "", signal);
      if (!res.available) {
        throw new Error(res.reason || res.error ? String(res.error) : "preview unavailable");
      }
      return res;
    },
    enabled: candidate.trim() !== "" && previewError === null,
    retry: false,
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["governance"] });
  };

  const execMut = useMutation({
    mutationFn: () =>
      governanceApi.executePromotion({
        model_id: candidate.trim(),
        model_version: "",
        actor: actor.trim(),
        reason: "operator promotion via governance UI",
        approval_token: token.trim(),
      }),
    onSuccess: async (data) => {
      if (data.available) {
        const promoId = str(obj(data.transition).promotion_id) ?? str(obj(data.transition).id) ?? "?";
        setCommandMessage(`PROMOTION COMMITTED — audit record ${promoId}`);
        pushToast("ok", `Promotion committed (${promoId}).`);
        setToken("");
        await invalidate();
      } else {
        const msg = data.error
          ? typeof data.error === "string"
            ? data.error
            : `${data.error.code ?? "BLOCKED"}: ${data.error.message ?? "promotion refused"}`
          : data.reason ?? "promotion blocked by the governance gate";
        setCommandMessage(`PROMOTION BLOCKED: ${msg}`);
        pushToast("fail", msg);
      }
    },
    onError: (e: unknown) => {
      setCommandMessage(`PROMOTION BLOCKED: ${e instanceof Error ? e.message : "request failed"}`);
      pushToast("fail", "Promotion execution failed.");
    },
  });

  const freezeMut = useMutation({
    mutationFn: (freeze: boolean) =>
      freeze
        ? governanceApi.freeze({ actor: actor.trim(), reason: "operator freeze from UI" })
        : governanceApi.unfreeze({ actor: actor.trim(), reason: "operator unfreeze from UI" }),
    onSuccess: async (data) => {
      if (data.available) {
        pushToast("ok", actor.trim() ? `Emergency ${freezeMut.variables ? "freeze" : "unfreeze"} recorded.` : "Emergency control recorded.");
        await invalidate();
      } else {
        pushToast("fail", String(data.reason ?? data.error ?? "command refused"));
      }
    },
    onError: (e: unknown) => pushToast("fail", e instanceof Error ? e.message : "emergency control failed"),
  });

  const reconcileMut = useMutation({
    mutationFn: () => governanceApi.reconcileRegistry(),
    onSuccess: async (data) => {
      if (data.available) {
        pushToast("ok", "Registry reconciled against the on-disk artifact store.");
        await invalidate();
      } else {
        pushToast("fail", String(data.reason ?? data.error ?? "reconcile refused"));
      }
    },
    onError: (e: unknown) => pushToast("fail", e instanceof Error ? e.message : "reconcile failed"),
  });

  const promo = obj(statusQ.data?.promotion);
  const frozen = Boolean(promo.frozen);
  const canExecute = candidate.trim() !== "" && token.trim() !== "" && actor.trim() !== "" && !frozen;

  return (
    <Panel
      title="Promotion controls (70D)"
      right={
        statusQ.isPending ? (
          <Skeleton count={1} height={16} />
        ) : statusQ.data && statusQ.data.available !== false ? (
          <span className={`badge ${frozen ? "bad" : "good"}`}>
            {frozen ? "promotion frozen" : "promotions enabled"}
          </span>
        ) : (
          <StatusBadge status="UNAVAILABLE" />
        )
      }
      tight
    >
      <div className="small muted" style={{ marginBottom: 10 }}>
        Promotion is NEVER automatic. Preview reads fresh gates; execution requires an explicit approval token, an
        operator identity for the audit trail, and records an immutable audit event.
      </div>

      <div style={{ display: "grid", gap: 8 }}>
        <label className="tiny muted" style={{ display: "grid", gap: 4 }}>
          operator identity (audited)
          <input
            className="input"
            placeholder="e.g. operator@desk"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
        </label>
        <label className="tiny muted" style={{ display: "grid", gap: 4 }}>
          candidate model_id
          <input
            className="input inline-mono"
            placeholder="candidate model_id"
            value={candidate}
            onChange={(e) => setCandidate(e.target.value)}
          />
        </label>
        <label className="tiny muted" style={{ display: "grid", gap: 4 }}>
          approval token (required to execute)
          <input
            className="input inline-mono"
            type="password"
            placeholder="approval token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
      </div>

      <div className="l3-toolbar" style={{ marginTop: 10, flexWrap: "wrap" }}>
        <button
          className="btn small"
          disabled={!candidate.trim()}
          onClick={() => {
            setPreviewError(null);
            setCommandMessage(null);
            void previewQ.refetch();
          }}
        >
          {previewQ.isFetching ? "previewing…" : "Preview"}
        </button>
        <button
          className="btn small primary"
          disabled={!canExecute || execMut.isPending}
          onClick={() => setConfirmOpen(true)}
          title={
            frozen
              ? "promotions are frozen — unfreeze first"
              : !canExecute
                ? "candidate + approval token + operator identity are all required"
                : "POST /api/models/promotion/execute"
          }
        >
          {execMut.isPending ? "promoting…" : "Promote"}
        </button>
        <button
          className="btn small"
          disabled={freezeMut.isPending || !actor.trim()}
          onClick={() => void freezeMut.mutateAsync(true)}
        >
          Freeze
        </button>
        <button
          className="btn small"
          disabled={freezeMut.isPending || !actor.trim()}
          onClick={() => void freezeMut.mutateAsync(false)}
        >
          Unfreeze
        </button>
        <button
          className="btn small"
          disabled={reconcileMut.isPending}
          onClick={() => void reconcileMut.mutateAsync()}
        >
          {reconcileMut.isPending ? "reconciling…" : "Reconcile registry"}
        </button>
      </div>

      {commandMessage && (
        <div
          className={`tiny ${commandMessage.startsWith("PROMOTION BLOCKED") ? "pnl-neg" : "muted"}`}
          style={{ marginTop: 10, fontFamily: "var(--mono)" }}
        >
          {commandMessage}
        </div>
      )}

      {previewError && (
        <ErrorState message={previewError} onRetry={() => { setPreviewError(null); void previewQ.refetch(); }} />
      )}

      {candidate.trim() !== "" && previewQ.isPending && <Skeleton count={4} />}

      {candidate.trim() !== "" && previewQ.isError && !previewError && (
        <ErrorState
          message={previewQ.error instanceof Error ? previewQ.error.message : "preview failed"}
          onRetry={() => { setPreviewError(null); void previewQ.refetch(); }}
        />
      )}

      {previewQ.data?.preview ? <PreviewBody preview={previewQ.data.preview} /> : null}

      {candidate.trim() === "" && !commandMessage && (
        <EmptyState message="No preview yet — enter a candidate and click Preview." />
      )}

      {confirmOpen && (
        <ConfirmModal
          title="Confirm — PROMOTE TO CHAMPION"
          danger
          confirmLabel="⚠ Promote (final)"
          busy={execMut.isPending}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => {
            setConfirmOpen(false);
            void execMut.mutateAsync();
          }}
        >
          <div>
            <b>Impact:</b> promotion is final and audited. The candidate <span className="inline-mono">{candidate.trim()}</span>{" "}
            becomes the serving champion immediately; the previous champion is retained for rollback.
            <div className="small muted" style={{ marginTop: 8 }}>
              The backend re-verifies every gate at execution time — a stale preview does not entitle a promotion, and a
              frozen state refuses it outright.
            </div>
          </div>
        </ConfirmModal>
      )}
    </Panel>
  );
}

/** Read-only render of the preview payload (gates, verification, rollback). */
function PreviewBody({ preview }: { preview: Record<string, unknown> }) {
  const p = obj(preview);
  const gates = obj(p.gates);
  const verification = obj(p.verification);
  const champ = obj(p.current_champion);
  const cand = obj(p.candidate);
  const rollback = obj(p.rollback);
  const schema = obj(p.schema);
  const eligible = verification.eligible === true;

  return (
    <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
      <div className="small">
        <strong>PROMOTION PREVIEW (read-only)</strong>
      </div>
      <dl className="kv">
        <dt>champion</dt>
        <dd className="inline-mono tiny">
          {str(champ.model_id) ?? "?"} @ {str(champ.version) ?? "?"} · hash{" "}
          {(str(champ.artifact_hash) ?? "?").slice(0, 12)}
        </dd>
        <dt>candidate</dt>
        <dd className="inline-mono tiny">
          {str(cand.model_id) ?? "?"} @ {str(cand.version) ?? "?"} · schema {str(cand.schema) ?? "?"}
        </dd>
        <dt>schema</dt>
        <dd className="tiny">
          champion={str(schema.champion) ?? "?"} candidate={str(schema.candidate) ?? "?"}
        </dd>
      </dl>
      <div className="grid cols-2">
        {GATE_ORDER.map((g) => {
          const st = String(gates[g] ?? "UNKNOWN").toUpperCase();
          return (
            <div key={g} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="tiny muted" style={{ flex: 1 }}>
                {g}
              </span>
              <StatusBadge status={st} />
            </div>
          );
        })}
      </div>
      <dl className="kv">
        <dt>rollback</dt>
        <dd className="tiny">{rollback.available ? `AVAILABLE -> ${str(rollback.target) ?? ""}` : "NONE"}</dd>
        <dt>eligible</dt>
        <dd>
          <span className={`badge ${eligible ? "good" : "bad"}`}>{eligible ? "YES" : "NO"}</span>
          {verification.reason ? <span className="tiny muted" style={{ marginInlineStart: 8 }}>{String(verification.reason)}</span> : null}
        </dd>
      </dl>
      {p.locked ? (
        <div className="l3-note warn">WAIT: another promotion is currently in progress (lock held).</div>
      ) : null}
    </div>
  );
}
