import sqlite3
import time

# setup database
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE behavior_detections (ticket TEXT, pattern TEXT)")
conn.execute("CREATE TABLE behavior_analysis (ticket TEXT, evidence_coverage REAL)")

# insert dummy data
for i in range(10000):
    conn.execute("INSERT INTO behavior_detections VALUES (?, ?)", (f"TICKET_{i}", "PATTERN_X"))
    conn.execute("INSERT INTO behavior_analysis VALUES (?, ?)", (f"TICKET_{i}", 0.95))

tickets = [f"TICKET_{i}" for i in range(5000)]


def method_chunk_loop():
    rows = []
    analysis = []
    for start in range(0, len(tickets), 400):
        chunk = tickets[start : start + 400]
        placeholders = ",".join("?" * len(chunk))

        # detections
        for r in conn.execute(
            f"SELECT ticket, pattern FROM behavior_detections WHERE ticket IN ({placeholders})",
            tuple(chunk),
        ):
            rows.append(r)

        # analysis
        for r in conn.execute(
            f"SELECT ticket, evidence_coverage FROM behavior_analysis WHERE ticket IN ({placeholders})",
            tuple(chunk),
        ):
            analysis.append(r)
    return len(rows), len(analysis)


def method_temp_table():
    rows = []
    analysis = []
    # Using temp table
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_tickets (ticket TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM tmp_tickets")
    conn.executemany("INSERT INTO tmp_tickets (ticket) VALUES (?)", [(t,) for t in tickets])

    for r in conn.execute(
        "SELECT behavior_detections.ticket, pattern FROM behavior_detections JOIN tmp_tickets ON behavior_detections.ticket = tmp_tickets.ticket"
    ):
        rows.append(r)

    for r in conn.execute(
        "SELECT behavior_analysis.ticket, evidence_coverage FROM behavior_analysis JOIN tmp_tickets ON behavior_analysis.ticket = tmp_tickets.ticket"
    ):
        analysis.append(r)

    # conn.execute("DROP TABLE tmp_tickets")
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
