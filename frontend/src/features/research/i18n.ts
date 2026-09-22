/**
 * i18n messages — scope: features/research
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "research.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("research.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  // --- lane5Kit shared presentation strings ---
  "research.kit.stale_error": { fa: "داده قدیمی — خطای بک‌اند", de: "veraltet — Backend-Fehler", es: "obsoleto — error del backend", ar: "قديم — خطأ في الخادم" },
  "research.kit.updated": { fa: "به‌روزرسانی {t}", de: "aktualisiert {t}", es: "actualizado {t}", ar: "محدَّث {t}" },
  "research.kit.no_timestamp": { fa: "زمان‌بازگشتی برنگشت", de: "kein Zeitstempel zurückgegeben", es: "no se devolvió marca de tiempo", ar: "لم يُعاد ختم زمني" },
  "research.kit.refreshing": { fa: " · در حال تازه‌سازی…", de: " · wird aktualisiert…", es: " · actualizando…", ar: " · جارٍ التحديث…" },
  "research.kit.no_payload": { fa: "بک‌اند هیچ محموله‌ای برای این بلوک برنگرداند.", de: "Das Backend hat kein Payload für diesen Block zurückgegeben.", es: "El backend no devolvió payload para este bloque.", ar: "لم يعِد الخادم أي حمولة لهذه الكتلة." },
  "research.kit.truncated": { fa: "… ({n} نویسه، کوتاه‌شده)", de: "… ({n} Zeichen, gekürzt)", es: "… ({n} caracteres, truncado)", ar: "… ({n} حرف، مقطوع)" },
  "research.kit.request_failed": { fa: "درخواست بک‌اند ناموفق بود", de: "Backend-Anfrage fehlgeschlagen", es: "La solicitud al backend falló", ar: "فشل طلب الخادم" },
  "research.kit.close": { fa: "بستن", de: "schließen", es: "cerrar", ar: "إغلاق" },
  "research.kit.sending": { fa: "…در حال ارسال به بک‌اند", de: "…wird an das Backend gesendet", es: "…enviando al backend", ar: "…جارٍ الإرسال إلى الخادم" },
  "research.kit.no_gates": { fa: "بک‌اند برای این مورد هیچ گیتی ثبت نکرده است.", de: "Das Backend hat für diesen Eintrag keine Gates erfasst.", es: "El backend no registró gates para este elemento.", ar: "لم يسجّل الخادم أي بوابات لهذا العنصر." },
  "research.kit.no_rows": { fa: "بک‌اند هیچ ردیفی برنگرداند.", de: "Das Backend hat keine Zeilen zurückgegeben.", es: "El backend no devolvió filas.", ar: "لم يعِد الخادم أي صفوف." },

  // --- model.commandVerdict verdict texts (backend-echo kept verbatim) ---
  "research.model.backend_error": { fa: "خطای بک‌اند: {code}", de: "Backend-Fehler: {code}", es: "Error del backend: {code}", ar: "خطأ الخادم: {code}" },
  "research.model.refused": { fa: "بک‌اند این فرمان را رد کرد.", de: "Das Backend hat den Befehl abgelehnt.", es: "El backend rechazó el comando.", ar: "رفض الخادم الأمر." },
  "research.model.accepted": { fa: "بک‌اند پذیرفت: {s}", de: "Das Backend hat angenommen: {s}", es: "El backend aceptó: {s}", ar: "قبل الخادم: {s}" },
  "research.model.accepted_generic": { fa: "بک‌اند فرمان را پذیرفت.", de: "Das Backend hat den Befehl angenommen.", es: "El backend aceptó el comando.", ar: "قبل الخادم الأمر." },
  "research.model.no_verdict": { fa: "بک‌اند بدون حکم صریح پاسخ داد.", de: "Das Backend hat ohne ausdrücklichen Bescheid geantwortet.", es: "El backend respondió sin un veredicto explícito.", ar: "أجاب الخادم دون حكم صريح." },
};
