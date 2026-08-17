"""Quantify current analysis calibration on the live DB."""

import sys
from pathlib import Path

sys.path.insert(0, "src")

from nexus_scalp.news.database import NewsDatabase

db = NewsDatabase(Path("artifacts/news.db"))
arts = db.list_articles(limit=100, include_duplicates=False)
print(f"articles: {len(arts)}")

rel_count = 0
imp_zero = 0
dir_nonneutral = 0
for a in arts:
    an = db.get_analysis(a["article_id"])
    if not an:
        continue
    rel = float(an.get("relevance_to_xauusd", 0.0) or 0.0)
    imp = float(an.get("importance_score", 0.0) or 0.0)
    direction = str(an.get("direction", "NEUTRAL"))
    if rel > 0:
        rel_count += 1
    if imp <= 0.02:
        imp_zero += 1
    if direction != "NEUTRAL":
        dir_nonneutral += 1

print(f"with analysis: {sum(1 for a in arts if db.get_analysis(a['article_id']))}")
print(f"XAUUSD relevance > 0: {rel_count}")
print(f"importance ~0: {imp_zero}")
print(f"non-NEUTRAL direction: {dir_nonneutral}")

print("\n--- top by relevance ---")
rows = []
for a in arts:
    an = db.get_analysis(a["article_id"])
    if an:
        rows.append((float(an.get("relevance_to_xauusd", 0) or 0), a["title"][:60]))
for rel, t in sorted(rows, reverse=True)[:6]:
    print(f"  {rel:.2f}  {t}")

print("\n--- gold-driver articles with ZERO relevance ---")
for a in arts:
    t = (a["title"] + " " + (a.get("summary") or "")).upper()
    an = db.get_analysis(a["article_id"])
    rel = float(an.get("relevance_to_xauusd", 0) or 0) if an else -1
    if rel == 0 and any(
        k in t for k in ("DOLLAR", "YIELD", "FED", "CPI", "OIL", "IRAN", "TREASURY", "INFLATION")
    ):
        print(f"  {a['article_id'][:10]} rel=0  {a['title'][:65]}")
