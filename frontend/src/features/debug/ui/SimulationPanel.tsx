/**
 * SimulationPanel — Interactive Live Simulation (legacy parity).
 *
 * Port of the Web/index.html monitoring-tab "Interactive Live Simulation"
 * card (Web/app.js injectSimTick): three dispatch buttons that POST
 * /api/simulation/tick with a tick type.
 *
 * FORENSIC SAFETY (server-enforced, mirrored in the UI):
 * the endpoint is a NO-OP unless execution mode is explicitly SIMULATION or
 * PAPER — synthetic prices can never masquerade as production telemetry.
 * The UI surfaces the server's own refusal message verbatim and never
 * implies success when the backend refused.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Panel } from "@/components/primitives";
import { useUiStore } from "@/stores/uiStore";
import { simulationApi, type SimulationTickType } from "../simulationApi";

type Phase = "idle" | "dispatching" | "dispatched" | "refused" | "failed";

const BUTTONS: Array<{ type: SimulationTickType; label: string; hint: string; tone: string }> = [
  { type: "BUY_PRESSURE", label: "↑ Buy pressure", hint: "upward pressure tick", tone: "var(--green)" },
  { type: "SELL_PRESSURE", label: "↓ Sell pressure", hint: "downward pressure tick", tone: "var(--red)" },
  { type: "VOLATILE_SWEEP", label: "⌁ Liquidity sweep (stop hunt)", hint: "deep swing sweep", tone: "var(--amber)" },
];

export function SimulationPanel() {
  const pushToast = useUiStore((s) => s.pushToast);
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const tickMut = useMutation({
    mutationFn: (type: SimulationTickType) => simulationApi.injectTick(type),
    onMutate: () => {
      setPhase("dispatching");
      setMessage(null);
    },
    onSuccess: (data) => {
      if (data.success) {
        setPhase("dispatched");
        setMessage(data.message ?? "tick dispatched to the engine pipeline");
        pushToast("ok", "Simulation tick dispatched.");
        window.setTimeout(() => setPhase((p) => (p === "dispatched" ? "idle" : p)), 1_500);
      } else {
        // The backend refused (e.g. mode=LIVE). Never render "Dispatched".
        setPhase("refused");
        setMessage(data.message ?? "the engine refused the simulated tick");
        pushToast("fail", data.message ?? "Simulation tick refused.");
      }
    },
    onError: (e: unknown) => {
      setPhase("failed");
      setMessage(e instanceof Error ? e.message : "simulation dispatch failed");
      pushToast("fail", "Simulation dispatch failed.");
    },
  });

  const badge = {
    idle: { text: "Ready", cls: "badge good" },
    dispatching: { text: "Dispatching…", cls: "badge warn" },
    dispatched: { text: "Dispatched", cls: "badge good" },
    refused: { text: "Refused", cls: "badge bad" },
    failed: { text: "Failed", cls: "badge bad" },
  }[phase];

  return (
    <Panel
      title="Interactive live simulation"
      right={<span className={badge.cls}>{badge.text}</span>}
    >
      <div className="small muted" style={{ marginBottom: 10 }}>
        Inject mock ticks to test the model&apos;s live response — random walk, trending structure, or deep swing
        sweeps. The backend refuses injection unless the execution mode is SIMULATION/PAPER, so synthetic prices can
        never reach a live pipeline.
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ display: "flex", gap: 8 }}>
          {BUTTONS.slice(0, 2).map((b) => (
            <button
              key={b.type}
              className="btn small"
              style={{ flex: 1, borderColor: b.tone, color: b.tone }}
              disabled={tickMut.isPending}
              onClick={() => void tickMut.mutateAsync(b.type)}
              title={b.hint}
            >
              {b.label}
            </button>
          ))}
        </div>
        <button
          className="btn small"
          style={{ borderColor: BUTTONS[2]!.tone, color: BUTTONS[2]!.tone }}
          disabled={tickMut.isPending}
          onClick={() => void tickMut.mutateAsync(BUTTONS[2]!.type)}
          title={BUTTONS[2]!.hint}
        >
          {BUTTONS[2]!.label}
        </button>
      </div>
      {message && (
        <div
          className={`tiny ${phase === "refused" || phase === "failed" ? "pnl-neg" : "muted"}`}
          style={{ marginTop: 10, fontFamily: "var(--mono)" }}
        >
          {message}
        </div>
      )}
    </Panel>
  );
}
