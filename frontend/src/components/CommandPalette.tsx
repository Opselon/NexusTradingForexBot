/**
 * Command palette — port of the main dashboard's UX_Palette (Web/ux_palette.js,
 * CHG-0048). Ctrl/Cmd+K opens; ↑↓ navigate, Enter run, Esc close. Groups:
 * Navigation / Actions / Help. Commands ONLY navigate or refresh already-
 * polled queries (react-query invalidation) — the palette never places,
 * closes or modifies anything directly; dangerous commands stay on their
 * pages behind the typed-confirmation layer.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/stores/i18nStore";
import { useUiStore } from "@/stores/uiStore";

interface Command {
  id: string;
  group: "nav" | "actions" | "help";
  label: string;
  icon: string;
  keywords: string;
  run: () => void;
}

export function CommandPalette({ onOpenHelp }: { onOpenHelp: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const t = useI18n((s) => s.t);
  const pushToast = useUiStore((s) => s.pushToast);

  const commands = useMemo<Command[]>(() => {
    const nav = (id: string, path: string, label: string, icon: string, keywords: string): Command => ({
      id, group: "nav", label, icon, keywords, run: () => navigate(path),
    });
    return [
      nav("goto_home", "/", t("ux.action.goto_home", "Go to Dashboard"), "◈", "dashboard home signal overview"),
      nav("goto_trading", "/trading", t("ux.action.goto_signals", "Trading / engine commands"), "⇅", "trading engine start stop mode live paper shadow"),
      nav("goto_positions", "/positions", t("ux.action.goto_positions", "Positions"), "▤", "positions close tickets exposure ledger"),
      nav("goto_risk", "/risk", t("ux.action.goto_health", "Risk / guardian"), "⛨", "risk guardian halt kill switch circuit breaker"),
      nav("goto_ml", "/ml", t("ux.action.goto_ml", "ML / 70D model"), "Σ", "model ml shadow70 integrity features inference"),
      nav("goto_intel", "/intelligence", t("ux.action.goto_intel", "Intelligence / news"), "≈", "news intelligence autopsies calendar sentiment"),
      nav("goto_audit", "/audit", t("ux.action.goto_diagnostics", "Audit / diagnostics"), "☰", "audit events ledger incidents database integrity"),
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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = !q
      ? commands
      : commands.filter((c) => `${c.label} ${c.keywords} ${c.group}`.toLowerCase().includes(q));
    return list.slice(0, 12);
  }, [query, commands]);

  useEffect(() => setSelected(0), [query]);

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
    if (open) setTimeout(() => inputRef.current?.focus(), 20);
  }, [open]);

  if (!open) return null;

  const runAt = (i: number) => {
    const c = filtered[i];
    if (!c) return;
    setOpen(false);
    setQuery("");
    c.run();
  };

  return (
    <div
      className="modal-overlay"
      style={{ alignItems: "flex-start", paddingTop: "12vh" }}
      onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={t("alt.palette.aria", "Command palette")}>
        <input
          ref={inputRef}
          className="palette-input"
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
        <div className="palette-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="palette-empty">{t("ux.palette.empty", "No results")}</div>
          ) : (
            filtered.map((c, i) => (
              <div key={c.id}>
                {(i === 0 || filtered[i - 1]?.group !== c.group) && <div className="palette-group">{groupLabel(c.group)}</div>}
                <div
                  role="option"
                  aria-selected={i === selected}
                  className={`palette-item ${i === selected ? "selected" : ""}`}
                  onClick={() => runAt(i)}
                  onMouseMove={() => setSelected(i)}
                >
                  <span className="p-icon">{c.icon}</span>
                  <span>{c.label}</span>
                </div>
              </div>
            ))
          )}
        </div>
        <div className="palette-foot">
          <span>{t("ux.palette.hint", "↑↓ navigate · Enter run · Esc close")}</span>
          <span className="inline-mono">Ctrl/Cmd+K</span>
        </div>
      </div>
    </div>
  );
}
