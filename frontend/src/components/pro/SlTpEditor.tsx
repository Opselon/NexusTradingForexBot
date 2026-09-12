/**
 * SlTpEditor — inline SL/TP modification for ONE position.
 *
 * Single sanctioned mutation path: `tradingApi.modifyPosition`
 * (POST /api/positions/modify → OrderLifecycleManager.modify_position_manual,
 * BUG-242 INV-004). No raw network calls in this file; the api layer owns the
 * transport. The payload always carries BOTH numeric levels because the
 * backend request model requires `stop_loss` + `take_profit` floats
 * (web/server.py ModifyPositionRequest) — an EMPTY input therefore means
 * "don't send a user value": that field falls back to the CURRENT BACKEND
 * value (re-sent unchanged), never invented (and never null→0.0 surprises
 * unless the backend itself reports 0/none).
 *
 * VALIDATION IS FORMAT-ONLY (opsChromeMath.parseNumericField): does this text
 * parse to a finite number? Nothing here judges trading semantics (side of
 * entry, broker stops level, RR...) — the backend owns those verdicts, and
 * its own words are displayed VERBATIM.
 *
 * NEVER OPTIMISTIC: a refusal leaves the UI untouched; even an ACCEPTED
 * command does not write the new SL/TP anywhere. The row keeps rendering
 * backend snapshot values until the next snapshot/frame arrives (we only
 * invalidate the read queries so the refetch happens).
 *
 * FEEDBACK BUS: results mirror into the ONE existing toast bus
 * (`useUiStore.pushToast`, the same store useMutationFeedback writes). The
 * hook itself is deliberately not reused here: its `res.ok && success !==
 * false` gate cannot read the legacy routes' `{success: bool}` bodies (they
 * carry no `ok` key), which would render a broker-ACCEPTED modify as a
 * refusal. Instead we apply the pinned legacy rule through
 * opsChromeMath.legacyMutationVerdict — backend payload decides, never the
 * HTTP 200 alone. (That hook gap is reported upstream as a risk.)
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { tradingApi } from "@/api/tradingApi";
import { ApiError } from "@/types/api";
import type { Position } from "@/types/domain";
import { legacyMutationVerdict, parseNumericField } from "@/lib/opsChromeMath";
import { formatPrice } from "@/lib/format";
import { useUiStore } from "@/stores/uiStore";

export interface SlTpEditorProps {
  position: Position;
  /** Shared price formatting digits from the snapshot (null → 2 default). */
  priceDigits?: number | null;
  /** Called after a backend-settled command (accepted true/false). */
  onSettled?: (accepted: boolean) => void;
  /** Render as an always-open form (default: compact "SL/TP" trigger button). */
  defaultOpen?: boolean;
}

interface Submission {
  ok: boolean;
  message: string;
  requestId: string | null;
}

export function SlTpEditor({ position, priceDigits = null, onSettled, defaultOpen = false }: SlTpEditorProps) {
  const digits = priceDigits ?? 2;
  const queryClient = useQueryClient();
  const pushToast = useUiStore((s) => s.pushToast);
  const [open, setOpen] = useState(defaultOpen);
  const [busy, setBusy] = useState(false);
  const [slText, setSlText] = useState("");
  const [tpText, setTpText] = useState("");
  const [verdict, setVerdict] = useState<Submission | null>(null);

  if (position.ticket === null || position.ticket === undefined) {
    return <span className="ops-sltp-hint">no ticket — nothing to modify</span>;
  }

  const ticket = position.ticket;

  const fieldProblem = (raw: string): string | null => {
    const parsed = parseNumericField(raw);
    return parsed.kind === "invalid" ? `not a number: "${parsed.raw}"` : null;
  };
  const slProblem = fieldProblem(slText);
  const tpProblem = fieldProblem(tpText);

  /** Resolve one input to the number actually sent (empty → current backend). */
  const resolve = (raw: string, current: number | null): number => {
    const parsed = parseNumericField(raw);
    if (parsed.kind === "ok") return parsed.value;
    return current ?? 0;
  };

  // Nothing typed → nothing to send (empty fields are not user values).
  const unchanged = parseNumericField(slText).kind !== "ok" && parseNumericField(tpText).kind !== "ok";
  const canSubmit = !busy && slProblem === null && tpProblem === null && !unchanged;

  const settle = (v: Submission): void => {
    setVerdict(v);
    pushToast(v.ok ? "ok" : "fail", v.ok ? `SL/TP #${ticket}: ${v.message}` : `SL/TP #${ticket} refused: ${v.message}`);
    onSettled?.(v.ok);
  };

  const submit = async (): Promise<void> => {
    if (!canSubmit) return;
    setBusy(true);
    setVerdict(null);
    try {
      const res = await tradingApi.modifyPosition({
        ticket,
        stop_loss: resolve(slText, position.sl),
        take_profit: resolve(tpText, position.tp),
      });
      const v = legacyMutationVerdict(res);
      settle({
        ok: v.ok,
        // verbatim backend words when present; fixed honest line when not
        message: v.message ?? (v.ok ? "accepted by backend" : "backend refused the command"),
        requestId: null,
      });
      if (v.ok) {
        // Refetch only — the ROW still shows the previous backend values until
        // real data says otherwise. Nothing is written locally.
        void queryClient.invalidateQueries({ queryKey: ["v1-positions"] });
        void queryClient.invalidateQueries({ queryKey: ["engine-snapshot"] });
      }
    } catch (e) {
      const apiErr = e instanceof ApiError ? e : null;
      const message = apiErr ? apiErr.message : e instanceof Error ? e.message : "modify request failed";
      settle({ ok: false, message, requestId: apiErr?.requestId ?? null });
    } finally {
      setBusy(false);
    }
  };

  const reset = (): void => {
    setSlText("");
    setTpText("");
    setVerdict(null);
    setOpen(false);
  };

  return (
    <div className="ops-sltp">
      {!open ? (
        <button className="btn small ghost ops-sltp-trigger" disabled={busy} onClick={() => setOpen(true)} title={`Modify SL/TP for #${ticket}`}>
          SL/TP
        </button>
      ) : (
        <div className="ops-sltp-form" role="group" aria-label={`SL/TP editor for position ${ticket}`}>
          <div className="ops-sltp-row">
            <span className="lab">SL</span>
            <input
              className="input"
              inputMode="decimal"
              type="text"
              placeholder={position.sl ? formatPrice(position.sl, digits) : "none"}
              value={slText}
              disabled={busy}
              onChange={(e) => setSlText(e.target.value)}
              aria-label={`New stop loss for position ${ticket}`}
            />
          </div>
          {slProblem && <div className="ops-sltp-invalid">{slProblem}</div>}
          <div className="ops-sltp-row">
            <span className="lab">TP</span>
            <input
              className="input"
              inputMode="decimal"
              type="text"
              placeholder={position.tp ? formatPrice(position.tp, digits) : "none"}
              value={tpText}
              disabled={busy}
              onChange={(e) => setTpText(e.target.value)}
              aria-label={`New take profit for position ${ticket}`}
            />
          </div>
          {tpProblem && <div className="ops-sltp-invalid">{tpProblem}</div>}

          <div className="ops-sltp-actions">
            <button className="btn small" disabled={busy} onClick={reset}>
              cancel
            </button>
            <button className="btn small primary" disabled={!canSubmit} onClick={() => void submit()}>
              {busy ? "sending…" : "send"}
            </button>
          </div>

          <div className="ops-sltp-hint">
            empty field keeps backend value · row updates only from the next snapshot/frame
          </div>

          {verdict && (
            <div className={`ops-sltp-verdict cmd-result ${verdict.ok ? "ok" : "fail"}`} role="status">
              {verdict.ok ? "✓" : "✕"} {verdict.message}
              {verdict.requestId ? ` (request_id: ${verdict.requestId})` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default SlTpEditor;
