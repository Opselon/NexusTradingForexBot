---
title: اعتبارسنجی
description: زنجیره گیت‌ها — «اعتبارسنجی‌شده» یعنی چه و چرا ردشدن‌ها منتشر می‌شوند.
lang: fa
translation-status: complete
source-revision: en:validation@2026-09-02
---

اعتبارسنجی دلیل وجود این پلتفرم است. شکست در هر گیت برای آن کاندیدا پایانی است — بدون میانگین‌گیری، بدون استثنا.

## زنجیره گیت‌ها

| لایه | گیت‌ها |
| :--- | :--- |
| دیتاست | گیت‌های کیفیت · اثرانگشت · منشأ |
| ویژگی‌ها | هش اسکیما · بُعد · بازه · ترتیب |
| مدل | گیت بارگذاری ۱۰-گانه · کالیبراسیون · حداقل شواهد |
| پژوهش | Walk-Forward پاکسازی‌شده/تحریم‌شده · کف‌های OOS · استحکام |
| حکمرانی | راستی‌آزمایی ۱۴-گانه · تراکنش ترویج |
| زمان‌اجر | گیت استقرار فارنزیک قبل از هر انتشار |
| انتشار | SHA-256 · مانیفست · SBOM · راستی‌آزمایی پس از انتشار |

## کف‌های OOS (چرخه حیات مدل)

macro-F1 ≥ ۰٫۳۴ · دقت متوازن ≥ ۰٫۳۴ · ECE ≤ ۰٫۱۵ · شواهد ≥ ۱۰۰ ردیف.

## گیت واقعاً می‌گزد — اثبات عمومی

کاندیدای نقدینگی ۷۰D — پژوهش‌شده‌ترین سری این مخزن — توسط همین زنجیره رد شد (OOS NOT_ELIGIBLE) پس از آنکه Walk-Forward و بنچمارک سایه روی داده واقعی منفی بازگشت. قرارداد زنده روی ۵۰D ماند. گیتی که چیزی را رد نکند تزئین است؛ این یکی یک ردشدن عمومی در کارنامه دارد.

عمق بیشتر: [out-of-sample.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/out-of-sample.md)
· [validation.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/validation.md)
· [reproducibility.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/reproducibility.md).
