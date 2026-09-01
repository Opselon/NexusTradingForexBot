---
title: البداية السريعة
description: أقصر مسار آمن من الاستنساخ إلى تشغيل المحرك — وضع PAPER افتراضياً.
lang: ar
translation-status: complete
source-revision: en:getting-started/quickstart@9.0.6
---

# البداية السريعة

```bash
# 1. التثبيت (المطورون، من المصدر)
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # لينكس: source .venv/bin/activate
pip install -e .[dev]

# 2. فحص النظام
nexus doctor          # تشخيص للقراءة فقط في 19 فئة + إصلاحات مقترحة
nexus health          # READY / DEGRADED / NOT READY

# 3. التشغيل — PAPER افتراضياً، لا LIVE بصمت أبداً
nexus start           # محاكاة، مركز التحكم على http://127.0.0.1:8080

# 4. التقييم على بيانات حقيقية مع صفر سلطة أوامر
nexus start --mode shadow

# 5. الإيقاف
nexus stop            # لوضع --daemon؛ أو Ctrl+C في المقدمة
```

## الأوضاع

| الوضع | البيانات | الأوامر | الاستخدام |
| :--- | :--- | :--- | :--- |
| `paper` (افتراضي) | محاكاة | محاكاة | أول تشغيل، الواجهة، التطوير |
| `shadow` | حقيقية | **لا شيء — صفر سلطة** (`simulated=True`) | تقييم النموذج/الإشارات على سوق حقيقي |
| `live` | حقيقية | حقيقية | **تأكيد تفاعلي صريح مطلوب**؛ تُطبع لوحة المخاطر كاملة |

> [!WARNING]
> يضع هذا المحرك **صفقاتاً حقيقية بأموال حقيقية** في وضع LIVE. المسار
> الموصى به: [حساب تجريبي → SHADOW → LIVE صغير](#الأوضاع).

## التالي

- [نظرة البنية](../architecture/overview.md) · [الحالة](../project/status.md) · [الأسئلة الشائعة](../reference/faq.md)
