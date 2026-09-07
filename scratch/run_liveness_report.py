"""Weekly liveness report against the REAL production news.db (read-only evidence)."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "src")

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.calendar.worker import weekly_liveness_report

db = NewsDatabase("artifacts/news.db")  # read-only usage: list_health only
report = weekly_liveness_report(news_db_health=db.list_health(), calendar_worker=None)
now = datetime.now(UTC).strftime("%Y-%m-%d")
out = Path("artifacts/forensics") / f"news_liveness_report_{now}.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("saved:", out)
for s in report["sources"]:
    age = s["last_success_age_sec"]
    print(f"  {s['source_id']:12s} {s['health']:9s} last_ok_age={age if age is None else round(age/3600, 1)}h failures={s['failure_count']}")
print("dead:", report["dead_sources"])
