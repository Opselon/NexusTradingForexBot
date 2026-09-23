/**
 * Language store — zustand binding over lib/i18n.
 *
 * Visual preference ONLY (never NSE state). Applies dir=rtl + lang on
 * <html>, mirroring the legacy dashboard's applyDirection(). Components read
 * `t()` and re-render on language change via the store subscription.
 */

import { create } from "zustand";
import { detectLang, isRtl, persistLang, translate, type Lang } from "@/lib/i18n";

interface I18nState {
  lang: Lang;
  setLang: (lang: Lang) => void;
  /** t(key, fallback, vars) — same contract as Web/ux_i18n.js. */
  t: (key: string, fallback: string, vars?: Record<string, string | number>) => string;
}

function applyDirection(lang: Lang): void {
  const el = document.documentElement;
  el.setAttribute("lang", lang);
  el.setAttribute("dir", isRtl(lang) ? "rtl" : "ltr");
}

export const useI18n = create<I18nState>((set, get) => {
  const initial = detectLang();
  applyDirection(initial);
  // `t` is bound to the CURRENT language through get() (so an old reference
  // never translates with a stale dictionary), but its identity is refreshed
  // on every setLang: consumers that select only `s.t` re-render exactly when
  // the language changes (a stable `t` would leave them on the old language
  // until some unrelated state happened to re-render them).
  const makeT = (): I18nState["t"] => (key, fallback, vars) => translate(get().lang, key, fallback, vars);
  return {
    lang: initial,
    setLang: (lang: Lang) => {
      persistLang(lang);
      applyDirection(lang);
      set({ lang, t: makeT() });
    },
    t: makeT(),
  };
});
