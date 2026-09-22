/**
 * i18n messages — scope: app
 * OWNER: orchestrator (chrome shell) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "shell.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("shell.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "shell.skip_content": { fa: "پرش به محتوا", de: "Zum Inhalt springen", es: "Saltar al contenido", ar: "انتقل إلى المحتوى" },
  "shell.nav.primary": { fa: "ناوبری اصلی", de: "Hauptnavigation", es: "Navegación principal", ar: "التنقل الرئيسي" },
  "shell.lang.aria": { fa: "زبان", de: "Sprache", es: "Idioma", ar: "اللغة" },
  "shell.dense.toggle": { fa: "تغییر چیدمان فشرده", de: "Dichtes Layout umschalten", es: "Alternar diseño compacto", ar: "تبديل التخطيط المضغوط" },
  "shell.dense.title": { fa: "چیدمان فشرده (فقط بصری)", de: "Dichtes Layout (nur visuell)", es: "Diseño compacto (solo visual)", ar: "التخطيط المضغوط (بصري فقط)" },
  "shell.sidebar.toggle": { fa: "نوار کناری (Alt+B)", de: "Seitenleiste umschalten (Alt+B)", es: "Alternar barra lateral (Alt+B)", ar: "تبديل الشريط الجانبي (Alt+B)" },
  "shell.engine.title": { fa: "وضعیت حلقه موتور (مستقل از بک‌اند)", de: "Status des Engine-Loops (vom Backend autoritativ)", es: "Estado del bucle del motor (lo determina el backend)", ar: "حالة حلقة المحرك (حدده الخادم)" },
  "shell.engine.label": { fa: "موتور", de: "ENGINE", es: "MOTOR", ar: "المحرك" },
  "shell.engine.running": { fa: "در حال اجرا", de: "LÄUFT", es: "EN MARCHA", ar: "يعمل" },
  "shell.engine.stopped": { fa: "متوقف", de: "GESTOPPT", es: "DETENIDO", ar: "متوقف" },
  "shell.health.title": { fa: "سلامت کلی بک‌اند (health.overall از /api/status)", de: "Backend-Gesundheit health.overall aus /api/status", es: "health.overall del backend desde /api/status", ar: "صحة الخادم health.overall من /api/status" },
  "shell.health.label": { fa: "سلامت —", de: "HEALTH —", es: "ESTADO —", ar: "الصحة —" },
  "shell.fresh.title": { fa: "مراحل تازگی خط لوله (live_freshness بک‌اند)", de: "Frischestufen der Pipeline (Backend live_freshness)", es: "Etapas de frescura del pipeline (live_freshness del backend)", ar: "مراحل نضج خط الإنتاج (live_freshness من الخادم)" },
  "shell.clock.title": { fa: "ساعت دیواری محلی (کمک بصری)", de: "Lokale Systemzeit (visuelle Hilfe)", es: "Hora local del sistema (ayuda visual)", ar: "الساعة المحلية (مساعدة بصرية)" },
  "shell.loading": { fa: "در حال اتصال به بک‌اند NSE…", de: "Verbinde mit dem NSE-Backend…", es: "Conectando al backend de NSE…", ar: "جارٍ الاتصال بخادم NSE…" },
  "shell.loading_feature": { fa: "در حال بارگذاری {name}…", de: "Lade {name}…", es: "Cargando {name}…", ar: "جارٍ تحميل {name}…" },
  "shell.unreachable": { fa: "بک‌اند در دسترس نیست", de: "Backend nicht erreichbar", es: "Backend no disponible", ar: "الخادم غير متاح" },
  "shell.unknown_route": { fa: "مسیر ناشناخته", de: "Unbekannte Route", es: "Ruta desconocida", ar: "مسار غير معروف" },
  "shell.section.error_fallback": { fa: "نقطه پایانی در دسترس نیست.", de: "Endpunkt nicht verfügbar.", es: "Punto final no disponible.", ar: "نقطة النهاية غير متاحة." },
  "shell.tri.unknown": { fa: "نامعلوم", de: "UNBEKANNT", es: "DESCONOCIDO", ar: "غير معروف" },
  "ux.table.filter_ph": { fa: "فیلتر ردیف‌ها (تیکت / نماد / متن)…", de: "Zeilen filtern (Ticket / Symbol / Text)…", es: "filtrar filas (ticket / símbolo / texto)…", ar: "تصفية الصفوف (تذكرة / رمز / نص)…" },
  "ux.table.filter_aria": { fa: "فیلتر ردیف‌های جدول", de: "Tabellenzeilen filtern", es: "Filtrar filas de la tabla", ar: "تصفية صفوف الجدول" },
  "ux.table.rows": { fa: "ردیف", de: "Zeilen", es: "filas", ar: "صفوف" },
  "ux.table.of": { fa: "از", de: "von", es: "de", ar: "من" },
  "ux.table.clear": { fa: "پاک کردن", de: "löschen", es: "limpiar", ar: "مسح" },
  "ux.table.empty": { fa: "ردیفی نیست.", de: "Keine Zeilen.", es: "Sin filas.", ar: "لا صفوف." },
  "ux.table.empty_filter": { fa: "هیچ ردیفی با فیلتر مطابقت ندارد.", de: "Keine Zeilen entsprechen dem Filter.", es: "Ninguna fila coincide con el filtro.", ar: "لا توجد صفوف مطابقة للفلتر." },
  "ux.table.sort_by": { fa: "مرتب‌سازی بر اساس {name}", de: "Sortieren nach {name}", es: "Ordenar por {name}", ar: "ترتيب حسب {name}" },
  "ux.meter.unknown": { fa: "نامعلوم", de: "UNBEKANNT", es: "DESCONOCIDO", ar: "غير معروف" },
  "ux.meter.no_limit": { fa: "بدون سقف از بک‌اند — نوار نامشخص است و هرگز برآورده نمایش داده نمی‌شود", de: "kein Backend-Limit in der Nutzlast — Balken unbestimmt, nie als erfüllt dargestellt", es: "sin límite del backend en la respuesta — la barra es indeterminada, nunca se muestra como cumplida", ar: "لا يوجد حد من الخادم — الشريط غير محدد ولا يُعرض أبداً كمُحقق" },
  "ux.meter.no_value": { fa: "بدون مقدار از بک‌اند — چیزی اندازه‌گیری نشده", de: "kein Backend-Wert — nichts gemessen", es: "sin valor del backend — nada medido", ar: "لا قيمة من الخادم — لا يوجد قياس" },
  "ux.meter.of_limit": { fa: "{pct}٪ از سقف بک‌اند (حاصل‌ضرب دو مقدار بک‌اند)", de: "{pct} % des Backend-Limits (Berechnung aus zwei Backend-Werten)", es: "{pct} % del límite del backend (cálculo sobre dos valores del backend)", ar: "{pct}٪ من حد الخادم (حساب على قيمتين من الخادم)" },
  "ux.drawer.close_aria": { fa: "بستن پنل", de: "Panel schließen", es: "Cerrar panel", ar: "إغلاق اللوحة" },
  "ux.cmd.accepted": { fa: "دستور توسط بک‌اند پذیرفته شد.", de: "Vom Backend angenommen.", es: "Comando aceptado por el backend.", ar: "قبل الخادم الأمر." },
  "ux.cmd.refused": { fa: "بک‌اند دستور را رد کرد.", de: "Vom Backend abgelehnt.", es: "El backend rechazó el comando.", ar: "رفض الخادم الأمر." },
  "ux.cmd.failed": { fa: "دستور ناموفق بود.", de: "Befehl fehlgeschlagen.", es: "El comando falló.", ar: "فشل الأمر." },
  "ux.reason.HIGH_IMPACT_NEWS": { fa: "پنجره اخبار با تأثیر بالا — معاملات برای ایمنی متوقف شد.", de: "Fenster mit hochwirkenden News — Handel pausiert.", es: "Ventana de noticias de alto impacto — trading pausado por seguridad.", ar: "نافذة أخبار عالية التأثير — توقف التداول للأمان." },
  "ux.reason.BLOCKED_BY_GUARDIAN_UNSAFE_REGIME.detail": { fa: "نگهبان رژیم، بازار را ناامن ارزیابی کرد؛ مدل مشورت نگرفت (هیچ اطمینانی تولید نمی‌شود).", de: "Der Regime-Guardian stufte den Markt als unsicher ein; das Modell wurde nicht befragt (es entsteht keine Konfidenz).", es: "El guardián de régimen clasificó el mercado como no seguro; no se consultó al modelo (no produce confianza).", ar: "صنّف حارس النموذج السوق أنه غير آمن؛ لم تُستشر النموذج (لا تُنتج أي ثقة)." },
  "ux.reason.CONFIDENCE_GATE.detail": { fa: "نامزد پیش از رسیدن به اجرا، توسط دروازه اطمینان فیلتر شد.", de: "Der Kandidat wurde vor der Ausführung durch das Konfidenz-Gate gefiltert.", es: "El candidato fue filtrado por el umbral de confianza antes de llegar a la ejecución.", ar: "جرى تصفية المرشح عبر بوابة الثقة قبل التنفيذ." },
  "ux.reason.NO_CANDIDATE.detail": { fa: "هیچ طرحی از فیلترهای نامزد عبور نکرد.", de: "Kein Setup hat die Kandidatenfilter passiert.", es: "Ninguna idea superó los filtros de candidatos.", ar: "لم يجتز أي مخطط المرشحات." },
  "ux.reason.HIGH_IMPACT_NEWS.detail": { fa: "فرماندار ریسک اخبار تصمیم را در دوره انتشار با تأثیر بالا مسدود کرد.", de: "Der News-Risiko-Governor blockierte die Entscheidung während einer hochwirkenden Veröffentlichung.", es: "El gobernador de riesgo de noticias bloqueó la decisión durante una publicación de alto impacto.", ar: "منع حاكم مخاطر الأخبار القرار أثناء إعلان عالي التأثير." },
};
