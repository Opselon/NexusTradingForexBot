"""Calendar worker integration probe: does the NewsEngine surface accept a CalendarWorker?

P0 wiring decision: LiveEngine owns news_engine (NewsEngine). Rather than
touch live_engine.py (foreign P1-seam agent owns it right now), the calendar
worker composes ON TOP of the existing NewsEngine via the news_engine.db and
exposes itself through NewsEngine so consumers use one handle.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.calendar.worker import CalendarWorker, weekly_liveness_report

tmp = Path(tempfile.mkdtemp()) / "news.db"
db = NewsDatabase(str(tmp))
db.seed_sources_and_health = None  # attr does not exist; guard probe
w = CalendarWorker(db)
w.tick()

# weekly report with empty news health (fresh db) + real calendar state
report = weekly_liveness_report(news_db_health=db.list_health(), calendar_worker=w)
print("report sources:", len(report["sources"]), "| dead:", report["dead_count"])
print("calendar leg:", {k: report["calendar"][k] for k in ("calendar_health", "event_count", "future_high_impact_count")})
