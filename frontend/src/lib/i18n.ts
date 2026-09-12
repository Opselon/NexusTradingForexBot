/**
 * i18n — port of the main dashboard's UX_I18N layer (Web/ux_i18n.js, CHG-0048).
 *
 * Same dictionaries (EN identity + FA/DE/ES/AR), same key contract, same
 * storage rule (localStorage = UI preference ONLY, never NSE state), same RTL
 * handling (fa/ar set dir=rtl). t(key, fallback, vars) returns the fallback —
 * which is the English source string — when no dictionary entry exists, so a
 * missing translation can never blank the UI.
 */

export type Lang = "en" | "fa" | "de" | "es" | "ar";

const LANG_KEY = "nexus.ui.lang"; // shared with the legacy dashboard: one preference
const RTL: Partial<Record<Lang, boolean>> = { fa: true, ar: true };

type Dict = Record<string, string>;

const DICTS: Record<Lang, Dict | null> = {
  en: null, // English = source strings (identity)
  fa: {
    "ux.conn.title": "ارتباط قطع شد",
    "ux.conn.detail": "به‌روزرسانی زنده متوقف شد. داده‌های روی صفحه ممکن است قدیمی باشند.",
    "ux.conn.stale_title": "داده‌ها ممکن است قدیمی باشند",
    "ux.conn.stale_detail": "مدتی است به‌روزرسانی زنده‌ای نرسیده. مقادیر نمایش‌داده‌شده آخرین مقادیر معتبر هستند.",
    "ux.conn.last": "آخرین به‌روزرسانی: {t} ({s} ثانیه پیش)",
    "ux.conn.never": "هنوز هیچ داده زنده‌ای دریافت نشده است.",
    "ux.conn.retry": "تلاش دوباره",
    "ux.confirm.title": "تأیید عملیات",
    "ux.confirm.ok": "تأیید",
    "ux.confirm.cancel": "انصراف",
    "ux.confirm.type": "برای فعال‌سازی دکمه تأیید عبارت {w} را تایپ کنید",
    "ux.mode.title": "تغییر حالت اجرا: {from} ← {to}؟",
    "ux.mode.body": "این تغییر نحوه اجرای معاملات موتور را عوض می‌کند.",
    "ux.mode.impact_label": "چه چیزی تغییر می‌کند",
    "ux.mode.live_warning": "پول واقعی در معرض ریسک است. این کار روی حساب زنده کارگزار شما اثر می‌گذارد.",
    "ux.mode.confirm_live": "فعال‌سازی اجرای زنده (LIVE)",
    "ux.stale": "قدیمی {s}ث",
    "ux.palette.placeholder": "جستجوی دستور… (مثلاً «سیگنال»، «پوزیشن»، «عیب‌یابی»)",
    "ux.palette.empty": "نتیجه‌ای یافت نشد",
    "ux.palette.hint": "↑↓ حرکت · Enter اجرا · Esc بستن",
    "ux.palette.group.nav": "ناوبری",
    "ux.palette.group.actions": "عملیات",
    "ux.palette.group.help": "راهنما",
    "ux.action.refresh": "به‌روزرسانی داده‌ها",
    "ux.action.run_health": "اجرای بررسی سلامت",
    "ux.action.goto_home": "رفتن به صفحه اصلی",
    "ux.action.goto_signals": "مشاهده سیگنال فعلی",
    "ux.action.goto_positions": "مشاهده پوزیشن‌ها",
    "ux.action.goto_health": "سلامت سیستم",
    "ux.action.goto_diagnostics": "عیب‌یابی و دیباگ",
    "ux.action.goto_settings": "تنظیمات",
    "ux.action.goto_replay": "بازپخش (Replay)",
    "ux.shortcut.help": "راهنمای کلیدهای میان‌بر",
    "ux.attention.critical": "نیازمند توجه فوری",
    "ux.attention.warning": "هشدارها",
    "ux.attention.allgood": "همه چیز درست است. نیازی به اقدام نیست.",
    "ux.attention.runtime_blocked": "رانتایم {m} است. تا بازیابی، معامله ممکن نیست.",
    "ux.attention.model_unavailable": "مدل در دسترس نیست — تصمیمی تولید نمی‌شود.",
    "ux.attention.stale": "داده قدیمی است — مقادیر نمایشی آخرین مقادیر شناخته‌شده‌اند.",
    "ux.attention.subsystem": "{s}: {v}",
    "ux.signal.no_trade": "بدون معامله",
    "ux.signal.buy": "خرید",
    "ux.signal.sell": "فروش",
    "ux.signal.wait": "انتظار",
    "ux.signal.confidence": "اطمینان",
    "ux.signal.not_available": "سیگنال در دسترس نیست",
    "ux.reason.BLOCKED_BY_GUARDIAN_UNSAFE_REGIME": "بدون معامله — رژیم بازار در حال حاضر برای ورود ناایمن ارزیابی شده است (Guardian).",
    "ux.reason.CONFIDENCE_GATE": "بدون معامله — سطح اطمینان مدل به آستانه لازم نرسید.",
    "ux.reason.NO_CANDIDATE": "در این لحظه فرصت معاملاتی مناسبی شناسایی نشد.",
    "ux.data.fresh": "تازه",
    "ux.data.stale": "قدیمی",
    "ux.sidebar.operate": "عملیات روزانه",
    "ux.sidebar.analyze": "تحلیل و پژوهش",
    "ux.sidebar.system": "سیستم",
    "ux.lang.label": "زبان",
  },
  de: {
    "ux.conn.title": "VERBINDUNG VERLOREN",
    "ux.conn.detail": "Live-Aktualisierungen gestoppt. Angezeigte Daten können veraltet sein.",
    "ux.conn.stale_title": "DATEN KÖNNEN VERALTET SEIN",
    "ux.conn.stale_detail": "Seit einer Weile keine Live-Updates. Angezeigte Werte sind die letzten bekannten.",
    "ux.conn.last": "Letzte Aktualisierung: {t} (vor {s}s)",
    "ux.conn.never": "Noch keine Live-Daten empfangen.",
    "ux.conn.retry": "Erneut versuchen",
    "ux.confirm.title": "Aktion bestätigen",
    "ux.confirm.ok": "Bestätigen",
    "ux.confirm.cancel": "Abbrechen",
    "ux.confirm.type": "Tippen Sie {w}, um die Bestätigung zu aktivieren",
    "ux.mode.title": "Ausführungsmodus wechseln: {from} → {to}?",
    "ux.mode.body": "Dies ändert, wie die Engine Aufträge ausführt.",
    "ux.mode.impact_label": "Was sich ändert",
    "ux.mode.live_warning": "Echtes Kapital ist gefährdet. Dies betrifft Ihr Live-Broker-Konto.",
    "ux.mode.confirm_live": "LIVE-Ausführung scharf schalten",
    "ux.stale": "VERALTET {s}s",
    "ux.palette.placeholder": "Befehl suchen… (z. B. „Signal\", „Position\", „Diagnose\")",
    "ux.palette.empty": "Keine Ergebnisse",
    "ux.palette.hint": "↑↓ Bewegen · Enter Ausführen · Esc Schließen",
    "ux.palette.group.nav": "Navigation",
    "ux.palette.group.actions": "Aktionen",
    "ux.palette.group.help": "Hilfe",
    "ux.action.refresh": "Daten aktualisieren",
    "ux.action.run_health": "Gesundheitscheck ausführen",
    "ux.action.goto_home": "Zur Startseite",
    "ux.action.goto_signals": "Aktuelles Signal anzeigen",
    "ux.action.goto_positions": "Positionen anzeigen",
    "ux.action.goto_health": "Systemzustand",
    "ux.action.goto_diagnostics": "Diagnose & Debug",
    "ux.action.goto_settings": "Einstellungen",
    "ux.action.goto_replay": "Replay öffnen",
    "ux.shortcut.help": "Tastenkürzel-Hilfe",
    "ux.attention.critical": "Sofortige Aufmerksamkeit erforderlich",
    "ux.attention.warning": "Warnungen",
    "ux.attention.allgood": "Alles in Ordnung. Keine Maßnahmen erforderlich.",
    "ux.attention.runtime_blocked": "Runtime ist {m}. Trading ist bis zur Erholung nicht möglich.",
    "ux.attention.model_unavailable": "Modell nicht verfügbar — keine Entscheidungen möglich.",
    "ux.attention.stale": "Daten sind veraltet — angezeigte Werte sind die letzten bekannten.",
    "ux.attention.subsystem": "{s}: {v}",
    "ux.signal.no_trade": "KEIN TRADE",
    "ux.signal.buy": "KAUFEN",
    "ux.signal.sell": "VERKAUFEN",
    "ux.signal.wait": "WARTEN",
    "ux.signal.confidence": "Konfidenz",
    "ux.signal.not_available": "Signal nicht verfügbar",
    "ux.data.fresh": "AKTUELL",
    "ux.data.stale": "VERALTET",
    "ux.sidebar.operate": "Täglicher Betrieb",
    "ux.sidebar.analyze": "Analyse & Forschung",
    "ux.sidebar.system": "System",
    "ux.lang.label": "Sprache",
  },
  es: {
    "ux.conn.title": "CONEXIÓN PERDIDA",
    "ux.conn.detail": "Las actualizaciones en vivo se detuvieron. Los datos en pantalla pueden estar desactualizados.",
    "ux.conn.stale_title": "LOS DATOS PUEDEN ESTAR DESACTUALIZADOS",
    "ux.conn.stale_detail": "No hay actualizaciones en vivo desde hace un rato. Los valores mostrados son los últimos conocidos.",
    "ux.conn.last": "Última actualización: {t} (hace {s}s)",
    "ux.conn.never": "Aún no se han recibido datos en vivo.",
    "ux.conn.retry": "Reintentar ahora",
    "ux.confirm.title": "Confirmar acción",
    "ux.confirm.ok": "Confirmar",
    "ux.confirm.cancel": "Cancelar",
    "ux.confirm.type": "Escriba {w} para habilitar la confirmación",
    "ux.mode.title": "¿Cambiar modo de ejecución: {from} → {to}?",
    "ux.mode.body": "Esto cambia cómo la plataforma ejecuta órdenes.",
    "ux.mode.impact_label": "Qué cambia",
    "ux.mode.live_warning": "Hay dinero real en riesgo. Esto afecta su cuenta real del bróker.",
    "ux.mode.confirm_live": "Activar ejecución EN VIVO",
    "ux.stale": "ANTIGUO {s}s",
    "ux.palette.placeholder": "Buscar comando… (p. ej. «señal», «posición», «diagnóstico»)",
    "ux.palette.empty": "Sin resultados",
    "ux.palette.hint": "↑↓ Mover · Enter Ejecutar · Esc Cerrar",
    "ux.palette.group.nav": "Navegación",
    "ux.palette.group.actions": "Acciones",
    "ux.palette.group.help": "Ayuda",
    "ux.action.refresh": "Actualizar datos",
    "ux.action.run_health": "Ejecutar chequeo de salud",
    "ux.action.goto_home": "Ir al inicio",
    "ux.action.goto_signals": "Ver señal actual",
    "ux.action.goto_positions": "Ver posiciones",
    "ux.action.goto_health": "Salud del sistema",
    "ux.action.goto_diagnostics": "Diagnóstico y depuración",
    "ux.action.goto_settings": "Ajustes",
    "ux.action.goto_replay": "Abrir Replay",
    "ux.shortcut.help": "Atajos de teclado",
    "ux.attention.critical": "Requiere atención inmediata",
    "ux.attention.warning": "Avisos",
    "ux.attention.allgood": "Todo en orden. No se requiere ninguna acción.",
    "ux.attention.runtime_blocked": "El runtime está {m}. No se puede operar hasta que se recupere.",
    "ux.attention.model_unavailable": "Modelo no disponible — no se producen decisiones.",
    "ux.attention.stale": "Datos desactualizados — los valores mostrados son los últimos conocidos.",
    "ux.attention.subsystem": "{s}: {v}",
    "ux.signal.no_trade": "SIN OPERACIÓN",
    "ux.signal.buy": "COMPRA",
    "ux.signal.sell": "VENTA",
    "ux.signal.wait": "ESPERA",
    "ux.signal.confidence": "Confianza",
    "ux.signal.not_available": "Señal no disponible",
    "ux.data.fresh": "FRESCO",
    "ux.data.stale": "ANTIGUO",
    "ux.sidebar.operate": "Operación diaria",
    "ux.sidebar.analyze": "Análisis e investigación",
    "ux.sidebar.system": "Sistema",
    "ux.lang.label": "Idioma",
  },
  ar: {
    "ux.conn.title": "انقطاع الاتصال",
    "ux.conn.detail": "توقفت التحديثات المباشرة. قد تكون البيانات المعروضة قديمة.",
    "ux.conn.stale_title": "قد تكون البيانات قديمة",
    "ux.conn.stale_detail": "لم تصل تحديثات مباشرة منذ فترة. القيم المعروضة هي آخر القيم المعروفة.",
    "ux.conn.last": "آخر تحديث: {t} (قبل {s} ثانية)",
    "ux.conn.never": "لم يتم استلام أي بيانات مباشرة بعد.",
    "ux.conn.retry": "إعادة المحاولة الآن",
    "ux.confirm.title": "تأكيد العملية",
    "ux.confirm.ok": "تأكيد",
    "ux.confirm.cancel": "إلغاء",
    "ux.confirm.type": "اكتب {w} لتمكين زر التأكيد",
    "ux.mode.title": "تغيير وضع التنفيذ: {from} ← {to}؟",
    "ux.mode.body": "سيؤدي هذا إلى تغيير طريقة تنفيذ الأوامر.",
    "ux.mode.impact_label": "ما الذي يتغير",
    "ux.mode.live_warning": "أموال حقيقية معرضة للخطر. سيؤثر ذلك على حسابك الحقيقي لدى الوسيط.",
    "ux.mode.confirm_live": "تفعيل التنفيذ المباشر",
    "ux.stale": "قديم {s}ث",
    "ux.palette.placeholder": "ابحث عن أمر… (مثل «إشارة»، «مركز»، «تشخيص»)",
    "ux.palette.empty": "لا توجد نتائج",
    "ux.palette.hint": "↑↓ تنقل · Enter تنفيذ · Esc إغلاق",
    "ux.palette.group.nav": "التنقل",
    "ux.palette.group.actions": "إجراءات",
    "ux.palette.group.help": "مساعدة",
    "ux.action.refresh": "تحديث البيانات",
    "ux.action.run_health": "تشغيل فحص الصحة",
    "ux.action.goto_home": "الانتقال إلى الصفحة الرئيسية",
    "ux.action.goto_signals": "عرض الإشارة الحالية",
    "ux.action.goto_positions": "عرض المراكز",
    "ux.action.goto_health": "صحة النظام",
    "ux.action.goto_diagnostics": "التشخيص وتتبع الأخطاء",
    "ux.action.goto_settings": "الإعدادات",
    "ux.action.goto_replay": "فتح إعادة التشغيل",
    "ux.shortcut.help": "دليل اختصارات لوحة المفاتيح",
    "ux.attention.critical": "يتطلب انتباهاً فورياً",
    "ux.attention.warning": "تحذيرات",
    "ux.attention.allgood": "كل شيء على ما يرام. لا حاجة لأي إجراء.",
    "ux.attention.runtime_blocked": "وقت التشغيل {m}. لا يمكن التداول حتى التعافي.",
    "ux.attention.model_unavailable": "النموذج غير متوفر — لا يمكن إنتاج قرارات.",
    "ux.attention.stale": "البيانات قديمة — القيم المعروضة هي آخر القيم المعروفة.",
    "ux.attention.subsystem": "{s}: {v}",
    "ux.signal.no_trade": "بدون صفقة",
    "ux.signal.buy": "شراء",
    "ux.signal.sell": "بيع",
    "ux.signal.wait": "انتظار",
    "ux.signal.confidence": "الثقة",
    "ux.signal.not_available": "الإشارة غير متاحة",
    "ux.data.fresh": "حديث",
    "ux.data.stale": "قديم",
    "ux.sidebar.operate": "التشغيل اليومي",
    "ux.sidebar.analyze": "التحليل والبحث",
    "ux.sidebar.system": "النظام",
    "ux.lang.label": "اللغة",
  },
};

export const LANGUAGES: Array<{ id: Lang; label: string }> = [
  { id: "en", label: "English" },
  { id: "fa", label: "فارسی" },
  { id: "de", label: "Deutsch" },
  { id: "es", label: "Español" },
  { id: "ar", label: "العربية" },
];

export function isRtl(lang: Lang): boolean {
  return RTL[lang] === true;
}

export function detectLang(): Lang {
  try {
    const saved = window.localStorage.getItem(LANG_KEY) as Lang | null;
    if (saved && saved in DICTS) return saved;
  } catch {
    /* storage unavailable */
  }
  const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
  return nav in DICTS ? (nav as Lang) : "en";
}

/** t(key, fallback, vars) — same contract as Web/ux_i18n.js: dictionary entry
 *  for the active language, else the English fallback, {var} interpolation. */
export function translate(lang: Lang, key: string, fallback: string, vars?: Record<string, string | number>): string {
  const dict = DICTS[lang];
  let s = (dict && dict[key]) || fallback;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}

export function persistLang(lang: Lang): void {
  try {
    window.localStorage.setItem(LANG_KEY, lang);
  } catch {
    /* ignore — preference just won't persist */
  }
}
