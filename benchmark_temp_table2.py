import time
import sqlite3

# setup database
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE anomaly_events (ticket TEXT, anomaly_type TEXT)")
conn.execute("CREATE TABLE behavior_analysis (ticket TEXT, evidence_coverage REAL)")

# insert dummy data
for i in range(10000):
    conn.execute("INSERT INTO anomaly_events VALUES (?, ?)", (f"TICKET_{i}", "TYPE_X"))
    conn.execute("INSERT INTO behavior_analysis VALUES (?, ?)", (f"TICKET_{i}", 0.95))

tickets = [f"TICKET_{i}" for i in range(5000)]

def method_chunk_loop():
    rows = []
    analysis = []
    for start in range(0, len(tickets), 500):
        chunk = tickets[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)

        for r in conn.execute(f"SELECT anomaly_type FROM anomaly_events WHERE ticket IN ({placeholders})", tuple(chunk)):
            rows.append(r)

        for r in conn.execute(f"SELECT evidence_coverage FROM behavior_analysis WHERE ticket IN ({placeholders})", tuple(chunk)):
            analysis.append(r)
    return len(rows), len(analysis)

def method_temp_table():
    rows = []
    analysis = []
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _tmp_rpt_tickets (ticket TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _tmp_rpt_tickets")
    conn.executemany("INSERT INTO _tmp_rpt_tickets (ticket) VALUES (?)", [(t,) for t in tickets])

    for r in conn.execute("SELECT anomaly_type FROM anomaly_events e JOIN _tmp_rpt_tickets t ON e.ticket = t.ticket"):
        rows.append(r)

    for r in conn.execute("SELECT evidence_coverage FROM behavior_analysis b JOIN _tmp_rpt_tickets t ON b.ticket = t.ticket"):
        analysis.append(r)

    return len(rows), len(analysis)

t0 = time.time()
for _ in range(100):
    method_chunk_loop()
t1 = time.time()
print(f"Chunk loop: {t1 - t0:.4f}s")

t0 = time.time()
for _ in range(100):
    method_temp_table()
t1 = time.time()
print(f"Temp table: {t1 - t0:.4f}s")
