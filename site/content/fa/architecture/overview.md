---
title: نمای معماری
description: معماری شش‌ضلعی (ports-and-adapters) و رویدادمحور موتور اسکالپ نکسوس.
lang: fa
translation-status: complete
source-revision: en:architecture/overview@9.0.6
---

# نمای معماری

NSE **شش‌ضلعی (ports-and-adapters) و رویدادمحور** است. پلتفرم بروکر، مدل‌ها و
آداپتورهای شبکه پشت قراردادهای port ایزوله شده‌اند (`IMT5Port`, `IGatewayPort`)؛
دامنه (domain) نمی‌داند کدام بروکر — یا اصلاً بروکری — متصل است.

```text
داده بازار (MT5 / paper / gateway)
        │
        ▼
ویژگی‌های علّی ── 50D scalp_v1 (قرارداد زنده) ── 70D scalp_v3 (پژوهشی)
        │
        ▼
اعتبارسنج استنتاج ──► ScalpNet (TCN + خودتوجهی، ۴-لاگیتی)
        │
        ▼
طبقه‌بان رژیم ──► ماتریس سیاست SMC ──► موتور ریسک (سایزینگ کلی، کلمپ‌ها)
        │
        ▼
OrderManager (روتر ۶۰-سناریویی · ۱۱ وضعیت پوزیشن · HARD_MAX_LOTS=10)
        │
        ▼
آداپتور IMT5Port ──► بروکر / paper        (اختیار سفارش اینجا تمام می‌شود)
        │
        ├──► دفتر کل حسابداری (SQLite WAL، تغییرناپذیر)
        ├──► هوش تجربه / کالبدشکافی معاملات
        ├──► پژوهش: بک‌تست ← walk-forward ← دروازه OOS ← سایه ← ارتقای اپراتوری
        └──► مشاهده‌پذیری: لاگ‌ها · حوادث · جنگل‌بانی · UI مرکز کنترل
```

## لایه‌ها

| لایه | مسیر | مسئولیت | مرز سخت |
| :--- | :--- | :--- | :--- |
| Domain | `src/nexus_scalp/domain/` | قراردادهای Pydantic تغییرناپذیر | مدل‌های frozen |
| Ports | `ports/` | `IMT5Port`، `IGatewayPort` | تغییر امضا ⇒ همه آداپتورها |
| Adapters | `adapters/` | MT5 Win32 IPC، ZMQ، paper، SQLite WAL | **بدون DB سنکرون روی مسیر داغ** |
| Features | `features/` | مونتاژ 50D پایه → 70D، رجیستری + هش طرحواره | طرحواره = SSoT |
| Models | `models/` | ScalpNet دو-مسیره، سر ۴-لاگیتی | بعد ورود = بعد طرحواره |
| Risk | `risk/` | حجم پویا، کلمپ حاشیه، سقف‌ها | مرجع نهایی مرزها |
| Execution | `execution/` | OrderManager: روتر ۶۰-سناریویی، ۱۱ وضعیت | مرجع نهایی dispatch |
| Accounting | `accounting/` | دفتر کل، PnL، تقویم بازار | تاریخ تغییرناپذیر |
| Application | `application/` | حلقه async و `LiveEngine` | هرگز حلقه رویداد بلاک نشود |
| حلقه هوش | `experience/` · `research/` · `shadow/` · `governance/` | کالبدشکافی، کاندیدا، سایه | **صفر اختیار سفارش** |

## حلقه هوش بسته

معامله زنده ← حسابداری ← تجربه ← کالبدشکافی ← پژوهش ← آموزش کاندیدا ←
مقایسه سایه ← ارتقای اپراتوری. همه‌چیز از دفاتر تغییرناپذیر بازسازی‌پذیر است.

## ادامه

- [نقشه سیستم](/architecture/system-map/) (انگلیسی) · [جریان داده](/architecture/data-flow/) (انگلیسی)
- نقشه داخلی معتبر: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md)
