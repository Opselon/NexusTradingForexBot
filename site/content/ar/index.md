---
title: Nexus Scalp Engine — التوثيق
description: مركز توثيق محرك نكسوس لتحليل الأصول — منصة بحث وتنفيذ تداول كمية.
lang: ar
translation-status: complete
source-revision: en:index@9.0.6
---

# محرك نكسوس — التوثيق

**منصة تداول كمية قائمة على البحث** — محرك سكالبينغ سداسي (hexagonal)
مدفوع بالأحداث لمنصة MetaTrader 5 (السوق الرئيسي: XAUUSD على M1)، مبني حول
هندسة الخصائص السببية، وحوكمة النماذج القائمة على الملفات (Model Factory)،
وأدوات بحث حتمية، والرصد الجنائي (forensic observability).

> [!IMPORTANT]
> يصف هذا التوثيق **منصة بحث وهندسة**. إنه ليس نصيحة استثمارية ولا وعداً
> بالربح. السكالبينغ بالرفع المالي ينطوي على مخاطر مالية بالغة. راجع
> [حالة المشروع](../project/status.md).

## ابدأ من هنا

| السؤال | الصفحة |
| :--- | :--- |
| ما هو نكسوس ولماذا وُجد؟ | [الرؤية](vision.md) |
| كيف أشغّله؟ | [البداية السريعة](quickstart.md) |
| كيف تعمل البنية؟ | [نظرة البنية](../architecture/overview.md) |
| كيف يُتحقق من البحث؟ | [منهجية البحث](../research/methodology.md) |
| ما الحقيقي وما التجريبي؟ | [الحالة](../project/status.md) |
| إلى أين يتجه المشروع؟ | [خارطة الطريق](../project/roadmap.md) |
| كيف أساهم؟ | [دليل المساهمة](../contributing/contribution-guide.md) |

## البداية السريعة

```bash
nexus doctor          # تشخيص كامل (قراءة فقط)
nexus start           # وضع PAPER افتراضياً — لا يصبح LIVE أبداً دون تأكيد صريح
nexus start --mode shadow   # بيانات سوق حقيقية، صفر سلطة أوامر
```

## اللغات

| 🇬🇧 English | 🇮🇷 فارسی | 🇪🇸 Español | 🇸🇦 العربية | 🇩🇪 Deutsch |
| :---: | :---: | :---: | :---: | :---: |
| [كامل](/index.md) | [نمای کلی](/fa/index.md) | [vista previa](/es/index.md) | **الحالية** | [Übersicht](/de/index.md) |

بقية الصفحات متاحة بالإنجليزية؛ يُدقَّق تغطية الترجمة عبر
`scripts/docs/check_translations.py`.
