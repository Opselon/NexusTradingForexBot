---
title: Terminology & Translations
description: Canonical multilingual terminology — how project vocabulary maps across EN/FA/ES/AR/DE.
lang: en
---

# Terminology & Translations

## Untranslatable canon

These stay in English/Latin script in every language (identifiers, contracts,
product names):

`Nexus · ScalpNet · OrderManager · RiskEngine · LiveEngine · IMT5Port ·
AuditRepository · MT5 · ZMQ · SQLite · FastAPI · 50D · 70D · scalp_v1 ·
scalp_v3 · NO_TRADE · BUY · SELL · WAIT · Champion · Challenger · Shadow ·
OOS · walk-forward · O(1) · MFE · MAE`

## Canonical term translations

The authoritative per-language table lives in
`site/terminology/<lang>.json` (consumed by the translation audit). Core
entries:

| EN | FA (فارسی) | ES | AR (العربية) | DE |
| :--- | :--- | :--- | :--- | :--- |
| Feature Vector | بردار ویژگی | Vector de características | متجه السمات | Merkmalsvektor |
| Feature Contract | قرارداد ویژگی | Contrato de características | عقد السمات | Merkmalsvertrag |
| Model Registry | ثبت مدل | Registro de modelos | سجل النماذج | Modellregistrierung |
| Serving Bundle | بسته سرو | Paquete de servicio | حزمة الخدمة | Serving-Paket |
| Replay | بازپخش | Replay | إعادة التشغيل | Replay |
| Shadow Runtime | زمان‌حجاب سایه | Runtime en sombra | وقت التشغيل الظل | Shadow-Laufzeit |
| Walk-Forward | پیش‌رونده | Walk-Forward | التحقق المتحرك | Walk-Forward |
| Out-of-Sample (OOS) | خارج نمونه | Fuera de muestra | خارج العينة | Out-of-Sample |
| Counterfactual | خلاف‌واقع | Contrafactual | مضاد الحقيقة | Kontrafaktisch |
| Regime | رژیم بازار | Régimen | النظام السعري | Marktregime |
| Risk Engine | موتور ریسک | Motor de riesgo | محرك المخاطر | Risikomotor |
| Execution | اجرا | Ejecución | التنفيذ | Ausführung |
| Observability | مشاهده‌پذیری | Observabilidad | قابلية المراقبة | Beobachtbarkeit |
| Provenance | منشأ (Provenance) | Procedencia | المصدر | Herkunft |
| Certification | گواهی‌سازی | Certificación | التصديق | Zertifizierung |
| Runtime Truth | حقیقت زمان اجرا | Verdad del runtime | حقيقة وقت التشغيل | Laufzeit-Wahrheit |

## Consistency rule

The translation audit (`scripts/docs/check_translations.py`) checks glossary
terms for consistency: a term translated differently in two pages of the same
language is a finding. Fix the terminology JSON, not the pages ad hoc.
