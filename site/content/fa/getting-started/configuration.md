---
title: پیکربندی
description: ساختار پیکربندی Nexus Scalp Engine — بوت‌استرپ AppConfig، اسنپ‌شات‌های زمان اجرا، پایگاه داده تنظیمات و قواعد عدم‌ذخیره‌اسرار روی دیسک.
lang: fa
translation-status: complete
source-revision: en:getting-started/configuration@9.0.6
---

# پیکربندی

پیکربندی سه لایه با مرجعیت‌های متفاوت دارد. قاطی‌کردن آن‌ها یک خطای کلاسیک است — پیش از ویرایش دستی YAML این صفحه را بخوانید.

## 1. `AppConfig` (بوت‌استرپ / درون‌ریزی / برون‌بری)

`src/nexus_scalp/configuration/config.py` — قرارداد YAML را بارگذاری می‌کند (`configs/base.yaml` پایه مرجع؛ `configs/live.yaml.example` نمونه با شکل live). فقط برای بوت‌استرپ/درون‌ریزی/برون‌بری استفاده می‌شود.

## 2. پیکربندی زمان اجرا (وضعیت زنده مرجع)

`RuntimeConfiguration` از طریق `RuntimeConfigStore.get_snapshot()` **وضعیت زنده مرجع** است — نسخه‌دار و با بارگذاری مجدد داغ. مصرف‌کنندگان باید از `get_snapshot()` بخوانند، نه از مقادیر کش‌شده سازنده. برچسب‌های دامنه تعیین می‌کنند تغییر چه زمانی اعمال می‌شود (`LIVE_IMMEDIATE` در برابر `NEXT_DECISION`).

## 3. پایگاه داده تنظیمات (اسرار + قصد کاربر)

اعتبارنامه‌ها (مثلاً تلگرام) در پایگاه داده تنظیمات از طریق `settings_service.set_telegram()` ذخیره می‌شوند — **هرگز** در `live.yaml` و هرگز کامیت نمی‌شوند (INV-010). مسیر ذخیره UI از همان سرویس عبور می‌کند.

## کلیدهای مرتبط با ریسک

| کلید | معنا |
| :--- | :--- |
| `risk.max_concurrent_positions` | مرز مواجهه (اجرای اول: `1`) |
| `risk.risk_per_trade_pct` | ورودی کِلی کسری، ریسک هر معامله |
| `risk.max_account_drawdown_pct` | توقف افت سرمایه |
| `liquidity_features_enabled` | فرمانده بلوک نقدینگی 70D (مسیر پژوهش) |

## اعتبارسنجی

```bash
nexus config                # بررسی پیکربندی فعال (اسرار ماسک می‌شوند)
nexus config --validate path/to/config.yaml
```

پیکربندی نامعتبر یا شکست doctor پیش از پرتاب، شروع موتور را مسدود می‌کند.

## داکر

`docker-compose.yml` + `.env.example` قرارداد متغیرهای محیطی کانتینر را تعریف می‌کنند (پیش‌فرض‌های امن، بدون اسرار). برای جزئیات [`docs/docker.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/docker.md) را ببینید.
