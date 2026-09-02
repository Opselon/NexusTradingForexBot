---
title: پژوهش
description: چگونه داده تاریخی به شواهد ابطال‌پذیر تبدیل می‌شود — دیتاست‌ها، بک‌تست، بازپخش، خلاف‌واقع.
lang: fa
translation-status: complete
source-revision: en:research@2026-09-02
---

پژوهش برای تولید **شواهد ابطال‌پذیر** وجود دارد. کاندیدایی که ابطال‌پذیر نباشد (بدون OOS، بدون برابری بازپخش، بدون منشأ) هرگز به مسیر زنده راه نمی‌یابد.

## زنجیره

```text
داده (دیتاست‌های اثرانگشت‌شده) → ویژگی‌ها (قرارداد علّی) → برچسب‌گذاری (سه‌سد)
→ آموزش (بذردار، قطعی) → بک‌تست (آگاه از اصطکاک)
→ Walk-Forward (پاکسازی + تحریم) → گیت OOS (شکست یعنی REJECTED)
→ تست استحکام → خلاف‌واقع → رجیستری → سایه → ترویج اپراتور
```

## اجزا

- **دیتاست‌ها** — تغییرناپذیر، اثرانگشت‌شده، با منشأ؛ دیتاست‌های تیک از سطح آداپتور معتبر دریافت و پس از آن آفلاین‌اند.
- **بک‌تست‌ها** — قطعی؛ اسپرد/اسلیپیج/لاتنسی مدل می‌شوند.
- **بازپخش** — بی‌دقیق (Bit-Exact) نسبت به دیتاست (تست‌های ضد-نشتی)؛ بازپخش جریانی موتور مشترک را روی ساعت منطقی با پرشدن شبیه‌سازی‌شده اجرا می‌کند و فراخوانی `order_send` در آن با تست ممنوع شده است.
- **خلاف‌واقع (CHG-0041)** — قدم زدن تصمیم‌های NO_TRADE با پر شدن فرضی: ۲۰۹۵ تصمیم، ۴۷۶ پوشش‌داده‌شده؛ طبقه CONFIDENCE_GATE فیلتری معتبر proved شد (میانگین R برابر ‎−۰٫۵۰۶)؛ طبقه SUPPORT-margin برای بازبینی سیاست علامت‌گذاری شد.

## منشأ و قطعیت

هر اجرا شناسه دیتاست، هش اسکیما، کامیت گیت و مقادیر مؤثر purge/embargo (رگرسیون BUG-183) را ثبت می‌کند. `NOT_RECORDED` زمانی صادقانه نوشته می‌شود که اطلاعاتی وجود ندارد — هرگز پس‌کاشت نمی‌شود. تست‌های رو به جلو با کپچر منجمد، پس از اجرا انجماد خود را دوباره تأیید می‌کنند.

عمق بیشتر: [methodology.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/methodology.md)
· [datasets.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/datasets.md)
· [replay.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/replay.md)
· [counterfactuals.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/counterfactuals.md).
