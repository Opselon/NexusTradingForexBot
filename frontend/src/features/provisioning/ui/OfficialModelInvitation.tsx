/**
 * OfficialModelInvitation — official-model adoption prompt (spec §6).
 *
 * Rendering rules honored here:
 * - Shown ONLY when the server says the per-release gate allows it
 *   (`platformApi.invitation()` — the user's declined/later decisions live
 *   server-side per release, so this card never re-asks the same release).
 * - YES installs the official model through the existing provisioning
 *   download (the fail-closed authorization gate still applies).
 * - NO keeps the current model; the decision is recorded per release.
 * - Later = bounded cooldown (server-side). No modal fatigue.
 * - Benchmark evidence is rendered from the platform payload only; this
 *   component never fabricates a comparison (§6.2: a local model may be
 *   excellent — the card says so explicitly).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { platformApi, type StartupSnapshot } from "../api";

export interface OfficialModelInvitationProps {
  /** True when the server's per-release gate allows the invitation. */
  offer: boolean;
  /** Latest handshake snapshot (model catalog + entitlement). */
  snapshot: StartupSnapshot | null;
  /** Installs the official model (page owns the download flow). */
  onInstall: () => void;
  /** Setup recovery is surfaced separately (AccountStatusPanel). */
}

export function OfficialModelInvitation({ offer, snapshot, onInstall }: OfficialModelInvitationProps) {
  const t = useI18n((s) => s.t);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Re-arm a local dismissal when the gated release changes.
  useEffect(() => {
    setDismissed(false);
  }, [snapshot?.latest_model?.version, offer]);

  const decide = useCallback(
    async (decision: "declined" | "later") => {
      const version = snapshot?.latest_model?.version;
      if (!version) return;
      setBusy(true);
      try {
        await platformApi.invitationDecide(version, decision);
        if (mountedRef.current) setDismissed(true);
      } finally {
        if (mountedRef.current) setBusy(false);
      }
    },
    [snapshot?.latest_model?.version],
  );

  if (!offer || dismissed || !snapshot?.latest_model?.available) return null;

  const release = snapshot.latest_model;
  const benchmark = release.benchmark as Record<string, unknown> | null | undefined;

  return (
    <Panel title={t("provisioning.invitation.title", "Discover Official Nexus Models")}>
      <p className="small">
        {t(
          "provisioning.invitation.body",
          "Official Nexus models are published through a controlled release process and carry versioned integrity verification and benchmark evidence.",
        )}
      </p>

      <div className="pv-platform-row">
        <span className="badge good">
          <span className="state-label">v{release.version}</span>
        </span>
        {typeof release.size_bytes === "number" ? (
          <span className="muted small">
            {(release.size_bytes / 1_000_000).toFixed(1)} MB
          </span>
        ) : null}
        {release.sha256 ? (
          <span className="small faint inline-mono">{release.sha256.slice(0, 12)}…</span>
        ) : null}
      </div>

      {release.release_notes ? (
        <p className="small muted">{release.release_notes}</p>
      ) : null}

      {benchmark ? (
        <details className="pv-benchmark-details">
          <summary className="small muted">
            {t("provisioning.invitation.benchmark", "Benchmark evidence")}
          </summary>
          <table className="pv-benchmark-table small">
            <tbody>
              {(["win_rate", "profit_factor", "expectancy", "max_drawdown", "sample_size"] as const).map(
                (key) => {
                  const raw = benchmark[key];
                  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
                  const label = key.replace(/_/g, " ");
                  return (
                    <tr key={key}>
                      <td className="muted">{label}</td>
                      <td>
                        <span className="badge neutral">
                          {Number.isInteger(raw) ? String(raw) : raw.toFixed(2)}
                        </span>
                      </td>
                    </tr>
                  );
                },
              )}
            </tbody>
          </table>
        </details>
      ) : (
        <p className="small faint">
          {t(
            "provisioning.invitation.no_evidence",
            "No benchmark evidence was published for this release.",
          )}
        </p>
      )}

      <p className="small muted">
        {t(
          "provisioning.invitation.local_vs_official",
          "Your local model is kept; the official model provides a reproducible, verified release identity.",
        )}
      </p>

      <div className="pv-toolbar">
        <button
          type="button"
          className="btn primary"
          disabled={busy || snapshot.access_allowed !== true}
          onClick={onInstall}
        >
          {t("provisioning.invitation.install", "YES — Install Official Model")}
        </button>
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void decide("declined")}>
          {t("provisioning.invitation.decline", "NO — Keep My Current Model")}
        </button>
        <button type="button" className="btn ghost" disabled={busy} onClick={() => void decide("later")}>
          {t("provisioning.invitation.later", "Later")}
        </button>
      </div>

      {snapshot.access_allowed !== true ? (
        <p className="small faint">
          {t(
            "provisioning.platform.locked.not_starred",
            "New official models are not available for download until your GitHub account has starred the repository.",
          )}
        </p>
      ) : null}
    </Panel>
  );
}
