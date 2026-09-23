/**
 * Language store — zustand binding over lib/i18n.
 *
 * Visual preference ONLY (never NSE state). Applies dir=rtl + lang on
 * <html>, mirroring the legacy dashboard's applyDirection().
 *
 * RE-RENDER CONTRACT (i18n wave 2026-09): `t` is REBUILT on every language
 * change, so its function identity changes with the language and EVERY
 * component selecting `t` re-renders on switch. A stable `t` closure (the
 * pre-wave shape) silently pinned old-language strings until some unrelated
 * state change re-rendered the component. Also dispatches the legacy-
 * compatible `nexus:lang-changed` event for non-React consumers (canvas
 * engines redraw on it).
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

/** t bound to one language — identity changes with the language (see header). */
function makeT(lang: Lang): I18nState["t"] {
  return (key, fallback, vars) => translate(lang, key, fallback, vars);
}

export const useI18n = create<I18nState>((set) => {
  const initial = detectLang();
  applyDirection(initial);
  return {
    lang: initial,
    setLang: (lang: Lang) => {
      persistLang(lang);
      applyDirection(lang);
      set({ lang, t: makeT(lang) });
      document.dispatchEvent(new CustomEvent("nexus:lang-changed", { detail: { lang } }));
    },
    t: makeT(initial),
  };
});
