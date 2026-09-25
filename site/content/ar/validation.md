---
title: التحقق
description: سلسلة البوابات — ماذا يعني «متحقق» ولماذا تُنشر الرفضات.
lang: ar
translation-status: complete
source-revision: en:validation@2026-09-02
---

التحقق هو سبب وجود المنصة. الفشل في أي بوابة نهائي لذلك المرشح — بلا متوسطات، بلا استثناءات.

## سلسلة البوابات

| الطبقة | البوابات |
| :--- | :--- |
| البيانات | بوابات الجودة · البصمة · المصدر |
| الخصائص | بصمة المخطط · البُعد · الحدود · الترتيب |
| النموذج | بوابة تحميل بـ10 بوابات · المعايرة · الحد الأدنى للأدلة |
| البحث | Walk-Forward منقّى/محجور · حدود OOS · المتانة |
| الحوكمة | تحقق 14-بوابة · معاملة الترقية |
| وقت التشغيل | بوابة النشر الجنائية قبل كل إصدار |
| الإصدار | SHA-256 · بيانات · SBOM · تحقق لاحق للنشر |

## حدود OOS الدنيا (دورة حياة النموذج)

macro-F1 ≥ 0.34 · الدقة المتوازنة ≥ 0.34 · ECE ≤ 0.15 · أدلة ≥ 100 صف.

## البوابة تعضّ — برهان علني

مرشح السيولة 70D — أكثر سلاسل البحث هندسةً في المستودع — رُفض بواسطة هذه السلسلة نفسها (OOS NOT_ELIGIBLE) بعد أن جاءت نتائج Walk-Forward ومعايير الظل سلبية على بيانات حقيقية. بقي العقد الحي على 50D. البوابة التي لا ترفض شيئًا مجرد زينة؛ هذه لديها رفض علني في سجلها.

للتعمق: [out-of-sample.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/out-of-sample.md)
· [validation.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/validation.md)
· [reproducibility.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/reproducibility.md).
