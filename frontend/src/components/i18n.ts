/**
 * i18n messages — scope: components
 * OWNER: lane C1 shared-components (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "ui.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("ui.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 * - ONE LINE per entry (no trailing comma after `ar:`): the parity gate's
 *   ENTRY regex only accepts whitespace between the ar value and the brace.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "ui.word.unknown": { fa: "نامعلوم", de: "UNBEKANNT", es: "DESCONOCIDO", ar: "غير معروف" },
  "ui.state.loading": { fa: "در حال بارگذاری وضعیت بک‌اند…", de: "Backend-Status wird geladen…", es: "Cargando el estado del backend…", ar: "جارٍ تحميل حالة الخادم…" },
  "ui.confirm.sending": { fa: "در حال ارسال…", de: "wird gesendet…", es: "enviando…", ar: "جارٍ الإرسال…" },
};
