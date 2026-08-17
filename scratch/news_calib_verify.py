"""Verify calibration: re-analyze real headlines with the upgraded local analyzer."""
import sys
from datetime import UTC, datetime

sys.path.insert(0, "src")

from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.models import NewsArticle

NEWS = [
    "US stock indices closed lower on the day. Declines are led by the S&P/Dow",
    "US 30-year yields rise to the highest since 2007",
    "Canada July CPI 3.0% y/y vs +2.9% expected",
    "USD/JPY rises to the highest level this month",
    "Crude oil futures settle at $84.50",
    "Gold buyers are taking more control with a break above the 100 day moving average",
    "Iran has set a deadline of \"a few weeks\" for full implementation of the MOU",
    "US stock markets grind lower as oil prices climb following report Iran seized a tanker",
    "Kickstart the NA session for August 17: USD starts the week on the defensive.",
    "European stocks close mostly lower as yields rise",
]

an = LocalNewsAnalyzer()
now = datetime.now(UTC)
for i, t in enumerate(NEWS):
    art = NewsArticle(
        article_id=f"cal_{i}",
        article_hash=f"h{i}",
        title=t,
        summary="",
        body="",
        source_id="forexlive",
        source_name="ForexLive",
        published_at=now,
    )
    ents = an.extract_entities(art)
    tops = an.classify_topics(art, ents)
    rel = an.xauusd_relevance(art, ents, tops)
    direction, impacts = an.directional_hypothesis(art, ents, tops)
    imp_score, imp = an.importance_score(art, tops, 0.5)
    d = direction.value if direction else "-"
    xau = next((i for i in impacts if i.asset == "XAUUSD"), None)
    print(f"rel={rel:.2f} dir={d:8s} imp={imp_score:.2f} | {t[:62]}")