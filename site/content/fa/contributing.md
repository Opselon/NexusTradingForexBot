---
title: مشارکت
description: نحوه مشارکت — راه‌اندازی، مدل مالکیت، گیت‌های کیفیت، گردش‌کار مستندات.
lang: fa
translation-status: complete
source-revision: en:contributing@2026-09-02
---

این مخزن توسط عامل‌های هماهنگ تحت یک قرارداد مهندسی سخت‌گیرانه توسعه می‌یابد؛ مشارکت‌کنندگان انسانی از همان نظم بهره می‌برند.

## راه‌اندازی

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q
```

## نظم تغییر

- **اول حافظه مهندسی را بخوانید**: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) (نقشه معماری)، [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md)، [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
- **قبل از کدنویسی ادعا کنید** — یک ردیف به `agents/taskboard.md` اضافه کنید.
- **استفاده مجدد > گسترش > بازآرایی > ایجاد**؛ فایل‌های مسیر داغ قرارداد-قفل‌اند.
- **کامیت‌ها**: `<Name>: <summary>` با بدنه ساختاریافته؛ هر کامیت یک گام منسجم.
- **گیت کیفیت**: `./beforePush.sh -SkipPush` (ruff · format · mypy · مجموعه بحرانی pytest · گیت استقرار فارنزیک).

## گردش‌کار مستندات

مستندات متعلق به نقش Nexus-Docs است؛ تغییرات مستندات هرگز کد زمان‌اجر را لمس نمی‌کنند.

```bash
python scripts/docs/check_docs.py            # دکتر: لینک · لنگر · ترجمه · اسرار · انحراف · بیلد
python scripts/docs/check_translations.py    # پوشش هر زبان از بازرسی واقعی
python scripts/docs/build_site.py            # ساخت سایت Pages در site/public
```

انگلیسی زبان مبدأ است؛ ترجمه‌ها `translation-status` و `source-revision` دارند. نام محصولات/ماژول‌ها ترجمه نمی‌شوند؛ اصطلاحات رسمی در `site/terminology/terms.csv` است.

## افزودن زبان جدید

1. پوشه `site/content/<lang>/` را بسازید (صفحات انگلیسی را کپی کنید).
2. زبان را در `scripts/docs/site_config.py` ثبت کنید (`dir: rtl` برای راست‌به‌چپ — چیدمان خودکار می‌چرخد و کد LTR می‌ماند).
3. صفحات را با `lang` + `translation-status` + `source-revision` علامت بزنید.
4. اصطلاحات رسمی را به `site/terminology/terms.csv` اضافه کنید.
5. دکتر و ممیزی ترجمه را اجرا کنید؛ یک PR فقط-مستندات باز کنید.
