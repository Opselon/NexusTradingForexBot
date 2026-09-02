import sqlite3
from collections import Counter

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\news.db")
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute("""
    SELECT a.published_at, n.direction FROM news_analysis n
    JOIN news_articles a ON a.article_id = n.article_id
    ORDER BY a.published_at DESC LIMIT 100
""")]
print("last 40 analyses:", [(r["published_at"][11:16], r["direction"]) for r in rows[:40]])
total = Counter(r["direction"] for r in rows)
print("last 100 direction counts:", dict(total))
con.close()