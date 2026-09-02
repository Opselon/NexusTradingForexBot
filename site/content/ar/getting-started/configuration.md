---
title: الإعداد
description: بنية إعداد Nexus Scalp Engine — تهيئة AppConfig، لقطات وقت التشغيل، قاعدة بيانات الإعدادات، وقواعد عدم تخزين الأسرار على القرص.
lang: ar
translation-status: complete
source-revision: en:getting-started/configuration@9.0.6
---

# الإعداد

للإعداد ثلاث طبقات بمرجعيات مختلفة. خلطها خطأ كلاسيكي — اقرأ هذه الصفحة قبل تحرير YAML يدويًا.

## 1. `AppConfig` (التهيئة / الاستيراد / التصدير)

`src/nexus_scalp/configuration/config.py` — يحمّل عقد YAML (`configs/base.yaml` هو الأساس المرجعي؛ و`configs/live.yaml.example` مثال بصيغة live). يُستخدم للتهيئة والاستيراد والتصدير فقط.

## 2. إعداد وقت التشغيل (الحالة الحية المرجعية)

`RuntimeConfiguration` عبر `RuntimeConfigStore.get_snapshot()` هي **الحالة الحية المرجعية** — مُسنة الإصدار وقابلة لإعادة التحميل الساخن. على المستهلكين القراءة عبر `get_snapshot()` لا القيم المخبأة من المُنشئ. وتحدد وسوم النطاق متى يسري التغيير (`LIVE_IMMEDIATE` مقابل `NEXT_DECISION`).

## 3. قاعدة بيانات الإعدادات (الأسرار + نية المستخدم)

بيانات الاعتماد (مثل تيليجرام) تُخزَّن في قاعدة بيانات الإعدادات عبر `settings_service.set_telegram()` — **أبدًا** في `live.yaml` ولا تُرفع للمستودع أبدًا (INV-010). ومسار الحفظ في الواجهة يمر عبر الخدمة نفسها.

## مفاتيح المخاطر الرئيسية

| المفتاح | المعنى |
| :--- | :--- |
| `risk.max_concurrent_positions` | حد التعرض (التشغيل الأول: `1`) |
| `risk.risk_per_trade_pct` | مدخل كِيلي الكسري، المخاطرة لكل صفقة |
| `risk.max_account_drawdown_pct` | حد التراجع |
| `liquidity_features_enabled` | متحكم كتلة السيولة 70D (مسار البحث) |

## التحقق

```bash
nexus config                # فحص الإعداد الفعّال (تُحجب الأسرار)
nexus config --validate path/to/config.yaml
```

الإعداد غير الصالح أو فشل فحص doctor المسبق يحجب بدء المحرك.

## دوكر

يحدد `docker-compose.yml` + `.env.example` عقد متغيرات البيئة للحاوية (افتراضيات آمنة بلا أسرار). التفاصيل في [`docs/docker.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/docker.md).
