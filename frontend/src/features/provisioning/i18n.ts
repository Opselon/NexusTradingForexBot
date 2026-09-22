/**
 * i18n messages — scope: features/provisioning
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "provisioning.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("provisioning.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  // ---- header -------------------------------------------------------------
  "provisioning.panel.title": { fa: "آماده‌سازی مدل برای اجرای نخست", de: "Erstlauf-Modellvorbereitung", es: "Preparación de modelo en el primer arranque", ar: "تحضير النموذج عند أول تشغيل" },
  "provisioning.panel.intro": { fa: "پیش از اجرای موتور یک مدل قابل سرویس آماده کنید. بررسی محیط فقط کاوشی است — نصب یک اقدام صریح و با رضایت شماست. همزمان فقط یک آموزش محلی اجرا می‌شود.", de: "Bereiten Sie vor dem Start der Engine ein servierbares Modell vor. Der Umgebungscheck ist nur Discovery — das Installieren ist eine ausdrückliche, bestätigte Aktion. Lokale Trainingsläufe laufen nur einzeln.", es: "Prepare un modelo servible antes de que arranque el motor. La comprobación del entorno es solo de descubrimiento; instalar es una acción explícita y consentida. Solo puede haber una ejecución local de entrenamiento cada vez.", ar: "حضّر نموذجًا قابلًا للخدمة قبل تشغيل المحرّك. فحص البيئة للاكتشاف فقط — والتثبيت إجراء صريح وموافق عليه. تدريب محلي واحد في كل مرة." },
  "provisioning.badge.ready": { fa: "محیط آماده است", de: "ENV BEREIT", es: "ENTORNO LISTO", ar: "البيئة جاهزة" },
  "provisioning.badge.not_ready": { fa: "محیط آماده نیست", de: "ENV NICHT BEREIT", es: "ENTORNO NO LISTO", ar: "البيئة غير جاهزة" },
  "provisioning.badge.unknown": { fa: "محیط —", de: "ENV —", es: "ENTORNO —", ar: "البيئة —" },
  "provisioning.banner.recommended": { fa: "اقدام پیشنهادی بعدی:", de: "Empfohlene nächste Aktion:", es: "Siguiente acción recomendada:", ar: "الإجراء التالي الموصى به:" },
  // ---- environment card ---------------------------------------------------
  "provisioning.env.title": { fa: "محیط آموزش", de: "Trainingsumgebung", es: "entorno de entrenamiento", ar: "بيئة التدريب" },
  "provisioning.env.backend_label": { fa: "بک‌اند:", de: "Backend:", es: "Backend:", ar: "الواجهة الخلفية:" },
  "provisioning.env.backend_discovery": { fa: "— فقط کاوش است و هیچ‌چیز با این بررسی نصب نمی‌شود.", de: "— nur Discovery; durch diesen Check wird nichts installiert.", es: "— solo descubrimiento; esta comprobación no instala nada.", ar: "— الاكتشاف فقط؛ لا يثبّت هذا الفحص أي شيء." },
  "provisioning.env.not_detected": { fa: "شناسایی نشد", de: "nicht erkannt", es: "no detectado", ar: "لم يُكتشف" },
  "provisioning.env.no_gpu": { fa: "GPU لازم نیست", de: "keine GPU erforderlich", es: "no se requiere GPU", ar: "لا حاجة لوحدة GPU" },
  "provisioning.env.can_train": { fa: "هم‌اکنون قابل آموزش است", de: "jetzt trainierbar", es: "se puede entrenar ahora", ar: "يمكن التدريب الآن" },
  "provisioning.env.blocked": { fa: "مسدود است", de: "blockiert", es: "bloqueado", ar: "محظور" },
  "provisioning.env.resolving": { fa: "در حال شناسایی محیط…", de: "Umgebung wird aufgelöst…", es: "Resolviendo el entorno…", ar: "جارٍ تحديد البيئة…" },
  "provisioning.env.blocking": { fa: "بررسی‌های بازدارنده", de: "Blockierende Prüfungen", es: "comprobaciones bloqueantes", ar: "فحوصات مانعة" },
  "provisioning.env.install_title": { fa: "رضایت صریح: نصب نسخ پین‌شده پایتورچ برای بک‌اند انتخابی", de: "Ausdrückliche Zustimmung: installiert gepinnte Python-/Torch-Versionen für das gewählte Backend", es: "consentimiento explícito: instala versiones fijadas de Python/Torch para el backend elegido", ar: "موافقة صريحة: تثبيت إصدارات Python/Torch المثبّتة للواجهة الخلفية المختارة" },
  "provisioning.env.installing": { fa: "در حال نصب…", de: "Wird installiert…", es: "Instalando…", ar: "جارٍ التثبيت…" },
  "provisioning.env.install": { fa: "نصب پشته آموزشی", de: "Trainings-Stack installieren", es: "instalar el stack de entrenamiento", ar: "تثبيت حزمة التدريب" },
  "provisioning.env.install_hint": { fa: "رضایت صریح (نسخه‌های پین‌شده). آموزش هرگز از اینجا خودکار شروع نمی‌شود.", de: "Ausdrückliche Zustimmung (gepinnte Varianten). Training startet hier nie automatisch.", es: "consentimiento explícito (variantes fijadas). El entrenamiento nunca se inicia solo desde aquí.", ar: "موافقة صريحة (إصدارات مثبّتة). لا يبدأ التدريب تلقائيًا من هنا أبدًا." },
  // ---- official model card ------------------------------------------------
  "provisioning.official.title": { fa: "مدل رسمی", de: "Offizielles Modell", es: "modelo oficial", ar: "النموذج الرسمي" },
  "provisioning.official.intro": { fa: "بسته مدل منتشرشده را دانلود و راستی‌آزمایی کنید. تا پاسس‌شدن همه بررسی‌ها چیزی نصب نمی‌شود.", de: "Laden Sie das veröffentlichte Modellpaket herunter und prüfen Sie es. Nichts wird installiert, bis jede Prüfung besteht.", es: "Descargue y verifique el paquete del modelo publicado. No se instala nada hasta que todas las comprobaciones pasen.", ar: "حمّل حزمة النموذج المنشورة وتحقّق منها. لا يُثبَّت أي شيء قبل نجاح جميع الفحوص." },
  "provisioning.official.empty": { fa: "هنوز وضعیتی برای slot نیست.", de: "Noch kein Slot-Zustand.", es: "Aún no hay estado de la ranura.", ar: "لا يوجد حالة للفتحة بعد." },
  "provisioning.official.empty_hint": { fa: "اقدام پیشنهادی پس از مشخص‌شدن در سربرگ نمایش داده می‌شود.", de: "Die empfohlene Aktion erscheint im Header, sobald sie bekannt ist.", es: "La acción recomendada aparece en la cabecera una vez conocida.", ar: "يظهر الإجراء الموصى به في الترويسة بمجرد معرفته." },
  "provisioning.official.downloading": { fa: "در حال دانلود…", de: "Wird heruntergeladen…", es: "Descargando…", ar: "جارٍ التنزيل…" },
  "provisioning.official.download": { fa: "دانلود و راستی‌آزمایی مدل رسمی", de: "Offizielles Modell laden & prüfen", es: "descargar y verificar el modelo oficial", ar: "تنزيل النموذج الرسمي والتحقق منه" },
  // ---- train card ---------------------------------------------------------
  "provisioning.train.title": { fa: "اجرای آموزش محلی", de: "Lokaler Trainingslauf", es: "ejecución local de entrenamiento", ar: "تشغيل تدريب محلي" },
  "provisioning.train.intro": { fa: "مدلی از یک فایل واردشده یا تاریخچه قرض‌گرفته از بروکر آموزش دهید. اعتبارسنجی سمت سرور است؛ منبع بروکر به تعداد صریح کندل نیاز دارد.", de: "Trainieren Sie ein Modell aus einer importierten Datei oder aus beim Broker geliehener Historie. Die Validierung erfolgt serverseitig; die Broker-Quelle erfordert eine ausdrückliche Kerzenanzahl.", es: "Entrene un modelo a partir de un archivo importado o del histórico tomado del bróker. La validación es servidor; la fuente bróker requiere un número explícito de velas.", ar: "درّب نموذجًا من ملف مستورد أو من سجل مستعار من الوسيط. التحقق على الخادم؛ ومصدر الوسيط يتطلب عدد شموع صريح." },
  "provisioning.train.source_file": { fa: "فایل واردشده", de: "Datei importieren", es: "archivo importado", ar: "ملف مستورد" },
  "provisioning.train.source_broker": { fa: "تاریخچه بروکر", de: "Broker-Historie", es: "histórico del bróker", ar: "سجل الوسيط" },
  "provisioning.train.file_label": { fa: "فایل داده (CSV/Parquet) — باید داخل یک ریشه مجاز ورود باشد", de: "Datendatei (CSV/Parquet) — muss in einem erlaubten Import-Root liegen", es: "archivo de datos (CSV/Parquet) — debe estar dentro de una raíz de importación permitida", ar: "ملف البيانات (CSV/Parquet) — يجب أن يكون داخل جذر استيراد مسموح" },
  "provisioning.train.file_ph": { fa: "مثلاً data/XAUUSD_M1.csv", de: "z. B. data/XAUUSD_M1.csv", es: "p. ej. data/XAUUSD_M1.csv", ar: "مثل data/XAUUSD_M1.csv" },
  "provisioning.train.allowed_roots": { fa: "ریشه‌های مجاز:", de: "Erlaubte Roots:", es: "raíces permitidas:", ar: "جذور الاستيراد المسموحة:" },
  "provisioning.train.candles_label": { fa: "کندل‌ها (N کندل اخیر — دنباله زمانی)", de: "Kerzen (die letzten N Bars — chronologische Folge)", es: "velas (las N barras más recientes — cola cronológica)", ar: "الشموع (آخر N شمعة — تسلسل زمني)" },
  "provisioning.train.folds": { fa: "تاخوردگی‌ها", de: "Folds", es: "pliegues", ar: "الطيات" },
  "provisioning.train.epochs": { fa: "اپوک‌ها", de: "Epochen", es: "épocas", ar: "الدورات" },
  "provisioning.train.consent": { fa: "اگر محیط آماده نیست، آن را آماده کن (رضایت صریح)", de: "Umgebung bei Bedarf vorbereiten (ausdrückliche Zustimmung)", es: "preparar el entorno si no está listo (consentimiento explícito)", ar: "تحضير البيئة إن لم تكن جاهزة (موافقة صريحة)" },
  "provisioning.train.training": { fa: "در حال آموزش…", de: "Training läuft…", es: "Entrenando…", ar: "جارٍ التدريب…" },
  "provisioning.train.start": { fa: "شروع اجرای آموزش", de: "Trainingslauf starten", es: "iniciar la ejecución de entrenamiento", ar: "بدء تشغيل التدريب" },
  "provisioning.train.cancel": { fa: "لغو اجرا", de: "Lauf abbrechen", es: "cancelar la ejecución", ar: "إلغاء التشغيل" },
  "provisioning.train.progress": { fa: "پیشرفت", de: "Fortschritt", es: "progreso", ar: "التقدم" },
  "provisioning.train.result": { fa: "نتیجه اجرا", de: "Ergebnis des Laufs", es: "resultado de la ejecución", ar: "نتيجة التشغيل" },
  // ---- checklist words ----------------------------------------------------
  "provisioning.check.ok": { fa: "درست", de: "OK", es: "correcto", ar: "سليم" },
  "provisioning.check.fail": { fa: "خطا", de: "FEHLER", es: "FALLO", ar: "فشل" },
  // ---- transient notices --------------------------------------------------
  "provisioning.notice.run_cancelled": { fa: "اجرا لغو شد — در مرز اپوک مشاهده شد.", de: "Lauf abgebrochen — an der Epoch-Grenze beobachtet.", es: "Ejecución cancelada; se observó en la frontera de época.", ar: "أُلغي التشغيل — لُوحظ عند حدود الدورة." },
  "provisioning.notice.run_finished": { fa: "اجرا پایان یافت.", de: "Lauf abgeschlossen.", es: "Ejecución finalizada.", ar: "انتهى التشغيل." },
  "provisioning.notice.install_done": { fa: "نصب محیط پایان یافت.", de: "Installation der Umgebung abgeschlossen.", es: "Instalación del entorno finalizada.", ar: "انتهى تثبيت البيئة." },
  "provisioning.notice.official_ready": { fa: "مدل رسمی نصب شد و قابل سرویس است.", de: "Offizielles Modell installiert und servierbar.", es: "Modelo oficial instalado y listo para servir.", ar: "ثُبّت النموذج الرسمي وأصبح قابلًا للخدمة." },
  "provisioning.notice.official_downloaded": { fa: "مدل رسمی دانلود شد؛ وضعیت slot را ببینید.", de: "Offizielles Modell heruntergeladen; Slot-Zustand siehe Header.", es: "Modelo oficial descargado; consulte el estado de la ranura.", ar: "تم تنزيل النموذج الرسمي؛ راجع حالة الفتحة." },
  "provisioning.notice.train_started": { fa: "اجرای آموزش آغاز شد.", de: "Trainingslauf gestartet.", es: "Ejecución de entrenamiento iniciada.", ar: "بدأ تشغيل التدريب." },
  "provisioning.notice.cancel_requested": { fa: "لغو درخواست شد — در مرز اپوک بعدی مشاهده می‌شود.", de: "Abbruch angefordert — wird an der nächsten Epoch-Grenze beobachtet.", es: "Cancelación solicitada; se observará en la siguiente frontera de época.", ar: "طُلب الإلغاء — سيُلاحظ عند حدود الدورة التالية." },
  "provisioning.notice.no_run": { fa: "اجرای فعالی برای لغو نیست.", de: "Kein aktiver Lauf zum Abbrechen.", es: "No hay ninguna ejecución activa que cancelar.", ar: "لا يوجد تشغيل نشط للإلغاء." },
};
