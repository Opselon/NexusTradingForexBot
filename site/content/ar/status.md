---
title: حالة المشروع
description: حالة صادقة ومدرَّجة بالأدلة لكل قدرة رئيسية — بلا تسويق.
lang: ar
translation-status: complete
source-revision: en:status@2026-09-02
---

تُدرَّج تسميات الحالة **وفق الأدلة**: «مُصادق» يتطلب قبولًا جنائيًا-رسميًا بمخرجات قابلة لإعادة الإنتاج؛ «مُنفَّذ» يعني موجودًا في الكود مع تغطية انحدار؛ «تجريبي» يعني خارج مسار الحي صراحةً.

| البُعد | الحالة | الدليل |
| :--- | :--- | :--- |
| الإصدار | **v9.0.6** (مصدر وحيد `pyproject.toml`) | خط أنابيب الإصدار يختم كل مخرج |
| الإصدارات | منشورة (v9.0.0 → v9.0.6؛ SHA-256، بيان، SBOM) | GitHub Releases |
| التنفيذ الحي (MT5) | يعمل، مع صمام أمان هوية الحساب | مجموعة BUG-142 |
| عقد الخصائص الحي 50D (`scalp_v1`) | **نشط** | `features/schema.py` |
| عقد 70D (`scalp_v3`) | مرشح فقط | `features/schema_contract.py` |
| أدلة 70D | **سالبة / غير حاسمة (OOS NOT_ELIGIBLE)** | تقارير TASK-05/TASK-09 |
| حلقة البحث | تعمل من طرف إلى طرف | مجموعات research/ |
| CI | ruff · mypy · مجموعة حرجة ~779 اختبارًا · CodeQL · Trivy | مسارات العمل |
| حوكمة النماذج | RESTORED_CANDIDATE — قرار المشغّل معلق | MODEL_GOVERNANCE v2 |

## قدرات بارزة

| القدرة | الحالة |
| :--- | :--- |
| خصائص سببية 50D · قيود المخاطر · وقت تشغيل الظل · بوابة OOS · المثبّت | ✅ مُصادق |
| مصنع النماذج · موجّه التنفيذ · المحاسبة · الحوادث · مركز التحكم | 🟢 مُنفَّذ |
| سلسلة 70D · بوابة الأخبار · الزمني · MSLIE | 🟡 تجريبي / 🔵 بحثي |
| تعدد الوسطاء | 📌 مخطط |

المصفوفة الكاملة: [capabilities.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/capabilities.md)
· القيود المعروفة: [status.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/status.md)
· سجل الأخطاء الجنائي العام: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
