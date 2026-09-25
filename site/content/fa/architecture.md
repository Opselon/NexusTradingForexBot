---
title: معماری
description: معماری شش‌ضلعی و رویدادمحور — لایه‌ها، مرزها و مسیر تیک تا تصمیم.
lang: fa
translation-status: complete
source-revision: en:architecture@2026-09-02
---

NSE به صورت **شش‌ضلعی (Ports-and-Adapters) و رویدادمحور** است: پلتفرم‌های بروکر، مدل‌ها و آداپتورهای شبکه پشت قراردادهای پورت (`IMT5Port`، `IGatewayPort`) قرار دارند؛ دامنه هرگز نمی‌داند کدام بروکر متصل است.

```text
داده بازار (MT5 / ZMQ / Paper)
  → ویژگی‌های علّی — پایه ۵۰D (scalp_v1) · سرهم‌بندی governed ۷۰D
  → اعتبارسنج استنتاج — هش اسکیما · بُعد اسکیلر · بازه‌ها (رد پرسر و صدا)
  → ScalpNet (TCN + خودتوجهی) — ۴ لوجیت → گیت اطمینان
  → نگهبان رژیم → ماتریس سیاست SMC (~۳۰ قانون)
  → موتور ریسک (حجم‌دهی کِلی · مارجین ≤۲۰٪ · سقف‌های رده · HARD_MAX_LOTS=10)
  → OrderManager (روتر ۶۰-سناریویی · ۱۱ وضعیت پوزیشن · جمع‌سازی اتمی)
  → آداپتور IMT5Port → MT5 / Paper / Shadow (صفر اختیار سفارش)
  → حسابداری (SQLite WAL غیرقابل‌تغییر) → تجربه و کالبدشکافی
  → مشاهده‌پذیری (لاگ · رخداد · فارنزیک) + مرکز کنترل (REST/SSE/WS)
```

## لایه‌ها

| لایه | مسئولیت | مرز سخت |
| :--- | :--- | :--- |
| دامنه (Domain) | قراردادهای Pydantic منجمد | هرگز جهش نمی‌یابند |
| پورت‌ها/آداپتورها | IPC وین32 متاتریدر · ZMQ · Paper · مخزن SQLite WAL | بدون DB همگام روی مسیر داغ |
| ویژگی‌ها | موتور علّی ۵۰D/۷۰D · رجیستری و هش اسکیما | ترتیب، قرارداد است |
| مدل‌ها/آموزش | ScalpNet · Walk-Forward پاکسازی‌شده · برچسب سه‌سد | بدون آینده‌نگری |
| سیگنال‌ها | سیاست · ماتریس قانون SMC | بدون اختیار سفارش |
| ریسک / اجرا | مرزهای حجم‌دهی · دیسپچ ۶۰-سناریویی | کلمپ‌های مرجع |
| حلقه هوش | تجربه · پژوهش · سایه · حکمرانی | صفر اختیار سفارش |
| مشاهده‌پذیری | لاگ · رخداد · فارنزیک | فقط تشخیصی |

## خواص کلیدی

- **INV-001** — صفر DB/آموزش/شبکه همگام روی مسیر تیک.
- **INV-002** — اجزای یادگیری فیزیکی نمی‌توانند سفارش ثبت کنند.
- **INV-008** — بدون آینده‌نگری؛ نقدینگی کاملاً علّی.
- **INV-011** — حقیقت بروکر هنگام تطبیق مواضع می‌چربد.
- بسته‌های مدل در هر اتصال از **گیت بارگذاری ۱۰-گانه** عبور می‌کنند؛ ناهمخوانی بُعد/هش پرسر و با کد تشخیصی بلاک می‌شود، هرگز بی‌صدا نیست.

عمق بیشتر: [overview.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/overview.md)
· [data-flow.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/data-flow.md)
· [model-pipeline.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/model-pipeline.md)
· نقشه مرجع داخلی: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md).
