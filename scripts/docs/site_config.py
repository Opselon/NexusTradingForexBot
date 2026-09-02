"""Site configuration — single source for the multilingual GitHub Pages build.

Nexus-Docs owns this file. The build (build_site.py), the doctor
(check_docs.py) and the translation audit (check_translations.py) all read it.

Adding a language = add an entry here + create site/content/<lang>/.
"""

SITE_NAME = "Nexus Scalp Engine"
SITE_TAGLINE = {
    "en": "Research-driven quantitative trading platform",
    "fa": "پلتفرم پژوهش‌محور معاملات کمی",
    "es": "Plataforma de trading cuantitativo basada en investigación",
    "ar": "منصة تداول كمي قائمة على البحث",
    "de": "Forschungsgetriebene quantitative Handelsplattform",
}
OWNER = "Opselon"
REPO = "NexusTradingForexBot"
REPO_URL = f"https://github.com/{OWNER}/{REPO}"
PAGES_URL = f"https://{OWNER}.github.io/{REPO}"
SOURCE_LANG = "en"

LANGUAGES = {
    "en": {"name": "English",  "dir": "ltr", "flag": "🇬🇧"},
    "fa": {"name": "فارسی",    "dir": "rtl", "flag": "🇮🇷"},
    "es": {"name": "Español",  "dir": "ltr", "flag": "🇪🇸"},
    "ar": {"name": "العربية",  "dir": "rtl", "flag": "🇸🇦"},
    "de": {"name": "Deutsch",  "dir": "ltr", "flag": "🇩🇪"},
}

# Navigation model — page ids per language tree. The site builder resolves
# these ids to pages in each language; a page missing from a non-source
# language is a translation-coverage finding, not a build failure (the
# language falls back to the English page with a notice).
NAV = [
    ("start",       {"en": "Start here",        "fa": "شروع",            "es": "Inicio",          "ar": "البداية",           "de": "Start"}),
    ("status",      {"en": "Project status",    "fa": "وضعیت پروژه",      "es": "Estado",          "ar": "حالة المشروع",      "de": "Projektstatus"}),
    ("architecture",{"en": "Architecture",      "fa": "معماری",          "es": "Arquitectura",    "ar": "البنية",            "de": "Architektur"}),
    ("research",    {"en": "Research",          "fa": "پژوهش",           "es": "Investigación",   "ar": "البحث",             "de": "Forschung"}),
    ("validation",  {"en": "Validation",        "fa": "اعتبارسنجی",      "es": "Validación",      "ar": "التحقق",            "de": "Validierung"}),
    ("roadmap",     {"en": "Roadmap",           "fa": "نقشه راه",         "es": "Hoja de ruta",    "ar": "خارطة الطريق",      "de": "Roadmap"}),
    ("reference",   {"en": "Reference & FAQ",   "fa": "مرجع و پرسش‌ها",   "es": "Referencia y FAQ","ar": "مرجع والأسئلة",     "de": "Referenz & FAQ"}),
    ("contributing",{"en": "Contributing",      "fa": "مشارکت",          "es": "Contribuir",      "ar": "المساهمة",          "de": "Mitwirken"}),
]
