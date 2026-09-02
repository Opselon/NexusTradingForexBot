---
title: المساهمة
description: كيف تساهم — التهيئة، نموذج الملكية، بوابات الجودة، سير عمل الوثائق.
lang: ar
translation-status: complete
source-revision: en:contributing@2026-09-02
---

يطور هذا المستودع وكلاء منسقون بموجب عقد هندسي صارم؛ ويستفيد المساهمون البشر من الانضباط نفسه.

## التهيئة

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q
```

## انضباط التغيير

- **اقرأ الذاكرة الهندسية أولاً**: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) (خريطة البنية)، [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md)، [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
- **اطلب قبل أن تبرمج** — أضف صفًا إلى `agents/taskboard.md`.
- **إعادة الاستخدام > التوسيع > إعادة الهيكلة > الإنشاء**؛ ملفات المسار الساخن مقفلة بالاتفاق.
- **الكوميتات**: `<الاسم>: <ملخص>` مع متن منظم؛ خطوة متماسكة واحدة لكل كوميت.
- **بوابة الجودة**: `./beforePush.sh -SkipPush` (ruff · format · mypy · مجموعة pytest الحرجة · بوابة النشر الجنائية).

## سير عمل الوثائق

الوثائق مملوكة لدور Nexus-Docs؛ تغييرات الوثائق لا تلمس كود وقت التشغيل أبدًا.

```bash
python scripts/docs/check_docs.py            # الفاحص: الروابط · المراسٍ · الترجمات · الأسرار · الانحراف · البناء
python scripts/docs/check_translations.py    # تغطية كل لغة من فحص فعلي
python scripts/docs/build_site.py            # بناء موقع Pages في site/public
```

الإنجليزية هي اللغة المصدر؛ والترجمات تحمل `translation-status` و`source-revision`. أسماء المنتجات/الوحدات لا تُترجم؛ والمصطلحات المعيارية في `site/terminology/terms.csv`.

## إضافة لغة جديدة

1. أنشئ `site/content/<lang>/` (انسخ مجموعة الصفحات الإنجليزية).
2. سجل اللغة في `scripts/docs/site_config.py` (`dir: rtl` لليمين-إلى-اليسار — ينعكس التخطيط تلقائيًا ويبقى الكود LTR).
3. ضع على الصفحات `lang` + `translation-status` + `source-revision`.
4. أضف المصطلحات المعيارية إلى `site/terminology/terms.csv`.
5. شغّل الفاحص وتدقيق الترجمة؛ وافتح PR للوثائق فقط.
