/**
 * PlatformVerificationPanel — GitHub star verification for official models.
 *
 * States rendered (product security spec §7): every backend state plus the
 * interaction states around the browser handoff. The component NEVER claims
 * success before the backend confirms: `STARRED_VERIFIED` is only rendered
 * from a platform-attested payload, and the browser-open step is always an
 * explicit, consented user action.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmModal, Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { platformApi } from "../api";
import type { PlatformState, PlatformStatus } from "../model";

const POLL_MS = 2500;

/** Badge tone per state (theme token classes). */
function toneFor(state: PlatformState): "good" | "warn" | "bad" | "neutral" {
  switch (state) {
    case "STARRED_VERIFIED":
      return "good";
    case "NOT_CONNECTED":
    case "AUTHENTICATION_PENDING":
    case "VERIFICATION_PENDING":
      return "warn";
    case "NOT_STARRED":
    case "VERIFICATION_EXPIRED":
    case "VERIFICATION_UNAVAILABLE":
      return "bad";
    default:
      return "neutral";
  }
}

export interface PlatformVerificationPanelProps {
  /** Notifies the page whether official downloads are unlocked. */
  onVerifiedChange?: (verified: boolean) => void;
}

export function PlatformVerificationPanel({ onVerifiedChange }: PlatformVerificationPanelProps) {
  const t = useI18n((s) => s.t);
  const [platform, setPlatform] = useState<PlatformStatus | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const pollRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const verified = platform?.state === "STARRED_VERIFIED";
  const connecting = platform?.state === "AUTHENTICATION_PENDING";

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    onVerifiedChange?.(verified);
  }, [verified, onVerifiedChange]);

  const apply = useCallback((payload: PlatformStatus | undefined) => {
    if (payload && mountedRef.current) setPlatform(payload);
  }, []);

  const load = useCallback(async () => {
    try {
      const out = await platformApi.status();
      if (out.success && out.platform) {
        apply(out.platform);
        setLoadFailed(false);
      } else {
        setLoadFailed(true);
      }
    } catch {
      setLoadFailed(true);
    }
  }, [apply]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while a connect session is pending; pause when the tab is hidden.
  useEffect(() => {
    if (!connecting) return;
    const tick = () => {
      if (document.hidden) return;
      void (async () => {
        try {
          const out = await platformApi.connectPoll();
          if (out.success && out.platform) apply(out.platform);
        } catch {
          /* transient — next tick retries */
        }
      })();
    };
    tick();
    pollRef.current = window.setInterval(tick, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
      pollRef.current = null;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [connecting, apply]);

  const onStart = useCallback(async () => {
    setBusy(true);
    try {
      const out = await platformApi.connectStart();
      if (out.success && out.platform) {
        apply(out.platform);
        setConfirmOpen(true);
      }
    } finally {
      setBusy(false);
    }
  }, [apply]);

  const onOpenBrowser = useCallback(() => {
    const url = platform?.verification_url;
    if (!url) return;
    // User-consented navigation to the platform verification page.
    window.open(url, "_blank", "noopener,noreferrer");
    setConfirmOpen(false);
  }, [platform?.verification_url]);

  const onCopyLink = useCallback(async () => {
    const url = platform?.verification_url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the URL is rendered as text below */
    }
  }, [platform?.verification_url]);

  const onReverify = useCallback(async () => {
    setBusy(true);
    try {
      const out = await platformApi.reverify();
      if (out.success) apply(out.platform);
    } finally {
      setBusy(false);
    }
  }, [apply]);

  const onDisconnect = useCallback(async () => {
    setBusy(true);
    try {
      const out = await platformApi.disconnect();
      if (out.success) apply(out.platform);
    } finally {
      setBusy(false);
    }
  }, [apply]);

  const stateLabel = () => {
    if (!platform) {
      return loadFailed
        ? t("provisioning.platform.state.unavailable", "Status endpoint unreachable")
        : t("provisioning.platform.state.loading", "Checking…");
    }
    switch (platform.state) {
      case "NOT_CONNECTED":
        return t("provisioning.platform.state.not_connected", "Not connected");
      case "AUTHENTICATION_PENDING":
        return t("provisioning.platform.state.auth_pending", "Connecting securely to GitHub…");
      case "VERIFICATION_PENDING":
        return t("provisioning.platform.state.verify_pending", "Verification pending");
      case "STARRED_VERIFIED":
        return t("provisioning.platform.state.verified", "Star verified — official models unlocked");
      case "NOT_STARRED":
        return t("provisioning.platform.state.not_starred", "Repository not starred");
      case "VERIFICATION_EXPIRED":
        return t("provisioning.platform.state.expired", "Verification expired");
      case "VERIFICATION_UNAVAILABLE":
        return t("provisioning.platform.state.unavailable", "Verification platform unreachable");
      default:
        return platform.state;
    }
  };

  const lockedMessage = () => {
    switch (platform?.state) {
      case "NOT_CONNECTED":
        return t(
          "provisioning.platform.locked.not_connected",
          "New official models are locked until you connect GitHub and star the repository.",
        );
      case "AUTHENTICATION_PENDING":
        return t(
          "provisioning.platform.locked.auth_pending",
          "Finish the GitHub authorization in your browser to continue.",
        );
      case "VERIFICATION_PENDING":
      case "NOT_STARRED":
        return t(
          "provisioning.platform.locked.not_starred",
          "New official models are not available for download until your GitHub account has starred the repository.",
        );
      case "VERIFICATION_EXPIRED":
        return t(
          "provisioning.platform.locked.expired",
          "Your verification expired — reconnect GitHub to renew official model access.",
        );
      case "VERIFICATION_UNAVAILABLE":
        return t(
          "provisioning.platform.locked.unavailable",
          "The verification platform is unreachable; official downloads are paused. Installed models keep working.",
        );
      default:
        return null;
    }
  };

  const locked = lockedMessage();

  return (
    <Panel title={t("provisioning.platform.title", "GitHub Verification & Official Access")}>
      <p className="small muted">
        {t(
          "provisioning.platform.intro",
          "Official Nexus models are delivered only to verified supporters. Connect your GitHub account and star",
        )}{" "}
        <a
          className="inline-mono"
          href={platform?.repository ?? "https://github.com/Opselon/NexusTradingForexBot"}
          target="_blank"
          rel="noopener noreferrer"
        >
          {platform?.repository ?? "Opselon/NexusTradingForexBot"}
        </a>
        {" — "}
        {t(
          "provisioning.platform.intro2",
          "verified server-side on every download. Installed models are never removed.",
        )}
      </p>

      <div className="pv-platform-row">
        <span className={`badge ${toneFor(platform?.state ?? "NOT_CONNECTED")}`}>
          <span className="state-label">{stateLabel()}</span>
        </span>
        {platform?.github_login ? (
          <span className="muted small">
            {t("provisioning.platform.connected_as", "Connected as")}{" "}
            <strong>@{platform.github_login}</strong>
          </span>
        ) : null}
      </div>

      {locked ? <div className="pv-callout bad">{locked}</div> : null}

      {platform?.state === "VERIFICATION_UNAVAILABLE" && platform.reason ? (
        <p className="small faint">{platform.reason}</p>
      ) : null}

      <div className="pv-toolbar">
        {verified ? (
          <>
            <span className="badge good">
              {t("provisioning.platform.unlocked", "Official models unlocked")}
            </span>
            <button type="button" className="btn" disabled={busy} onClick={() => void onReverify()}>
              {t("provisioning.platform.verify_again", "Verify Again")}
            </button>
            <button type="button" className="btn ghost" disabled={busy} onClick={() => void onDisconnect()}>
              {t("provisioning.platform.disconnect", "Disconnect")}
            </button>
          </>
        ) : connecting ? (
          <>
            <span className="small muted" aria-live="polite">
              {t("provisioning.platform.connecting", "Connecting securely to GitHub…")}
            </span>
            <button type="button" className="btn" onClick={() => setConfirmOpen(true)}>
              {t("provisioning.platform.open_browser", "Open browser again")}
            </button>
            <button type="button" className="btn ghost" disabled={busy} onClick={() => void onDisconnect()}>
              {t("provisioning.platform.cancel_connect", "Cancel")}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => void onStart()}
            >
              {t("provisioning.platform.cta_continue", "Continue with GitHub")}
            </button>
            {platform?.state === "NOT_STARRED" || platform?.state === "VERIFICATION_PENDING" ? (
              <>
                <a
                  className="btn"
                  href="https://github.com/Opselon/NexusTradingForexBot"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("provisioning.platform.star_cta", "Star NexusTradingForexBot on GitHub")}
                </a>
                <button type="button" className="btn" disabled={busy} onClick={() => void onReverify()}>
                  {t("provisioning.platform.verify_again", "Verify Again")}
                </button>
              </>
            ) : null}
          </>
        )}
      </div>

      <p className="small faint">
        {t(
          "provisioning.platform.trust",
          "Securely connect your GitHub account. We only verify your repository star status — your source code and private repositories are never accessed.",
        )}
      </p>

      {confirmOpen ? (
      <ConfirmModal
        danger={false}
        title={t("provisioning.platform.confirm_title", "Open the verification page?")}
        confirmLabel={t("provisioning.platform.confirm_open", "Open browser")}
        onConfirm={onOpenBrowser}
        onCancel={() => {
          setConfirmOpen(false);
          void onCopyLink();
        }}
      >
        <p className="small">
          {t(
            "provisioning.platform.confirm_body",
            "GitHub's own authorization page will open in your default browser. GitHub handles the sign-in; this app only receives your verification result.",
          )}
        </p>
        {platform?.verification_url ? (
          <p className="small faint inline-mono">{platform.verification_url}</p>
        ) : null}
        {copied ? (
          <p className="small">{t("provisioning.platform.copied", "Link copied to clipboard.")}</p>
        ) : null}
      </ConfirmModal>
      ) : null}
    </Panel>
  );
}
