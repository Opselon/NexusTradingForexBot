/**
 * i18n messages — scope: pages/Dashboard
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "dash.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("dash.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  "dash.age.label": { fa: "سن", de: "Alter", es: "edad", ar: "عمر" },
  "dash.chart.aria": { fa: "نمودار قیمت", de: "Preischart", es: "Gráfico de precios", ar: "رسم السعر" },
  "dash.chart.awaiting": { fa: "در انتظار تیک — هنوز هیچ کندلی نیست. نمودار فقط میله‌های واقعی MT5/موتور را رسم می‌کند، هرگز مصنوعی.", de: "Warte auf Ticks — noch keine Kerzen. Das Chart zeichnet echte MT5-/Engine-Bars, nie synthetische.", es: "Esperando ticks: aún no hay velas. El gráfico dibuja solo barras reales de MT5/motor, nunca sintéticas.", ar: "بانتظار التيكات — لا شموع بعد. يرسم الرسم أشرطة MT5/المحرك الحقيقية فقط لا الاصطناعية." },
  "dash.chart.canvas_aria": { fa: "{n} کندل {tf} برای {sym}", de: "{n} {tf}-Kerzen für {sym}", es: "{n} velas {tf} de {sym}", ar: "{n} شمعة {tf} لـ {sym}" },
  "dash.chart.completed": { fa: "تکمیل‌شده", de: "abgeschlossen", es: "completada", ar: "مكتملة" },
  "dash.chart.failed": { fa: "تاریخچه نمودار ناموفق بود: {e}", de: "Chart-Historie fehlgeschlagen: {e}", es: "falló el historial del gráfico: {e}", ar: "فشل تاريخ الرسم: {e}" },
  "dash.chart.forming": { fa: "در حال شکل‌گیری", de: "wird gebildet", es: "formándose", ar: "قيد التشكّل" },
  "dash.chart.loading": { fa: "در حال بارگذاری تاریخچه کارگزار…", de: "Broker-Historie wird geladen…", es: "cargando historial del bróker…", ar: "جارٍ تحميل تاريخ الوسيط…" },
  "dash.chart.show_bars": { fa: "نمایش {n} میله آخر", de: "letzte {n} Bars anzeigen", es: "mostrar las últimas {n} velas", ar: "عرض آخر {n} شمعة" },
  "dash.chart.tick_stale": { fa: "تیک قدیمی", de: "TICK VERALTET", es: "TICK ANTIGUO", ar: "تيك قديم" },
  "dash.chart.unavailable": { fa: "در دسترس نیست", de: "NICHT VERFÜGBAR", es: "NO DISPONIBLE", ar: "غير متاح" },
  "dash.feat.cat_all": { fa: "همه ابعاد", de: "Alle Dimensionen", es: "Todas las dimensiones", ar: "كل الأبعاد" },
  "dash.feat.cat_candlestick": { fa: "آناتومی کندل", de: "Kerzenanatomie", es: "Anatomía de las velas", ar: "تشريح الشمعة" },
  "dash.feat.cat_ichimoku": { fa: "ایچیموکو کینکو هیو", de: "Ichimoku Kinko Hyo", es: "Ichimoku Kinko Hyo", ar: "إيشيموكو كينكو هيو" },
  "dash.feat.cat_ict": { fa: "مفاهیم پول هوشمند ICT", de: "ICT Smart-Money-Konzepte", es: "Conceptos de dinero inteligente ICT", ar: "مفاهيم الأموال الذكية ICT" },
  "dash.feat.cat_liquidity": { fa: "خانواده نقدینگی (60..69)", de: "Liquiditäts-Familie (60..69)", es: "Familia de liquidez (60..69)", ar: "عائلة السيولة (60..69)" },
  "dash.feat.cat_multitimeframe": { fa: "چند تایم‌فریم و حمایت/مقاومت", de: "Mehrzeitrahmen & S/R", es: "Multi-timeframe y S/R", ar: "الأطر الزمنية المتعددة وS/R" },
  "dash.feat.cat_news": { fa: "خانواده اخبار (50..59)", de: "News-Familie (50..59)", es: "Familia de noticias (50..59)", ar: "عائلة الأخبار (50..59)" },
  "dash.feat.cat_patterns": { fa: "ساختار و الگوهای نوسان", de: "Struktur- & Swing-Muster", es: "Estructura y patrones de oscilación", ar: "البنية وأنماط التذبذب" },
  "dash.feat.cat_sessions": { fa: "تأخیر سشن‌های بازار", de: "Verzögerungen der Marktzeiten", es: "Retrasos de las sesiones de mercado", ar: "تأخّرات جلسات السوق" },
  "dash.feat.cat_volatility": { fa: "نوسان و ساختار خرد", de: "Volatilität & Mikrostruktur", es: "Volatilidad y microestructura", ar: "التذبذب والبنية الدقيقة" },
  "dash.feat.cats_aria": { fa: "دسته ویژگی", de: "Feature-Kategorie", es: "categoría de características", ar: "فئة الخصائص" },
  "dash.feat.dim": { fa: "بُعد {n}", de: "Dim {n}", es: "Dim {n}", ar: "بُعد {n}" },
  "dash.feat.empty": { fa: "در انتظار جریان زنده ویژگی‌ها از موتور…", de: "Warte auf Live-Feature-Stream der Engine…", es: "esperando el flujo en vivo de características del motor…", ar: "بانتظار تدفق الخصائص المباشر من المحرك…" },
  "dash.feat.empty_hint": { fa: "هنوز بردار ویژگی در این نشست نیست — کاشی‌ها هرگز با جای‌گذار پیش‌پر نمی‌شوند.", de: "Noch kein Feature-Vektor in dieser Sitzung — Kacheln werden nie mit Platzhaltern vorbefüllt.", es: "Aún no hay vector de características en esta sesión; las casillas nunca se rellenan con marcadores.", ar: "لا يوجد متجه خصائص في هذه الجلسة — لا تُملأ البلاطات مسبقًا بقيم بديلة." },
  "dash.feat.group_base": { fa: "BASE 0..49 ({n} فعال)", de: "BASE 0..49 ({n} aktiv)", es: "BASE 0..49 ({n} activos)", ar: "BASE 0..49 ({n} فعّال)" },
  "dash.feat.group_liq": { fa: "نقدینگی 60..69", de: "LIQUIDITÄT 60..69", es: "LIQUIDEZ 60..69", ar: "السيولة 60..69" },
  "dash.feat.group_news": { fa: "NEWS 50..59 (جای خانواده)", de: "NEWS 50..59 (Familien-Slot)", es: "NEWS 50..59 (ranura de familia)", ar: "NEWS 50..59 (خانة العائلة)" },
  "dash.feat.scale": { fa: "مقیاس نرمال‌شده [-3.0, +3.0]", de: "Normalisierte Skala [-3.0, +3.0]", es: "Escala normalizada [-3.0, +3.0]", ar: "مقياس مُطبَّع [-3.0, +3.0]" },
  "dash.feat.tile_title": { fa: "{name} · بُعد {n} · {s}", de: "{name} · Dimension {n} · {s}", es: "{name} · dimensión {n} · {s}", ar: "{name} · بُعد {n} · {s}" },
  "dash.feat.title": { fa: "ویژگی‌ها", de: "Features", es: "Características", ar: "الخصائص" },
  "dash.preds.empty": { fa: "هنوز پیش‌بینی هوش مصنوعی ثبت نشده است.", de: "Noch keine KI-Vorhersagen erfasst.", es: "Aún no se han registrado predicciones de IA.", ar: "لم تُسجَّل تنبؤات ذكاء اصطناعي بعد." },
  "dash.preds.empty_hint": { fa: "audit_signals خالی است — در انتظار تصمیم‌های زنده موتور. به‌صورت صفر رندر نمی‌شود.", de: "audit_signals ist leer — es wird auf Live-Entscheidungen der Engine gewartet. Nicht als Nullen dargestellt.", es: "audit_signals está vacío: se esperan decisiones en vivo del motor; no se muestra como ceros.", ar: "حقل audit_signals فارغ — بانتظار قرارات المحرك الحية، ولا يُعرض كأصفار." },
  "dash.preds.more": { fa: "+{o} ردیف قدیمی‌تر در اسنپ‌شات ({l} ردیف اول نمایش داده شده)", de: "+{o} ältere Zeilen im Snapshot (die ersten {l} angezeigt)", es: "+{o} filas antiguas en la instantánea (se muestran las primeras {l})", ar: "+{o} صف أقدم في اللقطة (تُعرض أول {l})" },
  "dash.preds.noprobs": { fa: "احتمال‌های softmax برای این ردیف ارسال نشده — ناشناخته نمایش داده می‌شود، نه صفر", de: "Softmax-Werte für diese Zeile nicht gesendet — als unbekannt statt als Nullen dargestellt", es: "no se enviaron probabilidades softmax para esta fila: se muestra como desconocido, no como ceros", ar: "لم تُرسل احتمالات softmax لهذا الصف — يُعرض مجهولًا لا أصفارًا" },
  "dash.preds.note": { fa: "دقت نتیجه در این بسته ارزیابی نمی‌شود (دفتر قدیمی 0/0 واقعی داشت) — نوار دقت جعلی رسم نمی‌شود.", de: "Die Trefferquote wird in diesem Payload nicht ausgewertet (das alte Ledger führte wörtlich 0/0) — kein falscher Genauigkeitsbalken.", es: "La precisión de resultados no se evalúa en esta carga (el libro antiguo llevaba 0/0 literal): no se dibuja una barra de precisión falsa.", ar: "لا يُقيَّم دقة النتائج في هذه الحمولة (كان السجل القديم يحمل 0/0 حرفيًا) — لا يُرسم شريط دقة وهمي." },
  "dash.preds.order_note": { fa: "ترتیب بک‌اند، جدیدترین اول", de: "Backend-Reihenfolge, neueste zuerst", es: "orden del backend, lo más reciente primero", ar: "ترتيب الخادم، الأحدث أولًا" },
  "dash.preds.subtitle": { fa: "ردیف‌های واقعی audit_signals از دفتر — هرگز جعل‌شده نیست", de: "echte audit_signals-Zeilen aus dem Ledger — nie gefälscht", es: "filas reales de audit_signals del libro — nunca fabricadas", ar: "سجلات audit_signals الحقيقية من السجل — ليست مُختلقة" },
  "dash.preds.title": { fa: "تصمیم‌های اخیر مدل ({n})", de: "Letzte Modellentscheidungen ({n})", es: "Decisiones recientes del modelo ({n})", ar: "قرارات النموذج الأخيرة ({n})" },
  "dash.radar.awaiting": { fa: "در انتظار اسنپ‌شات رادار…", de: "Warte auf Radar-Snapshot…", es: "esperando la instantánea del radar…", ar: "بانتظار لقطة الرادار…" },
  "dash.radar.best_setup": { fa: "بهترین ستاپ", de: "Bestes Setup", es: "Mejor setup", ar: "أفضل إعداد" },
  "dash.radar.candidates": { fa: "کاندیداها", de: "Kandidaten", es: "Candidatos", ar: "المرشحون" },
  "dash.radar.compatible": { fa: "راهبردهای سازگار", de: "Kompatible Strategien", es: "Estrategias compatibles", ar: "استراتيجيات متوافقة" },
  "dash.radar.decision": { fa: "تصمیم", de: "Entscheidung", es: "Decisión", ar: "القرار" },
  "dash.radar.direction": { fa: "جهت", de: "Richtung", es: "Dirección", ar: "الاتجاه" },
  "dash.radar.news_state": { fa: "وضعیت اخبار", de: "News-Zustand", es: "Estado de noticias", ar: "حالة الأخبار" },
  "dash.radar.no_data": { fa: "داده رادار موجود نیست", de: "KEINE RADARDATEN", es: "SIN DATOS DE RADAR", ar: "لا بيانات رادار" },
  "dash.radar.no_snapshot": { fa: "اسنپ‌شات راداری نیست", de: "kein Radar-Snapshot", es: "sin instantánea del radar", ar: "لا لقطة رادار" },
  "dash.radar.quality": { fa: "کیفیت", de: "Qualität", es: "Calidad", ar: "الجودة" },
  "dash.radar.ranked": { fa: "ستاپ‌های رتبه‌بندی‌شده (ترتیب بک‌اند)", de: "Rangierte Setups (Backend-Reihenfolge)", es: "Setups ordenados (orden del backend)", ar: "إعدادات مرتّبة (ترتيب الخادم)" },
  "dash.radar.regime": { fa: "رژیم بازار", de: "Marktregime", es: "Régimen de mercado", ar: "نظام السوق" },
  "dash.radar.strategies_aria": { fa: "راهبردهای سازگار", de: "kompatible Strategien", es: "estrategias compatibles", ar: "الاستراتيجيات المتوافقة" },
  "dash.radar.subtitle": { fa: "اطلاعات ستاپ از بک‌اند — عیناً رسم می‌شود، اینجا هرگز دوباره محاسبه نمی‌شود", de: "Setup-Intelligenz vom Backend — wörtlich dargestellt, hier nie neu berechnet", es: "inteligencia de setups del backend: se muestra literalmente y nunca se recalcula aquí", ar: "معلومات الإعدادات من الخادم — تُعرض حرفيًا ولا تُعاد حسابها هنا" },
  "dash.radar.title": { fa: "رادار بازار", de: "Markt-Radar", es: "Radar de mercado", ar: "رادار السوق" },
  "dash.radar.updated": { fa: "{time} · {sec} ثانیه پیش", de: "{time} · vor {sec}s", es: "{time} · hace {sec}s", ar: "{time} · قبل {sec} ثانية" },
  "dash.radar.updated_label": { fa: "به‌روزرسانی", de: "Aktualisiert", es: "Actualizado", ar: "محدَّث" },
};
