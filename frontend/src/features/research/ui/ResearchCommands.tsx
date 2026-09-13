/**
 * Research — command bar (confirm-guarded operator actions).
 *
 * Every action posts to the backend and renders the backend's own verdict
 * inline (`cmd-result`); nothing is locally faked. Destructive/production
 * effects (promote, self-heal, outcome repair/recovery) go through ConfirmModal.
 */

import { useState } from "react";
import { ConfirmModal } from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { CommandResultLine } from "./lane5Kit";
import { commandVerdict } from "../model";
import { researchUseCases } from "../useCases";

type Pending =
  | { kind: "promote"; strategyId: string; target: "SHADOW" | "ACTIVE" }
  | { kind: "text"; label: string; danger: boolean; run: () => Promise<unknown> }
  | null;

export default function ResearchCommands({ strategyId }: { strategyId: string | null }) {
  const cmd = useMutationFeedback();
  const [pending, setPending] = useState<Pending>(null);
  const [actor, setActor] = useState("operator");

  const runVerdict = async (label: string, fn: () => Promise<unknown>) => {
    await cmd.run(async () => {
      const res = await fn();
      const v = commandVerdict(res);
      return { ok: v.ok, success: v.ok, message: `${label}: ${v.message}`, status: v.ok ? 200 : 500 };
    });
  };

  const guarded = (label: string, fn: () => Promise<unknown>, danger = true, needsTarget = false) => {
    if (needsTarget && !strategyId) {
      void cmd.run(async () => ({
        ok: false,
        success: false,
        message: `${label}: select a strategy row first (the backend requires strategy_id).`,
        status: 400,
      }));
      return;
    }
    setPending({ kind: "text", label, danger, run: fn });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn small" disabled={cmd.state.running} onClick={() => guarded("Discover", () => researchUseCases.discover(), false)}>
          Run discovery
        </button>
        <button className="btn small" disabled={cmd.state.running} onClick={() => guarded("Validate", () => researchUseCases.validate(strategyId ?? ""), false, true)}>
          Validate selected
        </button>
        <button className="btn small" disabled={cmd.state.running} onClick={() => guarded("Self-heal research state", () => researchUseCases.selfHeal())}>
          Self-heal
        </button>
        <button className="btn small" disabled={cmd.state.running} onClick={() => guarded("Repair outcomes", () => researchUseCases.repairOutcomes())}>
          Repair outcomes
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() => guarded("Recover missing outcomes (dry-run classify)", () => researchUseCases.recoverMissingOutcomes(true), false)}
        >
          Recover outcomes (dry run)
        </button>
        <button className="btn small" disabled={cmd.state.running} onClick={() => guarded("Recover missing outcomes (write)", () => researchUseCases.recoverMissingOutcomes(false))}>
          Recover outcomes (write)
        </button>
        <button
          className="btn small danger"
          disabled={cmd.state.running || !strategyId}
          onClick={() => setPending({ kind: "promote", strategyId: strategyId ?? "", target: "SHADOW" })}
        >
          Promote → SHADOW
        </button>
        <button
          className="btn small danger"
          disabled={cmd.state.running || !strategyId}
          onClick={() => setPending({ kind: "promote", strategyId: strategyId ?? "", target: "ACTIVE" })}
        >
          Promote → ACTIVE
        </button>
      </div>
      <div className="tiny muted">
        Promotion requires an explicit actor id (recorded in the validation lineage). Illegal lifecycle jumps are refused by the registry state
        machine — the backend decides, the UI only asks.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label className="tiny muted" htmlFor="research-actor">
          actor
        </label>
        <input id="research-actor" className="input" style={{ width: 180 }} value={actor} onChange={(e) => setActor(e.target.value)} />
      </div>
      <CommandResultLine state={cmd.state} />

      {pending?.kind === "text" && (
        <ConfirmModal
          title={`Confirm: ${pending.label}`}
          danger={pending.danger}
          confirmLabel="Send command"
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const target = pending;
            setPending(null);
            await runVerdict(target.label, target.run);
          }}
        >
          <div className="small">
            This runs <b>{pending.label}</b> on the live backend. The engine ledger is append-only; the command can add derived rows but never
            rewrites immutable evidence.
          </div>
        </ConfirmModal>
      )}

      {pending?.kind === "promote" && (
        <ConfirmModal
          title={`Confirm promotion → ${pending.target}`}
          danger
          confirmLabel={pending.target === "ACTIVE" ? "Promote to ACTIVE" : "Promote to SHADOW"}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const target = pending;
            setPending(null);
            await runVerdict(`Promote ${target.target}`, () =>
              researchUseCases.promote(target.strategyId, target.target, actor.trim(), `ui:lane5 ${target.target}`),
            );
          }}
        >
          <div className="confirm-box">
            <div className="small">
              strategy <b className="inline-mono">{pending.strategyId}</b> → <b>{pending.target}</b>
            </div>
            <div className="row">
              <label className="tiny muted" htmlFor="promote-actor">
                actor (required by backend)
              </label>
              <input id="promote-actor" className="input" style={{ width: 200 }} value={actor} onChange={(e) => setActor(e.target.value)} />
            </div>
            <div className="note">
              ACTIVE means real dispatch authority in LIVE mode. The registry rejects unvalidated or rejected strategies regardless of this click.
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
