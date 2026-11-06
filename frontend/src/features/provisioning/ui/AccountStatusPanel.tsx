/**
 * AccountStatusPanel — global account / entitlement / diagnostics status.
 *
 * Lifecycle spec §3/§12: ONE summary component reading THE startup handshake
 * (never its own polling loop). Shows the live lease verdict (GrantedFresh …
 * LeaseExpired), the official-model update state, and the diagnostics
 * delivery evidence (GREEN delivered / AMBER pending / GRAY disabled).
 *
 * This component NEVER decides entitlement locally — it renders what the
 * platform attested. A stale snapshot is labeled as such.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { diagnosticsApi, platformApi, type StartupSnapshot } from "../api";

const REFRESH_MS = 60_000;

type LeaseTone = "good" | "warn" | "bad" | "neutral";
type DeliveryTone = "good" | "warn" | "neutral";

/** Numeric benchmark fields rendered with honest green/amber coloring (§7). */
const BENCHMARK_FIELDS: Array<[string, string]> = [
  ["win_rate", "win rate"],
  ["profit_factor", "profit factor"],
  ["expectancy", "net expectancy"],
  ["max_drawdown", "max drawdown"],
  ["sample_size", "evaluated trades"],
  ["sharpe", "sharpe ratio"],
];

function formatNumber(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return null;
}

function leaseTone(decision: StartupSnapshot["entitlement_decision"]): LeaseTone {
  switch (decision) {
    case "GrantedFresh":
      return "good";
    case "GrantedStale":
      return "warn";
    case "Revoked":
    case "LeaseExpired":
      return "bad";
    case "Pending":
      return "warn";
    default:
      return "neutral";
  }
}

export interface AccountStatusPanelProps {
  /** Latest handshake snapshot (shared with the page; no duplicate fetch). */
  snapshot: StartupSnapshot | null;
  /** Notifies the page when the official invitation should show. */
  onInvitationChange?: (offer: boolean) => void;
}

export function AccountStatusPanel({ snapshot, onInvitationChange }: AccountStatusPanelProps) {
  const t = useI18n((s) => s.t);
  const [delivery, setDelivery] = useState<{
    last_acknowledged_id?: string;
    pending_record_count?: number;
    last_failure_code?: string;
    consent_enabled?: boolean;
  } | null>(null);
  const [offer, setOffer] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    onInvitationChange?.(offer);
  }, [offer, onInvitationChange]);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    try {
      const inv = await platformApi.invitation();
      if (inv.success && mountedRef.current) setOffer(Boolean(inv.offer));
    } catch {
      /* transient — next tick retries */
    }
    try {
      const del = await diagnosticsApi.delivery();
      if (del.success && del.delivery && mountedRef.current) setDelivery(del.delivery);
    } catch {
      /* transient */
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(refresh, REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  const decisionKey = (d: StartupSnapshot["entitlement_decision"]): string => {
    switch (d) {
      case "GrantedFresh":
        return "provisioning.lifecycle.fresh";
      case "GrantedStale":
        return "provisioning.lifecycle.stale";
      case "Revoked":
        return "provisioning.lifecycle.revoked";
      case "Pending":
        return "provisioning.lifecycle.pending";
      case "LeaseExpired":
        return "provisioning.lifecycle.lease_expired";
      default:
        return "provisioning.lifecycle.unknown";
    }
  };

  const modelLine = (): { text: string; tone: LeaseTone } | null => {
    if (!snapshot) return null;
    if (snapshot.update_available === true) {
      return {
        text: t("provisioning.lifecycle.update_available", "New official version available"),
        tone: "warn",
      };
    }
    if (snapshot.update_available === false && snapshot.installed_model_version) {
      return {
        text: t("provisioning.lifecycle.model_current", "Latest official model installed"),
        tone: "good",
      };
    }
    if (snapshot.latest_model?.available) {
      return {
        text: t("provisioning.lifecycle.no_model", "No official model installed"),
        tone: "warn",
      };
    }
    return null;
  };

  const deliveryTone = (): DeliveryTone => {
    if (!delivery) return "neutral";
    if (delivery.consent_enabled === false) return "neutral";
    if (
      delivery.last_acknowledged_id &&
      (delivery.pending_record_count ?? 0) === 0 &&
      !delivery.last_failure_code
    ) {
      return "good";
    }
    return "warn";
  };

  const deliveryLabel = (): string => {
    if (!delivery) return "";
    if (delivery.consent_enabled === false) {
      return t("provisioning.lifecycle.delivery_off", "Diagnostics shipping disabled");
    }
    return deliveryTone() === "good"
      ? t("provisioning.lifecycle.delivery_ok", "Error reports delivered")
      : t("provisioning.lifecycle.delivery_pending", "Error reports pending delivery");
  };

  const model = modelLine();
  const tone = leaseTone(snapshot?.entitlement_decision ?? null);

  return (
    <Panel title={t("provisioning.lifecycle.title", "Account & Official Model Status")}>
      <div className="pv-platform-row">
        <span className={`badge ${tone}`}>
          <span className="state-label">
            {snapshot
              ? t(decisionKey(snapshot.entitlement_decision), snapshot.entitlement_decision ?? "Unknown")
              : t("provisioning.platform.state.loading", "Checking…")}
          </span>
        </span>
        {snapshot?.github_login ? (
          <span className="muted small">
            {t("provisioning.platform.connected_as", "Connected as")}{" "}
            <strong>@{snapshot.github_login}</strong>
          </span>
        ) : null}
      </div>

      {snapshot?.reason ? <p className="small faint">{snapshot.reason}</p> : null}

      {model ? (
        <div className="pv-platform-row">
          <span className={`badge ${model.tone}`}>
            <span className="state-label">{model.text}</span>
          </span>
          {snapshot?.latest_model?.version ? (
            <span className="muted small">v{snapshot.latest_model.version}</span>
          ) : null}
          {snapshot?.installed_model_version ? (
            <span className="muted small">
              {t("provisioning.invitation.benchmark", "installed")}: v{snapshot.installed_model_version}
            </span>
          ) : null}
        </div>
      ) : null}

      {delivery ? (
        <div className="pv-platform-row">
          <span className={`badge ${deliveryTone()}`}>
            <span className="state-label">{deliveryLabel()}</span>
          </span>
          {delivery.pending_record_count && delivery.pending_record_count > 0 ? (
            <span className="muted small">{delivery.pending_record_count} pending</span>
          ) : null}
        </div>
      ) : null}

      {snapshot?.latest_model?.benchmark ? (
        <details className="pv-benchmark-details">
          <summary className="small muted">
            {t("provisioning.invitation.benchmark", "Benchmark evidence")}
          </summary>
          <table className="pv-benchmark-table small">
            <tbody>
              {BENCHMARK_FIELDS.map(([key, label]) => {
                const raw = (snapshot.latest_model?.benchmark as Record<string, unknown> | null)?.[key];
                const shown = formatNumber(raw);
                if (shown === null) return null;
                // Honest coloring: only numeric thresholds shipped by the
                // server decide green/amber — the UI never grades words.
                const numeric = typeof raw === "number" ? raw : Number.NaN;
                const honest: LeaseTone =
                  key === "profit_factor"
                    ? Number.isFinite(numeric) && numeric > 1
                      ? "good"
                      : "warn"
                    : "neutral";
                return (
                  <tr key={key}>
                    <td className="muted">{label}</td>
                    <td>
                      <span className={`badge ${honest}`}>{shown}</span>
                    </td>
                  </tr>
                );
              })}
              {snapshot.latest_model?.benchmark?.evaluation_period ? (
                <tr>
                  <td className="muted">evaluation period</td>
                  <td>{String(snapshot.latest_model.benchmark.evaluation_period)}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
          <p className="small faint">
            {t(
              "provisioning.invitation.no_evidence",
              "No benchmark evidence was published for this release.",
            )}
          </p>
        </details>
      ) : (
        <p className="small faint">
          {t(
            "provisioning.invitation.no_evidence",
            "No benchmark evidence was published for this release.",
          )}
        </p>
      )}
    </Panel>
  );
}
