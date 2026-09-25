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
import { useI18n } from "@/stores/i18nStore";
import { CommandResultLine } from "./lane5Kit";
import { commandVerdict } from "../model";
import { researchUseCases } from "../useCases";

type Pending =
  | { kind: "promote"; strategyId: string; target: "SHADOW" | "ACTIVE" }
  | { kind: "text"; label: string; danger: boolean; run: () => Promise<unknown> }
  | null;

export default function ResearchCommands({ strategyId }: { strategyId: string | null }) {
  const t = useI18n((s) => s.t);
  const cmd = useMutationFeedback();
  const [pending, setPending] = useState<Pending>(null);
  const [actor, setActor] = useState("operator");

  const runVerdict = async (label: string, fn: () => Promise<unknown>) => {
    await cmd.run(async () => {
      const res = await fn();
      const v = commandVerdict(res, t);
      return { ok: v.ok, success: v.ok, message: `${label}: ${v.message}`, status: v.ok ? 200 : 500 };
    });
  };

  const guarded = (label: string, fn: () => Promise<unknown>, danger = true, needsTarget = false) => {
    if (needsTarget && !strategyId) {
      void cmd.run(async () => ({
        ok: false,
        success: false,
        message: `${label}: ${t(
          "research.cmd.select_first",
          "select a strategy row first (the backend requires strategy_id).",
        )}`,
        status: 400,
      }));
      return;
    }
    setPending({ kind: "text", label, danger, run: fn });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() => guarded(t("research.cmd.lbl_discover", "Discover"), () => researchUseCases.discover(), false)}
        >
          {t("research.cmd.btn_discovery", "Run discovery")}
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() => guarded(t("research.cmd.lbl_validate", "Validate"), () => researchUseCases.validate(strategyId ?? ""), false, true)}
        >
          {t("research.cmd.btn_validate", "Validate selected")}
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() => guarded(t("research.cmd.lbl_selfheal", "Self-heal research state"), () => researchUseCases.selfHeal())}
        >
          {t("research.cmd.btn_selfheal", "Self-heal")}
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() => guarded(t("research.cmd.lbl_repair", "Repair outcomes"), () => researchUseCases.repairOutcomes())}
        >
          {t("research.cmd.btn_repair", "Repair outcomes")}
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() =>
            guarded(t("research.cmd.lbl_recover_dry", "Recover missing outcomes (dry-run classify)"), () =>
              researchUseCases.recoverMissingOutcomes(true),
              false,
            )
          }
        >
          {t("research.cmd.btn_recover_dry", "Recover outcomes (dry run)")}
        </button>
        <button
          className="btn small"
          disabled={cmd.state.running}
          onClick={() =>
            guarded(t("research.cmd.lbl_recover_write", "Recover missing outcomes (write)"), () =>
              researchUseCases.recoverMissingOutcomes(false),
            )
          }
        >
          {t("research.cmd.btn_recover_write", "Recover outcomes (write)")}
        </button>
        <button
          className="btn small danger"
          disabled={cmd.state.running || !strategyId}
          onClick={() => setPending({ kind: "promote", strategyId: strategyId ?? "", target: "SHADOW" })}
        >
          {t("research.cmd.btn_promote_shadow", "Promote → SHADOW")}
        </button>
        <button
          className="btn small danger"
          disabled={cmd.state.running || !strategyId}
          onClick={() => setPending({ kind: "promote", strategyId: strategyId ?? "", target: "ACTIVE" })}
        >
          {t("research.cmd.btn_promote_active", "Promote → ACTIVE")}
        </button>
      </div>
      <div className="tiny muted">
        {t(
          "research.cmd.promo_note",
          "Promotion requires an explicit actor id (recorded in the validation lineage). Illegal lifecycle jumps are refused by the registry state machine — the backend decides, the UI only asks.",
        )}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label className="tiny muted" htmlFor="research-actor">
          {t("research.cmd.actor", "actor")}
        </label>
        <input id="research-actor" className="input" style={{ width: 180 }} value={actor} onChange={(e) => setActor(e.target.value)} />
      </div>
      <CommandResultLine state={cmd.state} />

      {pending?.kind === "text" && (
        <ConfirmModal
          title={t("research.cmd.confirm_title", "Confirm: {s}", { s: pending.label })}
          danger={pending.danger}
          confirmLabel={t("research.cmd.send", "Send command")}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const target = pending;
            setPending(null);
            await runVerdict(target.label, target.run);
          }}
        >
          <div className="small">
            {t("research.cmd.runs_pre", "This runs ")}
            <b>{pending.label}</b>
            {t(
              "research.cmd.runs_post",
              " on the live backend. The engine ledger is append-only; the command can add derived rows but never rewrites immutable evidence.",
            )}
          </div>
        </ConfirmModal>
      )}

      {pending?.kind === "promote" && (
        <ConfirmModal
          title={t("research.cmd.promote_title", "Confirm promotion → {s}", { s: pending.target })}
          danger
          confirmLabel={t("research.cmd.promote_to", "Promote to {s}", { s: pending.target })}
          busy={cmd.state.running}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            const target = pending;
            setPending(null);
            await runVerdict(t("research.cmd.lbl_promote", "Promote {s}", { s: target.target }), () =>
              researchUseCases.promote(target.strategyId, target.target, actor.trim(), `ui:lane5 ${target.target}`),
            );
          }}
        >
          <div className="confirm-box">
            <div className="small">
              {t("research.cmd.strategy_pre", "strategy ")}<b className="inline-mono">{pending.strategyId}</b> → <b>{pending.target}</b>
            </div>
            <div className="row">
              <label className="tiny muted" htmlFor="promote-actor">
                {t("research.cmd.actor_backend", "actor (required by backend)")}
              </label>
              <input id="promote-actor" className="input" style={{ width: 200 }} value={actor} onChange={(e) => setActor(e.target.value)} />
            </div>
            <div className="note">
              {t(
                "research.cmd.active_note",
                "ACTIVE means real dispatch authority in LIVE mode. The registry rejects unvalidated or rejected strategies regardless of this click.",
              )}
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
