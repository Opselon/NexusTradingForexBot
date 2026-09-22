/**
 * i18n messages — scope: pages/Positions
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "positions.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("positions.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 * - Trading notation (SL, TP, PnL, CSV, BUY/SELL enum values, symbol ids)
 *   stays verbatim inside every translation.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "positions.source.v1": { fa: "مبدل v1", de: "v1-Adapter", es: "adaptador v1", ar: "محوّل v1" },
  "positions.source.snapshot": { fa: "اسنپ‌شات کانونیکال (انتظار endpoint v1)", de: "kanonischer Snapshot (v1-Endpunkt ausstehend)", es: "instantánea canónica (endpoint v1 pendiente)", ar: "لقطة قانونية (نقطة v1 معلّقة)" },
  "positions.source.label": { fa: "منبع: {src}", de: "Quelle: {src}", es: "fuente: {src}", ar: "المصدر: {src}" },
  "positions.source.snapshot_version": { fa: "اسنپ‌شات نسخه {n}", de: "Snapshot v{n}", es: "instantánea v{n}", ar: "لقطة v{n}" },
  "positions.th.ticket": { fa: "تیکت", de: "Ticket", es: "Ticket", ar: "التذكرة" },
  "positions.th.symbol": { fa: "نماد", de: "Symbol", es: "Símbolo", ar: "الرمز" },
  "positions.th.side": { fa: "جهت", de: "Seite", es: "Lado", ar: "الجهة" },
  "positions.th.volume": { fa: "حجم", de: "Volumen", es: "Volumen", ar: "الحجم" },
  "positions.th.entry": { fa: "ورود", de: "Einstieg", es: "Entrada", ar: "الدخول" },
  "positions.th.current": { fa: "فعلی", de: "Aktuell", es: "Actual", ar: "الحالي" },
  "positions.th.sl": { fa: "SL", de: "SL", es: "SL", ar: "SL" },
  "positions.th.tp": { fa: "TP", de: "TP", es: "TP", ar: "TP" },
  "positions.th.pnl": { fa: "PnL", de: "G/V", es: "PnL", ar: "PnL" },
  "positions.th.swap": { fa: "سوآپ", de: "Swap", es: "Swap", ar: "التباديل" },
  "positions.th.opened": { fa: "بازشده", de: "Eröffnet", es: "Abierta", ar: "الفتح" },
  "positions.th.dir": { fa: "جهت", de: "Richtung", es: "Dirección", ar: "الاتجاه" },
  "positions.th.status": { fa: "وضعیت", de: "Status", es: "Estado", ar: "الحالة" },
  "positions.th.closed": { fa: "بسته‌شده", de: "Geschlossen", es: "Cerrada", ar: "الإغلاق" },
  "positions.btn.sl_tp": { fa: "SL/TP", de: "SL/TP", es: "SL/TP", ar: "SL/TP" },
  "positions.actions.no_ticket": { fa: "بدون تیکت", de: "kein Ticket", es: "sin ticket", ar: "بلا تذكرة" },
  "positions.dialog.modify_summary": { fa: "{sym} {side} {vol} لات @ {price}", de: "{sym} {side} {vol} Lots @ {price}", es: "{sym} {side} {vol} lotes @ {price}", ar: "{sym} {side} {vol} لوت @ {price}" },
  "positions.dialog.close_summary": { fa: "{sym} {side} {vol} لات @ {price} · شناور {fl}", de: "{sym} {side} {vol} Lots @ {price} · schwebend {fl}", es: "{sym} {side} {vol} lotes @ {price} · flotante {fl}", ar: "{sym} {side} {vol} لوت @ {price} · عائمة {fl}" },
  "positions.metric.floating": { fa: "PnL شناور", de: "Schwebender G/V", es: "PnL flotante", ar: "أرباح/خسائر عائمة" },
  "positions.status.partial": { fa: "جزئی", de: "TEILWEISE", es: "PARCIAL", ar: "جزئي" },
  "positions.metric.floating_sub": { fa: "Σ فیلدهای سود بک‌اند", de: "Σ der Backend-Profitfelder", es: "Σ de los campos de beneficio del backend", ar: "مجموع حقول الربح من الخادم" },
  "positions.metric.partial_sub": { fa: "برخی ردیف‌ها مقدار سود ندارند — جمع ارائه نشد", de: "einige Zeilen ohne Profitwert — Summe wird nicht gezeigt", es: "algunas filas sin valor de beneficio — suma no mostrada", ar: "بعض الصفوف بلا قيمة ربح — لم تُعرض المجموع" },
  "positions.metric.open": { fa: "پوزیشن‌های باز", de: "Offene Positionen", es: "Posiciones abiertas", ar: "المراكز المفتوحة" },
  "positions.metric.volume_sub": { fa: "حجم {n} لات", de: "Volumen {n} Lots", es: "volumen {n} lotes", ar: "الحجم {n} لوت" },
  "positions.metric.winners": { fa: "برندگان / بازندگان", de: "Gewinner / Verlierer", es: "Ganadores / perdedores", ar: "الرابحون / الخاسرون" },
  "positions.metric.winners_sub": { fa: "بر اساس علامت شناور بک‌اند (فقط نمایش)", de: "nach Vorzeichen des Backend-Schwebewerts (nur Anzeige)", es: "según el signo flotante del backend (solo visualización)", ar: "حسب إشارة القيمة العائمة من الخادم (للعرض فقط)" },
  "positions.metric.crosscheck": { fa: "تأیید متقابل مبدل", de: "Gegenprüfung des Adapters", es: "Verificación cruzada del adaptador", ar: "تحقق متبادل من المحوّل" },
  "positions.status.mismatch": { fa: "ناسازگاری", de: "ABWEICHUNG", es: "DISCREPANCIA", ar: "عدم تطابق" },
  "positions.status.match": { fa: "سازگار", de: "STIMMT ÜBEREIN", es: "COINCIDE", ar: "متطابق" },
  "positions.metric.crosscheck_sub": { fa: "اسنپ‌شات {a} در برابر v1 {b} پوزیشن", de: "Snapshot {a} vs. v1 {b} Positionen", es: "instantánea {a} frente a {b} posiciones en v1", ar: "لقطة {a} مقابل {b} مركز في v1" },
  "positions.close.title": { fa: "بستن پوزیشن #{n}", de: "Position #{n} schließen", es: "Cerrar posición #{n}", ar: "إغلاق المركز #{n}" },
  "positions.close.confirm": { fa: "تأیید بستن", de: "Schließen bestätigen", es: "Confirmar cierre", ar: "تأكيد الإغلاق" },
  "positions.close.body": { fa: "بستن پوزیشن #{n} در قیمت بازار از طریق OrderLifecycleManager.", de: "Position #{n} zum Marktpreis über den OrderLifecycleManager schließen.", es: "Cerrar la posición #{n} a precio de mercado mediante el OrderLifecycleManager.", ar: "إغلاق المركز #{n} بالسعر السوقي عبر OrderLifecycleManager." },
  "positions.close.note": { fa: "بک‌اند ممکن است رد کند (Guardian، وضعیت، اتصال) — پاسخ تعیین‌کننده است؛ این پنجره فقط از کلیک اشتباه جلوگیری می‌کند.", de: "Das Backend kann ablehnen (Guardian, Zustand, Konnektivität) — die Antwort entscheidet; dieser Dialog verhindert nur Fehlklicks.", es: "El backend puede rechazar (guardian, estado, conectividad) — decide la respuesta; este diálogo solo evita clics erróneos.", ar: "قد يرفض الخادم (الحارس، الحالة، الاتصال) — القرار للرد، ووظيفة هذا الحوار هي فقط منع النقر الخاطئ." },
  "positions.modify.title": { fa: "ویرایش SL/TP · پوزیشن #{n}", de: "SL/TP ändern · Position #{n}", es: "Modificar SL/TP · posición #{n}", ar: "تعديل SL/TP · المركز #{n}" },
  "positions.modify.confirm": { fa: "ارسال تغییرات", de: "Änderung senden", es: "Enviar modificación", ar: "إرسال التعديل" },
  "positions.modify.sl_label": { fa: "حد ضرر (۰ = حذف)", de: "Stop-Loss (0 = löschen)", es: "stop loss (0 = borrar)", ar: "وقف الخسارة (0 = مسح)" },
  "positions.modify.tp_label": { fa: "حد سود (۰ = حذف)", de: "Take-Profit (0 = löschen)", es: "take profit (0 = borrar)", ar: "جني الأرباح (0 = مسح)" },
  "positions.modify.note": { fa: "به‌صورت {ticket, stop_loss, take_profit} برای POST /api/positions/modify ارسال می‌شود — OrderLifecycleManager بر اساس مشخصات نماد کارگزار و گیت enforce_stop_loss اعتبارسنجی می‌کند و ممکن است رد کند؛ پاسخ نهایی است.", de: "Wird als {ticket, stop_loss, take_profit} an POST /api/positions/modify gesendet — der OrderLifecycleManager validiert gegen die Brokersymbol-Spezifikation und das enforce_stop_loss-Gate und kann ablehnen; die Antwort entscheidet.", es: "Se envía como {ticket, stop_loss, take_profit} a POST /api/positions/modify — el OrderLifecycleManager valida contra la especificación del símbolo del bróker y el gate enforce_stop_loss, y puede rechazar; decide la respuesta.", ar: "يُرسل كـ {ticket, stop_loss, take_profit} إلى POST /api/positions/modify — يتحقق OrderLifecycleManager وفق مواصفات الرمز لدى الوسيط وبوابة enforce_stop_loss وقد يرفض؛ القرار للرد." },
  "positions.panel.open": { fa: "پوزیشن‌های باز ({n})", de: "Offene Positionen ({n})", es: "Posiciones abiertas ({n})", ar: "المراكز المفتوحة ({n})" },
  "positions.loading.positions": { fa: "خواندن مبدل کارگزار…", de: "Broker-Adapter wird gelesen…", es: "Leyendo el adaptador del bróker…", ar: "جارٍ قراءة محوّل الوسيط…" },
  "positions.error.positions": { fa: "endpoint پوزیشن‌ها در دسترس نیست", de: "Positions-Endpunkt nicht verfügbar", es: "Endpoint de posiciones no disponible", ar: "نقطة نهاية المراكز غير متاحة" },
  "positions.empty.positions": { fa: "پوزیشن بازی وجود ندارد.", de: "Keine offenen Positionen.", es: "No hay posiciones abiertas.", ar: "لا توجد مراكز مفتوحة." },
  "positions.empty.positions_hint": { fa: "اسنپ‌شات مبدل کارگزار خالی است — چیزی پنهان یا تخمین زده نشده.", de: "Snapshot des Broker-Adapters ist leer — nichts wird versteckt oder geschätzt.", es: "La instantánea del adaptador del bróker está vacía — no se oculta ni se estima nada.", ar: "لقطة محوّل الوسيط فارغة — لا شيء مخفي أو مقدّر." },
  "positions.crosscheck.text": { fa: "endpoint مبدل v1 و اسنپ‌شات کانونیکال در شمار پوزیشن‌های باز اختلاف دارند ({a} در برابر {b}). معمولاً شکاف زمانی تازه‌سازی است — هر دو را دوباره بخوانید؛ اگر ادامه یافت، پیش از اعتماد به هر کدام، انطباق را در صفحه Trading بررسی کنید.", de: "Das v1-Adapter-Endpunkt und der kanonische Snapshot widersprechen sich bei der Anzahl offener Positionen ({a} vs. {b}). Meist eine Timing-Lücke beim Aktualisieren — beide neu laden; besteht es weiter, vor dem Vertrauen die Abstimmung auf der Trading-Seite prüfen.", es: "El endpoint del adaptador v1 y la instantánea canónica discrepan en el número de posiciones abiertas ({a} frente a {b}). Suele ser un desfase de actualización — vuelva a traer ambos; si persiste, revise la conciliación en la página de Trading antes de confiar en cualquiera.", ar: "تتعارض نقطة نهاية المحوّل v1 مع اللقطة القانونية في عدد المراكز المفتوحة ({a} مقابل {b}). غالبًا فارق توقيت في التحديث — أعد قراءة الاثنين؛ وإن استمر، راجع التوفيق في صفحة التداول قبل الوثوق بأي منهما." },
  "positions.panel.ledger": { fa: "دفتر معاملات بسته‌شده (بازسازی‌شده از کارگزار)", de: "Buch geschlossener Trades (vom Broker rekonstruiert)", es: "Libro de operaciones cerradas (reconstruido desde el bróker)", ar: "سجل الصفقات المغلقة (مُعاد بناؤه من الوسيط)" },
  "positions.filter.ledger_aria": { fa: "فیلتر وضعیت دفتر", de: "Statusfilter des Buchs", es: "filtro de estado del libro", ar: "مرشّح حالة السجل" },
  "positions.filter.all_statuses": { fa: "همه وضعیت‌ها", de: "alle Statuswerte", es: "todos los estados", ar: "كل الحالات" },
  "positions.age.label": { fa: "قدم", de: "Alter", es: "antigüedad", ar: "العمر" },
  "positions.loading.ledger": { fa: "خواندن دفتر ممیزی…", de: "Audit-Buch wird gelesen…", es: "Leyendo el libro de auditoría…", ar: "جارٍ قراءة سجل التدقيق…" },
  "positions.error.ledger": { fa: "دفتر در دسترس نیست", de: "Buch nicht verfügbar", es: "Libro no disponible", ar: "السجل غير متاح" },
  "positions.empty.ledger": { fa: "هنوز معامله بسته‌ای در دفتر نیست.", de: "Noch keine geschlossenen Trades im Buch.", es: "Aún no hay operaciones cerradas en el libro.", ar: "لا توجد صفقات مغلقة في السجل بعد." },
  "positions.empty.ledger_hint": { fa: "ردیف‌ها پس از بسته شدن معامله و خواندن تاریخچه کارگزار (یا جایگزین دفتر موتور) نمایان می‌شوند.", de: "Zeilen erscheinen, sobald Trades geschlossen und die Brokershistorie (oder die Engine-Buch-Fallback) gelesen wird.", es: "Las filas aparecen cuando se cierran las operaciones y se lee el historial del bróker (o el libro alternativo del motor).", ar: "تظهر الصفوف بعد إغلاق الصفقات وقراءة سجل الوسيط (أو السجل البديل للمحرك)." },
  "positions.empty.rows_ledger": { fa: "ردیفی در دفتر نیست.", de: "Keine Buchzeilen.", es: "No hay filas en el libro.", ar: "لا توجد صفوف في السجل." },
  "positions.csv.title": { fa: "دقیقاً ردیف‌های برگشتی از /api/account/trades را خروجی می‌گیرد — بدون بازپرسش، بدون مقدار اضافه", de: "exportiert genau die von /api/account/trades gelieferten Zeilen — keine Neuanfrage, keine zusätzlichen Werte", es: "exporta exactamente las filas devueltas por /api/account/trades — sin reconsultar, sin valores añadidos", ar: "يصدّر صفوف /api/account/trades بالضبط — دون إعادة استعلام أو قيم مضافة" },
  "positions.csv.export": { fa: "خروجی CSV", de: "CSV exportieren", es: "exportar CSV", ar: "تصدير CSV" },
  "positions.ledger.note": { fa: "ردیف‌هایی که هنوز در جدول پوزیشن‌های باز هستند اینجا پنهان می‌شوند (به‌جز فیلتر وضعیت) تا یک پوزیشن در یک صفحه دو بار شمرده نشود.", de: "Buchzeilen, die noch in der Tabelle der offenen Positionen stehen, werden hier ausgeblendet (abgesehen vom Statusfilter), damit eine Position nie zweimal gezählt wird.", es: "Las filas del libro que siguen en la tabla de posiciones abiertas se ocultan aquí (aparte del filtro de estado) para que una posición nunca se cuente dos veces en una pantalla.", ar: "تُخفى هنا صفوف السجل التي لا تزال موجودة في جدول المراكز المفتوحة (تجاهلًا لمرشّح الحالة) حتى لا يُحسب مركز مرتين في الشاشة الواحدة." },
};
