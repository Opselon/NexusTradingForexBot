/**
 * Settings — visual-only preferences for the alternative console (/settings).
 *
 * Backend-authority contract: this page holds ZERO NSE state. It exposes only
 * presentation preferences already owned by the frozen stores —
 *   - useUiStore: density (`nse.altui.dense`), sidebar (`nse.altui.sidebar`),
 *     and a read-only note about the 4 s toast-dedupe window (toastGuard),
 *   - useI18n: language select over LANGUAGES (`nexus.ui.lang`, shared with
 *     the legacy dashboard; toggling updates <html lang>/<html dir> through
 *     the store's applyDirection — RTL for fa/ar),
 * plus a shortcut cheat-sheet matching the REAL bindings in AppShell.tsx
 * (lines 114–139) and CommandPalette.tsx (lines 75–85), the theme token NAMES
 * from styles/theme.css :root (names only — values live in CSS), an About
 * block mirroring the last snapshot identity passed down as the `snapshot`
 * prop (same wiring shape as every other page), and a "clear local UI prefs"
 * action scoped STRICTLY to localStorage keys `nse.altui.*` + `nexus.ui.lang`
 * behind a ConfirmModal (Esc = cancel). The auth token lives in
 * sessionStorage — it is never listed, matched, or removed here.
 */

import { useMemo, useState } from "react";
import { version as UI_VERSION } from "../../../package.json";
import type { EngineSnapshot } from "@/types/domain";
import { useUiStore } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";
import { LANGUAGES, type Lang } from "@/lib/i18n";
import { ConfirmModal, Panel, Segmented } from "@/components/primitives";
import "@/styles/pro-settings.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
}

/* ------------------------------------------------------------------ */
/* localStorage preference keys (visual prefs ONLY — never NSE state). */
/* The readPref/writePref helpers in uiStore.ts are module-private,    */
/* and uiStore is frozen for this lane, so this page keeps its OWN     */
/* small fail-soft readers for the clear-prefs action, following the   */
/* same pattern. No new preference keys are introduced by this page.   */
/* ------------------------------------------------------------------ */

const PREF_PREFIX = "nse.altui.";
const LANG_KEY = "nexus.ui.lang";

function listUiPrefKeys(): string[] {
  const out: string[] = [];
  try {
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && (k.startsWith(PREF_PREFIX) || k === LANG_KEY)) out.push(k);
    }
  } catch {
    /* private mode / disabled storage — nothing to list */
  }
  return out.sort();
}

function removeUiPrefKeys(keys: string[]): string[] {
  const removed: string[] = [];
  try {
    for (const k of keys) {
      window.localStorage.removeItem(k);
      removed.push(k);
    }
  } catch {
    /* storage unavailable — prefs just were never persisted anyway */
  }
  return removed;
}

/* ------------------------------------------------------------------ */
/* Verified keyboard bindings (read of AppShell.tsx + CommandPalette). */
/*                                                                    */
/* Truth notes (checked against source, not against the docs):         */
/*  - Alt+<digit> covers EVERY sidebar NAV route by position — the     */
/*    handler uses NAV_ROUTES.length (today 7). Adding /settings to    */
/*    NAV_SECTIONS makes Alt+8 light up with no handler change.        */
/*  - Plain `r` refreshes, but ONLY when an engine-snapshot is already */
/*    cached and focus is not in an input/textarea/select/content-     */
/*    editable — the sidebar chip and help modal omit that nuance.     */
/*  - There is NO Alt+1..7 binding in CommandPalette; it binds only    */
/*    Ctrl/Cmd+K globally and ↑↓/Enter/Esc while its input is open.    */
/*  - Esc closes dialogs (ConfirmModal, palette) and only cancels.     */
/* ------------------------------------------------------------------ */

/* Token NAMES exactly as declared in styles/theme.css :root (verified
 * 2026-09-13, 27 tokens). Values are deliberately NOT restated here — the
 * stylesheet is the single source; `body.dense` overrides --row-pad,
 * --card-pad and --panel-gap. */
const THEME_TOKENS: string[] = [
  "--bg", "--bg-panel", "--bg-panel-2", "--bg-inset", "--elevate-hi", "--elevate-lo",
  "--border", "--border-strong",
  "--text", "--text-dim", "--text-faint",
  "--accent", "--accent-strong", "--green", "--green-dim", "--red", "--red-dim",
  "--amber", "--amber-dim", "--violet", "--live-red-bg", "--paper-blue-bg",
  "--mono", "--sans",
  "--row-pad", "--card-pad", "--panel-gap",
];

export default function SettingsPage({ snapshot }: Props) {
  const dense = useUiStore((s) => s.dense);
  const toggleDense = useUiStore((s) => s.toggleDense);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const pushToast = useUiStore((s) => s.pushToast);
  const lang = useI18n((s) => s.lang);
  const setLang = useI18n((s) => s.setLang);
  const t = useI18n((s) => s.t);

  /* Verified keyboard bindings (read of AppShell.tsx + CommandPalette).
     Built during render so every label goes through t() and follows the
     language switch; keys stay string literals for the parity gate. */
  const shortcuts: Array<{ keys: string[]; action: string; note: string }> = [
    {
      keys: ["Ctrl", "K"],
      action: t("settings.shortcut.palette.action", "Open / close the command palette"),
      note: t("settings.shortcut.palette.note", "Cmd+K on macOS — bound in CommandPalette.tsx; palette commands only navigate or re-run existing queries."),
    },
    {
      keys: ["Alt", "1–7"],
      action: t("settings.shortcut.alt.action", "Jump to sidebar page by position"),
      note: t("settings.shortcut.alt.note", "Dynamic: one slot per NAV item — becomes 1–8 once the /settings NAV entry lands. Disabled while typing."),
    },
    {
      keys: ["Alt", "B"],
      action: t("settings.shortcut.sidebar.action", "Collapse / expand the sidebar"),
      note: t("settings.shortcut.sidebar.note", "Same action as the sidebar-foot toggle and the Sidebar switch below."),
    },
    {
      keys: ["R"],
      action: t("settings.shortcut.refresh.action", "Refresh the engine snapshot"),
      note: t("settings.shortcut.refresh.note", "Plain R (no modifier), ignored while typing, and only acts after a first snapshot has loaded."),
    },
    {
      keys: ["↑", "↓", "Enter"],
      action: t("settings.shortcut.move.action", "Move / run inside the palette"),
      note: t("settings.shortcut.move.note", "Only while the palette input is focused."),
    },
    {
      keys: ["Esc"],
      action: t("settings.shortcut.esc.action", "Close palette / dialog"),
      note: t("settings.shortcut.esc.note", "Cancel only — Esc never confirms an action."),
    },
  ];

  const [clearOpen, setClearOpen] = useState(false);
  const [keysAtOpen, setKeysAtOpen] = useState<string[]>([]);
  const [clearedCount, setClearedCount] = useState<number | null>(null);

  const openClearDialog = () => {
    setKeysAtOpen(listUiPrefKeys());
    setClearedCount(null);
    setClearOpen(true);
  };

  const confirmClear = () => {
    // Return the in-memory prefs to defaults through the store's public
    // toggles (the only non-frozen path), then wipe what they re-wrote plus
    // every other nse.altui.* / nexus.ui.lang key.
    if (dense) toggleDense();
    if (collapsed) toggleSidebar();
    const removed = removeUiPrefKeys(listUiPrefKeys());
    setClearedCount(removed.length);
    setClearOpen(false);
    pushToast("ok", t("settings.local.cleared_toast", "Local UI prefs cleared ({n} key(s))", { n: removed.length }));
  };

  const versioningEntries = useMemo(
    () => Object.entries(snapshot?.versioning ?? {}),
    [snapshot?.versioning],
  );

  return (
    <div className="set-wrap">
      <div className="page-head">
        <h1>{t("settings.head.title", "Settings")}</h1>
        <span className="crumb">{t("settings.head.crumb", "ALT CONSOLE")}</span>
        <span className="desc">{t("ux.sidebar.system", "System")} — {t("settings.head.desc_suffix", "visual preferences only")}</span>
      </div>

      <div className="set-note">
        {t("settings.note.backend_authority", "⚠ Nothing on this page reads or changes NSE state. Engine mode, risk gates, positions and snapshots stay backend-authoritative; the About block merely mirrors the snapshot the shell already polled.")}
      </div>

      <div className="set-grid">
        <Panel title={t("settings.panel.appearance", "Appearance")} accent>
          <div className="set-row">
            <div>
              <div className="lab">{t("ux.settings.density", "Row density")}</div>
              <div className="sub">
                {t("settings.density.sub", "Shared with the sidebar DENSITY switch —")} <code className="inline-mono">nse.altui.dense</code>{" "}
                <span className="badge">{dense ? t("settings.density.badge_dense", "1 (dense)") : t("settings.density.badge_comfortable", "0 (comfortable)")}</span>
              </div>
            </div>
            <Segmented
              options={[
                { id: "comfortable", label: t("settings.density.opt_comfortable", "Comfortable") },
                { id: "dense", label: t("settings.density.opt_dense", "Dense") },
              ]}
              value={dense ? "dense" : "comfortable"}
              onChange={(v) => {
                const wantDense = v === "dense";
                if (wantDense !== dense) toggleDense();
              }}
            />
          </div>
          <div className="set-row">
            <div>
              <div className="lab">{t("ux.settings.sidebar", "Sidebar")}</div>
              <div className="sub">
                {t("settings.sidebar.sub", "Same action as Alt+B and the « button —")} <code className="inline-mono">nse.altui.sidebar</code>{" "}
                <span className="badge">{collapsed ? t("settings.sidebar.badge_collapsed", "1 (collapsed)") : t("settings.sidebar.badge_expanded", "0 (expanded)")}</span>
              </div>
            </div>
            <Segmented
              options={[
                { id: "expanded", label: t("settings.sidebar.opt_expanded", "Expanded") },
                { id: "collapsed", label: t("settings.sidebar.opt_collapsed", "Collapsed") },
              ]}
              value={collapsed ? "collapsed" : "expanded"}
              onChange={(v) => {
                const wantCollapsed = v === "collapsed";
                if (wantCollapsed !== collapsed) toggleSidebar();
              }}
            />
          </div>
          <div className="set-row">
            <div>
              <div className="lab">{t("settings.toast.lab", "Toast dedupe")}</div>
              <div className="sub">
                {t("settings.toast.sub", "Identical toasts inside a 4 second window are merged once (uiStore toastGuard — parity with legacy NX.toast). Display behaviour, always on, not a backend setting.")}
              </div>
            </div>
            <span className="badge neutral">{t("settings.toast.badge", "INFO")}</span>
          </div>
        </Panel>

        <Panel title={t("ux.lang.label", "Language")} accent>
          <div className="set-row">
            <div>
              <div className="lab">{t("ux.lang.label", "Language")}</div>
              <div className="sub">
                {t("settings.lang.sub_a", "One surface with the sidebar LangRow; stored as")} <code className="inline-mono">nexus.ui.lang</code>{" "}
                {t("settings.lang.sub_b", "(shared with the legacy dashboard). Selecting RTL (فارسی / العربية) flips")}{" "}
                <code className="inline-mono">&lt;html dir&gt;</code> {t("settings.lang.sub_c", "through the store immediately.")}
              </div>
            </div>
            <select
              className="select"
              value={lang}
              onChange={(e) => setLang(e.target.value as Lang)}
              aria-label={t("ux.lang.label", "Language")}
            >
              {LANGUAGES.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <div className="set-row">
            <div>
              <div className="lab">{t("settings.direction.lab", "Direction")}</div>
              <div className="sub">
                <code className="inline-mono">&lt;html lang&gt;</code> = {lang} ·{" "}
                <code className="inline-mono">&lt;html dir&gt;</code> = {["fa", "ar"].includes(lang) ? "rtl" : "ltr"}{" "}
                {t("settings.direction.note", "(applied by i18nStore, mirrored from the legacy applyDirection)")}
              </div>
            </div>
          </div>
        </Panel>

        <Panel title={t("settings.panel.shortcuts", "Keyboard shortcuts")}>
          <div className="set-kbd-list">
            {shortcuts.map((s) => (
              <div className="set-kbd-row" key={s.action}>
                <span className="set-kbd-keys">
                  {s.keys.map((k) => (
                    <kbd key={k}>{k}</kbd>
                  ))}
                </span>
                <span className="set-kbd-body">
                  <span className="lab">{s.action}</span>
                  <span className="sub">{s.note}</span>
                </span>
              </div>
            ))}
          </div>
          <div className="sub tiny" style={{ marginTop: 8 }}>
            {t("settings.shortcuts.footnote", "Verified against AppShell.tsx (Alt+digit / Alt+B / R) and CommandPalette.tsx (Ctrl/Cmd+K, ↑↓/Enter/Esc) — not against the sidebar chip, which shortens the truth to")}{" "}
            <span className="inline-mono">alt 1–7 · ctrl K</span>.
          </div>
        </Panel>

        <Panel title={t("settings.panel.theme", "Theme tokens")} right={<span className="timestamp-note">{t("settings.theme.vars", "{n} vars · :root", { n: THEME_TOKENS.length })}</span>}>
          <div className="sub" style={{ marginBottom: 8 }}>
            {t("settings.theme.sub_a", "Names as declared in")} <code className="inline-mono">src/styles/theme.css</code> :root — {t("settings.theme.sub_b", "the stylesheet owns the values.")}{" "}
            <code className="inline-mono">body.dense</code> {t("settings.theme.sub_c", "overrides --row-pad, --card-pad, --panel-gap; color stays semantic (backend state), never user-picked.")}
          </div>
          <div className="set-tokens">
            {THEME_TOKENS.map((tok) => (
              <span className="set-token" key={tok}>{tok}</span>
            ))}
          </div>
        </Panel>

        <Panel title={t("settings.panel.about", "About")} accent>
          <dl className="kv">
            <dt>{t("settings.about.console_build", "console build")}</dt>
            <dd>v{UI_VERSION} (frontend/package.json)</dd>
            <dt>{t("settings.about.state_version", "snapshot state_version")}</dt>
            <dd>{snapshot ? String(snapshot.state_version) : t("settings.about.no_snapshot", "— (no snapshot yet)")}</dd>
            <dt>snapshot_timestamp</dt>
            <dd>{snapshot?.snapshot_timestamp ?? "—"}</dd>
            <dt>generated_at</dt>
            <dd>{snapshot?.generated_at ?? "—"}</dd>
            <dt>{t("settings.about.versioning_block", "backend versioning block")}</dt>
            <dd>
              {versioningEntries.length > 0
                ? versioningEntries.map(([k, v]) => `${k}=${String(v)}`).join(" · ")
                : t("settings.about.no_versioning", "— (not provided by /api/status)")}
            </dd>
            <dt>{t("settings.about.feed_label", "realtime feed")}</dt>
            <dd>{t("settings.about.feed_value", "SSE /api/ticks/stream (state_version guards out-of-order frames)")}</dd>
          </dl>
          <div className="sub tiny" style={{ marginTop: 8 }}>
            {t("settings.about.readonly", "Read-only mirror of the snapshot the shell already polled — this page issues no fetches and invalidates no queries.")}
          </div>
        </Panel>

        <Panel title={t("settings.panel.local", "Local data")}>
          <div className="set-row">
            <div>
              <div className="lab">{t("settings.local.stored_lab", "Stored UI preferences")}</div>
              <div className="sub">
                <code className="inline-mono">nse.altui.dense</code> ·{" "}
                <code className="inline-mono">nse.altui.sidebar</code> ·{" "}
                <code className="inline-mono">nexus.ui.lang</code> — {t("settings.local.sub", "visual values only; the WEB-AUTH token is sessionStorage and out of scope.")}
              </div>
            </div>
            <button className="btn danger" onClick={openClearDialog}>
              {t("settings.local.clear_btn", "Clear local UI prefs…")}
            </button>
          </div>
          {clearedCount !== null && (
            <div className="set-cleared" role="status">
              {t("settings.local.cleared", "✓ Removed {n} localStorage key(s). Density and sidebar are back to defaults for this session; the language selection stays on screen until reload, then falls back to browser detection.", { n: clearedCount })}
            </div>
          )}
        </Panel>
      </div>

      {clearOpen && (
        <ConfirmModal
          title={t("settings.clear.title", "Clear local UI preferences")}
          danger
          confirmLabel={t("settings.clear.confirm", "Clear prefs")}
          onCancel={() => setClearOpen(false)}
          onConfirm={confirmClear}
        >
          <div className="confirm-box">
            <div className="note">
              {t("settings.clear.note_a", "Removes ONLY these localStorage keys in this browser: every")} <code className="inline-mono">nse.altui.*</code>{" "}
              {t("settings.clear.note_b", "pref and")} <code className="inline-mono">nexus.ui.lang</code>. {t("settings.clear.note_c", "Backend state, engine settings and the session auth token are untouched. This console keeps working — the values just stop persisting.")}
            </div>
            <div className="row">
              {keysAtOpen.length > 0 ? (
                keysAtOpen.map((k) => (
                  <span className="set-token" key={k} style={{ textTransform: "none" }}>
                    {k}
                  </span>
                ))
              ) : (
                <span className="muted">{t("settings.clear.no_keys", "No nse.altui.* / nexus.ui.lang keys are currently stored.")}</span>
              )}
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
