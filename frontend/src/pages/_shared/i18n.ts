/**
 * i18n messages — scope: pages/_shared
 * OWNER: orchestrator (pages/_shared widgets) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "_shared.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("_shared.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "_shared.hero.endpoints_aria": { fa: "endpointهای بک‌اند که این صفحه می‌خواند", de: "von dieser Seite gelesene Backend-Endpunkte", es: "endpoints del backend que lee esta página", ar: "نقاط نهاية الخلفية التي تقرأها هذه الصفحة" },
  "_shared.hero.endpoint_title": { fa: "منبع — endpoint بک‌اند {ep}: همه اعداد این صفحه از آن خوانده می‌شوند، هرگز اینجا استنباط نمی‌شوند", de: "Provenienz — Backend-Endpunkt {ep}: jede Zahl dieser Seite wird daraus gelesen, hier nie abgeleitet", es: "Procedencia: endpoint del backend {ep}; todas las cifras de esta página se leen de él, nunca se infieren aquí", ar: "المصدر: نقطة نهاية الخلفية {ep} — تُقرأ كل أرقام هذه الصفحة منها، ولا تُستنتج هنا أبدًا" },
};
