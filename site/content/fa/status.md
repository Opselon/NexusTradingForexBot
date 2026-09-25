---
title: وضعیت پروژه
description: وضعیت صادقانه و درجه‌بندی‌شده با شواهد برای هر قابلیت اصلی — بدون بازاریابی.
lang: fa
translation-status: complete
source-revision: en:status@2026-09-02
---

برچسب‌های وضعیت **بر اساس شواهد درجه‌بندی می‌شوند**: «گواهی‌شده» نیازمند پذیرش رسمی médico-forensic با مصنوعات بازتولیدپذیر است؛ «پیاده‌سازی‌شده» یعنی در کد با پوشش رگرسیون؛ «آزمایشی» یعنی صریحاً خارج از مسیر زنده.

| بُعد | وضعیت | شواهد |
| :--- | :--- | :--- |
| نسخه | **v9.0.6** (منبع واحد `pyproject.toml`) | خط لوله انتشار هر مصنوع را مهر می‌زند |
| انتشار | منتشرشده (v9.0.0 تا v9.0.6؛ SHA-256، مانیفست، SBOM) | GitHub Releases |
| اجرای زنده (MT5) | کارا، با فیوز هویت حساب | مجموعه BUG-142 |
| قرارداد زنده ۵۰D (`scalp_v1`) | **فعال** | `features/schema.py` |
| قرارداد ۷۰D (`scalp_v3`) | فقط کاندیدا | `features/schema_contract.py` |
| شواهد ۷۰D | **منفی / نامشخص (OOS NOT_ELIGIBLE)** | گزارش‌های TASK-05/TASK-09 |
| حلقه پژوهش | سرتاسری و کارا | مجموعه‌های research/ |
| CI | ruff · mypy · مجموعه بحرانی ~۷۷۹ تست · CodeQL · Trivy | workflowها |
| حکمرانی مدل | RESTORED_CANDIDATE — تصمیم اپراتور در انتظار | MODEL_GOVERNANCE v2 |

## قابلیت‌های شاخص

| قابلیت | وضعیت |
| :--- | :--- |
| ویژگی‌های علّی ۵۰D · کلمپ‌های ریسک · زمان‌اجر سایه · گیت OOS · نصب‌کننده | ✅ گواهی‌شده |
| کارخانه مدل · روتر اجرا · حسابداری · رخدادها · مرکز کنترل | 🟢 پیاده‌سازی‌شده |
| سری ۷۰D · گیت اخبار · زمانی · MSLIE | 🟡 آزمایشی / 🔵 پژوهشی |
| چند-بروکر | 📌 برنامه‌ریزی‌شده |

ماتریس کامل: [capabilities.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/capabilities.md)
· محدودیت‌های شناخته‌شده: [status.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/status.md)
· دفتر رگبارهای صادقانه: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
