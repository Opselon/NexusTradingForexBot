/**
 * Command palette — port of the main dashboard's UX_Palette (Web/ux_palette.js,
 * CHG-0048). Ctrl/Cmd+K opens; ↑↓ navigate, Enter run, Esc close. Groups:
 * Navigation / Actions / Help. Commands ONLY navigate or refresh already-
 * polled queries (react-query invalidation) — the palette never places,
 * closes or modifies anything directly; dangerous commands stay on their
 * pages behind the typed-confirmation layer.
 *
 * Wave-2 pass (Lane P): fuzzy subsequence filtering (label + keywords),
 * registry-section grouping, session recents, kbd chips, glass modal with a
 * subtle 3D entrance. Command SOURCE is unchanged: legacy pages + the whole
 * FEATURE_REGISTRY + the two fixed actions.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useDialogA11y } from "./useDialogA11y";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/stores/i18nStore";
import { useUiStore } from "@/stores/uiStore";
import { FEATURE_REGISTRY, featureLabelKey } from "@/app/featureRegistry";
import { runtimeConfig } from "@/core/config";
import "./shell.css";

interface Command {
  id: string;
  group: "nav" | "actions" | "help";
  label: string;
  icon: string;
  keywords: string;
  /** Registry section chip for nav entries (presentation only). */
  section?: string;
  run: () => void;
}

/** Legacy static pages (registry covers every other tab). */
const LEGACY_NAV: Array<{ route: string; label: string; icon: string; keywords: string }> = [
  { route: "/", label: "Dashboard", icon: "◈", keywords: "dashboard home signal overview" },
  { route: "/trading", label: "Trading", icon: "⇅", keywords: "trading engine start stop mode live paper shadow" },
  { route: "/positions", label: "Positions", icon: "▤", keywords: "positions close tickets exposure ledger" },
  { route: "/risk", label: "Risk", icon: "⛨", keywords: "risk guardian halt kill switch circuit breaker" },
  { route: "/ml", label: "ML / 70D", icon: "Σ", keywords: "model ml shadow70 integrity features inference" },
  { route: "/intelligence", label: "Intelligence", icon: "≈", keywords: "news intelligence autopsies calendar sentiment" },
  { route: "/audit", label: "Audit", icon: "☰", keywords: "audit events ledger incidents database integrity" },
];

/* ------------------------------ fuzzy filter ------------------------------ */

/** Subsequence score: consecutive-prefix hits weigh more than scattered ones;
 *  a keyword/label substring match short-circuits to a strong score. */
function fuzzyScore(haystack: string, needle: string): number {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  const direct = h.indexOf(n);
  if (direct >= 0) return 1000 - direct;
  let score = 0;
  let runLen = 0;
  let pos = -1;
  for (const ch of n) {
    const at = h.indexOf(ch, pos + 1);
    if (at === -1) return 0; // not a subsequence → excluded
    runLen = at === pos + 1 ? runLen + 1 : 1;
    score += 10 + runLen * 4 - Math.min(at, 20);
    pos = at;
  }
  return score;
}

/* ------------------------------- recents --------------------------------- */

const RECENTS_KEY = "nse.altui.palette.recents";
const MAX_RECENTS = 5;

function readRecents(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, MAX_RECENTS) : [];
  } catch {
    return []; // private mode / disabled storage — recents just won't persist
  }
}
function writeRecents(ids: string[]): void {
  try {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(ids.slice(0, MAX_RECENTS)));
  } catch {
    /* visual pref only — fail-soft */
  }
}

export function CommandPalette({ onOpenHelp }: { onOpenHelp: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [recents, setRecents] = useState<string[]>(() => readRecents());
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  // Trap + focus restore; Esc stays owned by the input (query must survive Esc? no — Esc clears/closes there).
  useDialogA11y(boxRef, () => setOpen(false), { escEnabled: false, initialFocus: false });
  const listRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const t = useI18n((s) => s.t);
  const pushToast = useUiStore((s) => s.pushToast);

  const commands = useMemo<Command[]>(() => {
    const nav = (id: string, path: string, label: string, icon: string, keywords: string, section?: string): Command => ({
      id, group: "nav", label, icon, keywords, section, run: () => navigate(path),
    });
    // Legacy pages (stable ids) + EVERY registered feature route — the
    // registry is the single source, so a new feature is palette-visible
    // without touching this file.
    const legacy = LEGACY_NAV.map((p) =>
      nav(
        `goto_${p.route === "/" ? "home" : p.route.slice(1)}`,
        p.route,
        t(`nav.page.${p.route === "/" ? "dashboard" : p.route.slice(1)}`, p.label),
        p.icon,
        p.keywords,
        t("ux.palette.section.pages", "PAGES"),
      ),
    );
    const features = runtimeConfig.flags.paletteAllRoutes !== false
      ? FEATURE_REGISTRY.map((f) =>
          nav(
            `goto_${f.route.slice(1)}`,
            f.route,
            t(featureLabelKey(f.route), f.label),
            f.icon,
            `${f.route} ${f.section} ${f.label} ${f.legacyTab} ${f.keywords ?? ""}`,
            f.section,
          ),
        )
      : [];
    return [
      ...legacy,
      ...features,
      {
        id: "refresh",
        group: "actions",
        label: t("ux.action.refresh", "Refresh data"),
        icon: "↻",
        keywords: "refresh reload snapshot",
        run: () => {
          void queryClient.invalidateQueries();
          pushToast("info", `${t("ux.action.refresh", "Refresh data")}…`);
        },
      },
      { id: "help", group: "help", label: t("ux.shortcut.help", "Keyboard shortcuts"), icon: "?", keywords: "help keys shortcuts", run: onOpenHelp },
    ];
  }, [navigate, queryClient, pushToast, t, onOpenHelp]);

  const groupLabel = (g: Command["group"]) =>
    g === "nav" ? t("ux.palette.group.nav", "Navigation") : g === "actions" ? t("ux.palette.group.actions", "Actions") : t("ux.palette.group.help", "Help");

  /** scored, group-ordered result list for the current query */
  const filtered = useMemo<Command[]>(() => {
    const q = query.trim();
    if (!q) {
      // Empty query: recents first (session), then registry order.
      const byId = new Map(commands.map((c) => [c.id, c]));
      const rec = recents.map((id) => byId.get(id)).filter((c): c is Command => Boolean(c));
      const rest = commands.filter((c) => !recents.includes(c.id));
      return [...rec, ...rest].slice(0, 40);
    }
    const scored = commands
      .map((c) => ({ c, s: Math.max(fuzzyScore(`${c.label} ${c.keywords}`, q), fuzzyScore(c.group, q) - 50) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s);
    return scored.slice(0, 30).map((x) => x.c);
  }, [query, commands, recents]);

  const recentsTop = !query.trim() && recents.length > 0 && filtered.length > 0 && filtered[0]?.group === "nav" && recents.includes(filtered[0].id);

  useEffect(() => setSelected(0), [query]);

  // Keep the selected row in view when keyboard-wrapping.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${selected}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  // Global hotkey: Ctrl/Cmd+K anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setRecents(readRecents());
      const timer = window.setTimeout(() => inputRef.current?.focus(), 20);
      return () => window.clearTimeout(timer);
    }
  }, [open]);

  if (!open) return null;

  const runAt = (i: number) => {
    const c = filtered[i];
    if (!c) return;
    setOpen(false);
    setQuery("");
    const next = [c.id, ...recents.filter((r) => r !== c.id)].slice(0, MAX_RECENTS);
    setRecents(next);
    writeRecents(next);
    c.run();
  };

  const kbd = (k: string) => <kbd>{k}</kbd>;

  return (
    <div
      className="modal-overlay cp-overlay"
      style={{ alignItems: "flex-start", paddingTop: "12vh" }}
      onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}
    >
      <div ref={boxRef} className="modal cp-modal" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="cp-searchrow">
          <span className="cp-glyph" aria-hidden="true">⌘</span>
          <input
            ref={inputRef}
            className="palette-input cp-input"
            role="combobox"
                        aria-expanded="true"
                        aria-controls="cp-listbox"
                        aria-activedescendant={`cp-opt-${selected}`}
                        aria-label="Search commands"
            placeholder={t("ux.palette.placeholder", "Search commands… (e.g. “signal”, “position”, “diagnostics”)")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setSelected((s) => (s + 1) % Math.max(1, filtered.length)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSelected((s) => (s - 1 + filtered.length) % Math.max(1, filtered.length)); }
              else if (e.key === "Enter") { e.preventDefault(); runAt(selected); }
              else if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
            }}
          />
          <span className="cp-count tiny faint inline-mono">{filtered.length ? `${filtered.length}` : "0"}</span>
        </div>
        <div className="palette-list cp-list" id="cp-listbox" ref={listRef} role="listbox" aria-label="Commands">
          {filtered.length === 0 ? (
            <div className="palette-empty">{t("ux.palette.empty", "No results")}</div>
          ) : (
            filtered.map((c, i) => {
              const prev = filtered[i - 1];
              const showGroup = !prev || prev.group !== c.group;
              const isRecent = !query.trim() && recentsTop && recents.includes(c.id) && (!prev || !recents.includes(prev.id));
              return (
                <div key={c.id}>
                  {(showGroup || isRecent) && (
                    <div className="palette-group cp-group">
                      {isRecent && !showGroup
                        ? t("ux.palette.group.recents", "Recent")
                        : groupLabel(c.group)}
                    </div>
                  )}
                  <div
                    role="option"
                    aria-selected={i === selected}
                    id={`cp-opt-${i}`}
                    data-idx={i}
                    className={`palette-item cp-item ${i === selected ? "selected" : ""}`}
                    onClick={() => runAt(i)}
                    onMouseMove={() => setSelected(i)}
                  >
                    <span className="p-icon">{c.icon}</span>
                    <span className="cp-label">{c.label}</span>
                    {c.section && <span className="cp-section tiny">{c.section}</span>}
                    {i === selected && <span className="cp-run hint">{kbd("↵")}</span>}
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div className="palette-foot cp-foot">
          <span>
            {kbd("↑")} {kbd("↓")} {t("ux.palette.hint.nav", "navigate")} · {kbd("↵")} {t("ux.palette.hint.run", "run")} · {kbd("esc")} {t("ux.palette.hint.close", "close")}
          </span>
          <span className="inline-mono">{kbd("Ctrl")} + {kbd("K")}</span>
        </div>
      </div>
    </div>
  );
}
