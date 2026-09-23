/**
 * i18n messages — scope: pages/Audit
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "audit.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("audit.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 * - Backend identifiers (audit_events, audit_ledger, event_type, quick_check,
 *   PRAGMA / table names) stay verbatim inside every translation.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "audit.tab.events": { fa: "جریان رویدادها", de: "Ereignisstrom", es: "Flujo de eventos", ar: "تدفق الأحداث" },
  "audit.tab.ledger": { fa: "دفتر معاملات", de: "Handelsbuch", es: "Libro de operaciones", ar: "سجل التداولات" },
  "audit.tab.incidents": { fa: "رخدادها", de: "Vorfälle", es: "Incidentes", ar: "الحوادث" },
  "audit.tab.db": { fa: "یکپارچگی", de: "Integrität", es: "Integridad", ar: "سلامة البيانات" },
  "audit.pager.prev": { fa: "‹ قبلی", de: "‹ zurück", es: "‹ anterior", ar: "‹ السابق" },
  "audit.pager.page": { fa: "صفحه {p}", de: "Seite {p}", es: "página {p}", ar: "الصفحة {p}" },
  "audit.pager.next": { fa: "بعدی ›", de: "weiter ›", es: "siguiente ›", ar: "التالي ›" },
  "audit.panel.db": { fa: "پایگاه داده ممیزی (فراداده گزارش‌شده توسط بک‌اند)", de: "Audit-Datenbank (vom Backend gemeldete Metadaten)", es: "Base de datos de auditoría (metadatos reportados por el backend)", ar: "قاعدة بيانات التدقيق (بيانات وصفية يبلّغ عنها الخادم)" },
  "audit.db.k": { fa: "پایگاه داده", de: "Datenbank", es: "base de datos", ar: "قاعدة البيانات" },
  "audit.db.summary": { fa: "{mb} مگابایت · {tables} جدول", de: "{mb} MB · {tables} Tabellen", es: "{mb} MB · {tables} tablas", ar: "{mb} ميغابايت · {tables} جدول" },
  "audit.db.not_present": { fa: "موجود نیست", de: "nicht vorhanden", es: "no presente", ar: "غير موجودة" },
  "audit.metric.quick_check": { fa: "quick_check", de: "quick_check", es: "quick_check", ar: "quick_check" },
  "audit.metric.rows_signals": { fa: "ردیف‌های audit_signals", de: "audit_signals-Zeilen", es: "filas de audit_signals", ar: "سجلات audit_signals" },
  "audit.metric.rows_ledger": { fa: "ردیف‌های audit_ledger", de: "audit_ledger-Zeilen", es: "filas de audit_ledger", ar: "سجلات audit_ledger" },
  "audit.db.note": { fa: "دسترسی از طریق لایه ممیزی بک‌اند انجام می‌شود (endpointهای محدود و فقط‌خواندنی). هیچ مسیر پایگاه داده، درایور یا SQLی به مرورگر نمی‌رسد، جز نام فایلی که خود API برای اپراتور منتشر می‌کند.", de: "Der Zugriff erfolgt über die Backend-Audit-Schicht (begrenzte, read-only Endpunkte). Keine Datenbankpfade, Treiber oder SQL erreichen den Browser, außer der für den Operator sichtbare Dateiname, den die API selbst veröffentlicht.", es: "El acceso pasa por la capa de auditoría del backend (endpoints limitados y de solo lectura). Ninguna ruta de base de datos, controlador o SQL llega al navegador más allá del nombre de archivo visible para el operador que publica la propia API.", ar: "يمر الوصول عبر طبقة التدقيق في الخادم (نقاط نهاية محدودة للقراءة فقط). لا تصل أي مسارات قواعد بيانات أو أوامر SQL إلى المتصفح سوى اسم الملف الذي تنشره الواجهة نفسها للمشغّل." },
  "audit.age.metadata": { fa: "قدم فراداده", de: "Alter der Metadaten", es: "antigüedad de los metadatos", ar: "عمر البيانات الوصفية" },
  "audit.panel.events": { fa: "audit_events (جریان رویدادهای سیستم)", de: "audit_events (Ereignisstrom des Systems)", es: "audit_events (flujo de eventos del sistema)", ar: "audit_events (تدفق أحداث النظام)" },
  "audit.filter.event_type": { fa: "فیلتر event_type…", de: "event_type-Filter…", es: "filtro event_type…", ar: "مرشّح event_type…" },
  "audit.filter.event_type_aria": { fa: "فیلتر بر اساس نوع رویداد", de: "nach Ereignistyp filtern", es: "filtrar por tipo de evento", ar: "ترشيح حسب نوع الحدث" },
  "audit.csv.events_title": { fa: "همین صفحه از نمای فیلترشده فعلی را خروجی می‌گیرد (ردیف‌های بک‌اند بدون تغییر)", de: "exportiert DIESE Seite der aktuellen gefilterten Ansicht (Backend-Zeilen unverändert)", es: "exporta ESTA página de la vista filtrada actual (filas del backend sin modificar)", ar: "يصدّر هذه الصفحة من العرض المصفّى الحالي (صفوف الخادم كما هي)" },
  "audit.events.error": { fa: "رویدادهای ممیزی در دسترس نیست", de: "Audit-Ereignisse nicht verfügbar", es: "Eventos de auditoría no disponibles", ar: "أحداث التدقيق غير متاحة" },
  "audit.events.empty_filtered": { fa: "هیچ رویداد ممیزی مطابق با «{f}» یافت نشد.", de: "Keine Audit-Ereignisse passen zu „{f}“.", es: "Ningún evento de auditoría coincide con «{f}».", ar: "لا يوجد حدث تدقيق مطابق لـ «{f}»." },
  "audit.events.empty": { fa: "هیچ رویداد ممیزی مطابقی وجود ندارد.", de: "Keine Audit-Ereignisse passen.", es: "Ningún evento de auditoría coincide.", ar: "لا توجد أحداث تدقيق مطابقة." },
  "audit.events.empty_hint": { fa: "فیلتر event_type را تنظیم کنید یا منتظر فعالیت موتور بمانید.", de: "Den event_type-Filter anpassen oder auf Engine-Aktivität warten.", es: "Ajuste el filtro event_type o espere actividad del motor.", ar: "عدّل مرشّح event_type أو انتظر نشاط المحرك." },
  "audit.th.id": { fa: "شناسه", de: "Kennung", es: "ID", ar: "المعرّف" },
  "audit.th.time": { fa: "زمان", de: "Zeit", es: "Hora", ar: "الوقت" },
  "audit.th.type": { fa: "نوع", de: "Typ", es: "Tipo", ar: "النوع" },
  "audit.th.payload": { fa: "payload (خلاصه)", de: "Payload (Zusammenfassung)", es: "payload (resumen)", ar: "الحمولة (ملخص)" },
  "audit.drawer.event_title": { fa: "رویداد audit_event #{id} · {type}", de: "audit_event-Ereignis #{id} · {type}", es: "evento audit_event #{id} · {type}", ar: "حدث audit_event #{id} · {type}" },
  "audit.events.drawer_note": { fa: "با کلیک روی ردیف، کشوی payload خام باز می‌شود (متن بک‌اند، بدون تغییر).", de: "Ein Klick auf die Zeile öffnet den Roh-Payload-Drawer (Backend-Text, unverändert).", es: "Al hacer clic en la fila se abre el cajón con el payload sin procesar (texto del backend, sin modificar).", ar: "النقر على الصف يفتح درج الحمولة الخام (نص الخادم دون تعديل)." },
  "audit.panel.ledger": { fa: "audit_ledger (سوابق معاملات)", de: "audit_ledger (Handelsdatensätze)", es: "audit_ledger (registros de operaciones)", ar: "audit_ledger (سجلات التداول)" },
  "audit.filter.status": { fa: "فیلتر وضعیت…", de: "Statusfilter…", es: "filtro de estado…", ar: "مرشّح الحالة…" },
  "audit.filter.status_aria": { fa: "فیلتر بر اساس وضعیت", de: "nach Status filtern", es: "filtrar por estado", ar: "ترشيح حسب الحالة" },
  "audit.ledger.error": { fa: "دفتر در دسترس نیست", de: "Buch nicht verfügbar", es: "Libro no disponible", ar: "السجل غير متاح" },
  "audit.ledger.empty_filtered": { fa: "هیچ ردیف دفتر مطابق با «{f}» نیست.", de: "Keine Buchzeilen passen zu „{f}“.", es: "Ninguna fila del libro coincide con «{f}».", ar: "لا يوجد صف في السجل مطابق لـ «{f}»." },
  "audit.ledger.empty": { fa: "هیچ ردیف دفتر مطابقی نیست.", de: "Keine Buchzeilen passen.", es: "Ninguna fila del libro coincide.", ar: "لا توجد صفوف مطابقة في السجل." },
  "audit.th.ticket": { fa: "تیکت", de: "Ticket", es: "Ticket", ar: "التذكرة" },
  "audit.th.symbol": { fa: "نماد", de: "Symbol", es: "Símbolo", ar: "الرمز" },
  "audit.th.dir": { fa: "جهت", de: "Richtung", es: "Dirección", ar: "الاتجاه" },
  "audit.th.volume": { fa: "حجم", de: "Volumen", es: "Volumen", ar: "الحجم" },
  "audit.th.entry": { fa: "ورود", de: "Einstieg", es: "Entrada", ar: "الدخول" },
  "audit.th.status": { fa: "وضعیت", de: "Status", es: "Estado", ar: "الحالة" },
  "audit.th.pnl": { fa: "PnL", de: "G/V", es: "PnL", ar: "PnL" },
  "audit.drawer.ledger_title": { fa: "audit_ledger — تیکت {t}", de: "audit_ledger — Ticket {t}", es: "audit_ledger — ticket {t}", ar: "audit_ledger — التذكرة {t}" },
  "audit.panel.incidents": { fa: "فهرست رخدادها", de: "Liste der Vorfälle", es: "Inventario de incidentes", ar: "جرد الحوادث" },
  "audit.filter.severity_aria": { fa: "فیلتر شدت", de: "Schweregrad-Filter", es: "filtro de gravedad", ar: "مرشّح الخطورة" },
  "audit.filter.all_severities": { fa: "همه شدت‌ها", de: "alle Schweregrade", es: "todas las gravedades", ar: "كل مستويات الخطورة" },
  "audit.incidents.error": { fa: "ذخیره رخدادها در دسترس نیست", de: "Vorfallsspeicher nicht verfügbar", es: "Almacén de incidentes no disponible", ar: "مخزن الحوادث غير متاح" },
  "audit.incidents.empty_filtered": { fa: "رخدادی مطابق با شدت «{f}» نیست.", de: "Keine Vorfälle mit Schweregrad „{f}“.", es: "Ningún incidente coincide con la gravedad «{f}».", ar: "لا توجد حادثة بمستوى خطورة «{f}»." },
  "audit.incidents.empty": { fa: "رخداد مطابقی نیست.", de: "Keine Vorfälle passen.", es: "Ningún incidente coincide.", ar: "لا توجد حوادث مطابقة." },
  "audit.th.severity": { fa: "شدت", de: "Schweregrad", es: "Gravedad", ar: "الخطورة" },
  "audit.th.category": { fa: "دسته", de: "Kategorie", es: "Categoría", ar: "الفئة" },
  "audit.th.component": { fa: "مؤلفه", de: "Komponente", es: "Componente", ar: "المكوّن" },
  "audit.th.title": { fa: "عنوان", de: "Titel", es: "Título", ar: "العنوان" },
  "audit.th.created": { fa: "ایجاد", de: "Erstellt", es: "Creado", ar: "الإنشاء" },
  "audit.drawer.incident_title": { fa: "رخداد {id}", de: "Vorfall {id}", es: "incidente {id}", ar: "حادثة {id}" },
  "audit.panel.integrity": { fa: "یکپارچگی پایگاه داده (PRAGMA فقط‌خواندنی از طریق بک‌اند)", de: "Datenbankintegrität (read-only PRAGMA über das Backend)", es: "Integridad de la base de datos (PRAGMA de solo lectura vía backend)", ar: "سلامة قاعدة البيانات (PRAGMA للقراءة فقط عبر الخادم)" },
  "audit.integrity.error": { fa: "endpoint یکپارچگی در دسترس نیست.", de: "Integritäts-Endpunkt nicht verfügbar.", es: "El endpoint de integridad no está disponible.", ar: "نقطة نهاية السلامة غير متاحة." },
  "audit.chip.tables_counted": { fa: "جدول شمارش‌شده", de: "gezählte Tabellen", es: "tablas contadas", ar: "الجداول المعدودة" },
  "audit.drawer.payload_label": { fa: "payload بک‌اند (بدون تغییر)", de: "Backend-Payload (wörtlich)", es: "payload del backend (verbatim)", ar: "حمولة الخادم (حرفية)" },
};
