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
  "ui.eb.this_view": { fa: "این نما", de: "Diese Ansicht", es: "Esta vista", ar: "هذا العرض" },
  "ui.eb.crashed": { fa: "{l} هنگام رسم کرش کرد.", de: "{l} ist beim Rendern abgestürzt.", es: "{l} se ha bloqueado al renderizarse.", ar: "{l} تعطّل أثناء العرض." },
  "ui.eb.intact": { fa: "بقیه کنسول سالم است — هیچ مقداری پنهان یا ساخته نشده است.", de: "Der Rest der Konsole ist intakt — es wurden keine Werte versteckt oder erfunden.", es: "El resto de la consola está intacto: no se ocultó ni se inventó ningún valor.", ar: "بقية الكونسول سليمة — لم يُخفَ أي قيمة ولم يُختلق أي رقم." },
  "ui.eb.retry_render": { fa: "تلاش مجدد رسم", de: "Rendern erneut versuchen", es: "Reintentar el renderizado", ar: "إعادة محاولة العرض" },
  "ui.eb.reload": { fa: "بارگذاری مجدد", de: "Neu laden", es: "Recargar", ar: "إعادة التحميل" },
  "ui.conn.live_feed": { fa: "جریان زنده", de: "LIVE-FEED", es: "FLUJO EN VIVO", ar: "البث المباشر" },
  "ui.conn.connecting": { fa: "در حال اتصال", de: "VERBINDEN", es: "CONECTANDO", ar: "جارٍ الاتصال" },
  "ui.conn.error_word": { fa: "خطا", de: "FEHLER", es: "ERROR", ar: "خطأ" },
  "ui.conn.data_age": { fa: "سن داده {a}", de: "Datenalter {a}", es: "antigüedad de datos {a}", ar: "عمر البيانات {a}" },
  "ui.conn.age": { fa: "سن {a}", de: "Alter {a}", es: "edad {a}", ar: "العمر {a}" },
  "ui.mode.none": { fa: "حالت —", de: "MODUS —", es: "MODO —", ar: "الوضع —" },
  "ui.mode.mismatch": { fa: "(عدم تطابق منبع)", de: "(QUELLE NICHT KONFORM)", es: "(FUENTE NO COINCIDE)", ar: "(تعارض المصدر)" },
  "ui.mode.mismatch_title": { fa: "عدم تطابق حالت-منبع: runtime_mode={m} اما data_source={d} (محافظ BUG-232). به این به‌عنوان وضعیت واقعی کارگزار اعتماد نکنید.", de: "MODUS-QUELLE NICHT KONFORM: runtime_mode={m}, aber data_source={d} (BUG-232-Wächter). Dies nicht als echten Broker-Zustand behandeln.", es: "DESAJUSTE DE FUENTE Y MODO: runtime_mode={m} pero data_source={d} (guardia BUG-232). No confíe en esto como estado real del bróker.", ar: "تعارض الوضع والمصدر: runtime_mode={m} بينما data_source={d} (حارس BUG-232). لا تعتبر هذا حالة الوسيط الحقيقية." },
  "ui.mode.real_orders": { fa: "سفارش‌های واقعی", de: "ECHTE ORDERS", es: "ÓRDENES REALES", ar: "أوامر حقيقية" },
  "ui.attention.summary": { fa: "خلاصه توجه", de: "Aufmerksamkeitsübersicht", es: "Resumen de atención", ar: "ملخص الانتباه" },
};
