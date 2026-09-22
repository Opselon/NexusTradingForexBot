/**
 * i18n messages — scope: features/model-studio
 * OWNER: lane for this scope (see CONTRACT.md) (i18n wave). DATA-ONLY file: no logic, only the type import.
 *
 * Format (enforced by tests/js/frontend_i18n_parity.test.js):
 *   "<key>": { fa: "...", de: "...", es: "...", ar: "..." },
 * - Key namespace: "model-studio.<area>.<name>" (lowercase, dot-separated).
 * - en is identity: the English source string lives at the call site —
 *   t("model-studio.x.y", "English source") — do NOT add `en` entries here.
 * - Registered centrally by src/lib/i18nMessages.ts (pre-wired; never edit
 *   that file). Components never import this file; they call t() instead.
 */
import type { FeatureMessages } from "@/lib/i18n";

export const MESSAGES: FeatureMessages = {
  // ---- ModelStudioHeader -------------------------------------------------
  "model-studio.header.title": { fa: "استودیوی مدل عصبی", de: "Neuronales Modell-Studio", es: "Estudio neural de modelos", ar: "استوديو النموذج العصبي" },
  "model-studio.header.dim_badge": { fa: "{d}D تنسور", de: "{d}D-Tensor", es: "{d}D tensor", ar: "{d}D تنسور" },
  "model-studio.header.subtitle": { fa: "استنتاج عمیق یادگیری تعاملی، تفسیرپذیری شیب‌گرا، مونتاژ زندهٔ تنسور 70D و اجرای آموزش پایتورچ.", de: "Interaktive Deep-Learning-Inferenz, erklärbare Gradient-Saliency, Live-Montage der 70D-Tensoren und PyTorch-Trainingssteuerung.", es: "Inferencia interactiva de aprendizaje profundo, explicabilidad por saliencia de gradientes, ensamblaje en vivo del tensor 70D y despacho de entrenamiento con PyTorch.", ar: "استدلال التعلم العميق التفاعلي، وتفسير ابرزية التدرجات، وتركيب التنسور 70D الحي، وتشغيل التدريب عبر PyTorch." },
  "model-studio.header.refresh_title": { fa: "تازه‌سازی مدل فعال، مخزن و شاخص‌های کلی", de: "Aktives Modell, Registry und Kennzahlen aktualisieren", es: "Actualizar el modelo activo, el registro y las métricas generales", ar: "تحديث النموذج النشط والسجل ومقاييس النظرة العامة" },
  "model-studio.header.active_champion": { fa: "قهرمان فعال حافظهٔ اجرا", de: "Aktiver Champion zur Laufzeit", es: "Campeón activo en ejecución", ar: "البطل النشط أثناء التشغيل" },
  "model-studio.header.no_champion": { fa: "قهرمان فعالی در حافظه نیست", de: "Kein aktiver Champion im Speicher", es: "Ningún campeón activo en memoria", ar: "لا يوجد بطل نشط في الذاكرة" },
  "model-studio.header.stage_champion": { fa: "قهرمان", de: "Champion", es: "Campeón", ar: "بطل" },
  "model-studio.header.ft_on": { fa: "FT: روشن", de: "FT: Ein", es: "FT: activado", ar: "FT: مفعّل" },
  "model-studio.header.ft_off": { fa: "FT: خاموش", de: "FT: Aus", es: "FT: desactivado", ar: "FT: معطّل" },
  "model-studio.header.scaler_attached": { fa: "مقیاس‌دهنده: متصل", de: "Skalierer: angebunden", es: "Escalador: conectado", ar: "المُقاسِم: موصول" },
  "model-studio.header.scaler_standby": { fa: "مقیاس‌دهنده: آماده‌باش", de: "Skalierer: Bereitschaft", es: "Escalador: en espera", ar: "المُقاسِم: في انتظار" },
  "model-studio.header.weights_sha": { fa: "هش SHA256 وزن‌ها", de: "Gewichte SHA256", es: "SHA256 de los pesos", ar: "SHA256 للأوزان" },
  "model-studio.header.engine_inferences": { fa: "استنتاج‌های موتور", de: "Inferenzen der Engine", es: "Inferencias del motor", ar: "استدلالات المحرك" },
  "model-studio.header.architecture": { fa: "معماری", de: "Architektur", es: "Arquitectura", ar: "البنية" },
  "model-studio.header.layer_spec": { fa: "مشخصات لایه", de: "Schicht-Spezifikation", es: "Especificación de capas", ar: "مواصفات الطبقات" },
  "model-studio.header.model_source": { fa: "منبع مدل", de: "Modellquelle", es: "Origen del modelo", ar: "مصدر النموذج" },
  "model-studio.header.memory_state": { fa: "وضعیت حافظه", de: "Speicherzustand", es: "Estado en memoria", ar: "حالة الذاكرة" },
  "model-studio.header.parameters": { fa: "پارامترها", de: "Parameter", es: "Parámetros", ar: "المعاملات" },
  "model-studio.header.trainable": { fa: "قابل آموزش: {n}", de: "Trainierbar: {n}", es: "Entrenables: {n}", ar: "قابل للتدريب: {n}" },
  "model-studio.header.artifact_integrity": { fa: "یکپارچگی خروجی‌ها", de: "Integrität der Artefakte", es: "Integridad de los artefactos", ar: "سلامة المخرجات" },
  "model-studio.header.execution_device": { fa: "دستگاه اجرا", de: "Ausführungsgerät", es: "Dispositivo de ejecución", ar: "جهاز التنفيذ" },
  "model-studio.header.torch_runtime": { fa: "محیط اجرای پایتورچ", de: "PyTorch-Laufzeitumgebung", es: "Entorno de ejecución de PyTorch", ar: "بيئة تشغيل PyTorch" },
  "model-studio.header.scaler_status": { fa: "وضعیت مقیاس‌دهنده", de: "Status des Skalierers", es: "Estado del escalador", ar: "حالة المُقاسِم" },
  "model-studio.header.clamped_cols": { fa: "محدودشده: {n} ستون", de: "Begrenzt: {n} Spalten", es: "Limitadas: {n} columnas", ar: "مقيّدة: {n} عمود" },
  // __APPEND_BELOW__
};
