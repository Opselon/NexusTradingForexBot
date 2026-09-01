---
title: شروع سریع
description: کوتاه‌ترین مسیر امن از کلون تا اجرای موتور — حالت PAPER پیش‌فرض است.
lang: fa
translation-status: complete
source-revision: en:getting-started/quickstart@9.0.6
---

# شروع سریع

```bash
# ۱. نصب (توسعه‌دهندگان، از سورس)
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # لینوکس: source .venv/bin/activate
pip install -e .[dev]

# ۲. سلامت سیستم
nexus doctor          # عیب‌یابی فقط-خواندنی در ۱۹ دسته + پیشنهاد رفع
nexus health          # READY / DEGRADED / NOT READY

# ۳. اجرا — PAPER پیش‌فرض است، هرگز بی‌صدا LIVE نمی‌شود
nexus start           # شبیه‌سازی کاغذی، داشبورد روی http://127.0.0.1:8080

# ۴. ارزیابی روی داده واقعی، با صفر اختیار سفارش
nexus start --mode shadow

# ۵. توقف
nexus stop            # برای اجرای --daemon؛ در پیش‌زمینه Ctrl+C
```

## معنای حالت‌ها

| حالت | داده | سفارش | کاربرد |
| :--- | :--- | :--- | :--- |
| `paper` (پیش‌فرض) | شبیه‌سازی‌شده | شبیه‌سازی‌شده | اجرای اول، UI، توسعه |
| `shadow` | زنده | **هیچ — صفر اختیار سفارش** (`simulated=True`) | ارزیابی مدل/سیگنال روی بازار واقعی |
| `live` | زنده | واقعی | **تأیید تعاملی صریح لازم است**؛ پنل کامل ریسک چاپ می‌شود |

> [!WARNING]
> این موتور در حالت LIVE با پول واقعی معامله می‌کند. مسیر توصیه‌شده:
> [حساب دمو → SHADOW → LIVE کوچک](#معنای-حالت‌ها). کلمپ‌های سخت موتور از
> استراتژی محافظت می‌کنند — نه از سرمایه شما در برابر نوسان بازار.

## گام بعدی

- [نمای معماری](../architecture/overview.md)
- [وضعیت پروژه](../project/status.md)
- [پرسش‌های متداول](../reference/faq.md)
