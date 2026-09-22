"""Phase 1 / Task 1 — candle vs news temporal overlap audit (read-only).

Answers the Path A vs Path B question: are there >= 20,000 contiguous M1 bars
that overlap the live news_analysis window? Read-only w.r.t. all DBs.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]


def ro_query(db: str, sql: str) -> pl.DataFrame:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=1")
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.execute(sql).description]
    con.close()
    return pl.DataFrame({c: [r[i] for r in rows] for i, c in enumerate(cols)})


def to_naive(s: pl.Series) -> pl.Series:
    t = s.str.to_datetime() if s.dtype == pl.String else s.cast(pl.Datetime)
    return t.dt.replace_time_zone(None) if t.dtype.time_zone is not None else t


def main() -> int:
    ndb = REPO / "artifacts" / "news.db"
    na = ro_query(
        str(ndb),
        "SELECT MIN(published_at) p0, MAX(published_at) p1, COUNT(*) n FROM news_articles",
    ).row(0, named=True)
    nb = ro_query(
        str(ndb),
        "SELECT MIN(analyzed_at) a0, MAX(analyzed_at) a1, COUNT(*) n FROM news_analysis",
    ).row(0, named=True)

    n0 = dt.datetime.fromisoformat(na["p0"]).replace(tzinfo=None)
    n1 = dt.datetime.fromisoformat(na["p1"]).replace(tzinfo=None)
    a0 = dt.datetime.fromisoformat(nb["a0"]).replace(tzinfo=None)
    a1 = dt.datetime.fromisoformat(nb["a1"]).replace(tzinfo=None)
    print("=" * 74)
    print("NEWS WINDOWS (UTC, naive-normalized)")
    print(f"  news_articles  : {n0} .. {n1}  n={na['n']}")
    print(f"  news_analysis  : {a0} .. {a1}  n={nb['n']}   <-- bridge source")

    print("=" * 74)
    print("M1 CANDLE SOURCES")

    report: dict[str, dict] = {}

    # --- raw csv (epoch or ISO depending on file; detect) ---
    raw_path = REPO / "data" / "raw" / "XAUUSD_M1.csv"
    raw = pl.read_csv(raw_path)
    tcol = "time" if "time" in raw.columns else raw.columns[0]
    if raw[tcol].dtype in (pl.Int64, pl.Float64):
        # raw CSV stores epoch seconds. polars 1.43 refuses numpy datetime64[s]
        # via pl.Series, so build the datetime column from python epochs.
        epoch = raw[tcol].to_numpy().astype("int64")
        t = pl.Series(
            "time",
            [dt.datetime.fromtimestamp(int(s), tz=dt.UTC).replace(tzinfo=None) for s in epoch],
        ).cast(pl.Datetime)
        raw_fmt = "epoch_seconds"
    else:
        t = to_naive(raw[tcol])
        raw_fmt = "iso"
    rd = t.diff().dt.total_seconds().drop_nulls().to_numpy()
    r_inside = int((t >= a0).filter(t <= a1).len())
    report["raw_csv"] = {
        "path": str(raw_path),
        "time_format": raw_fmt,
        "rows": int(raw.height),
        "start": str(t.min()),
        "end": str(t.max()),
        "inside_news_analysis_window": r_inside,
        "max_gap_sec": float(rd.max()) if rd.size else 0.0,
        "gaps_gt_900s": int((rd > 900).sum()) if rd.size else 0,
    }

    # --- candle_intel.db ---
    cdb = REPO / "artifacts" / "candle_intel.db"
    c = ro_query(str(cdb), "SELECT bar_ts FROM candles ORDER BY bar_ts")
    tc = to_naive(c["bar_ts"])
    cd = tc.diff().dt.total_seconds().drop_nulls().to_numpy()
    c_inside = int((tc >= a0).filter(tc <= a1).len())
    ov = tc.filter((tc >= a0) & (tc <= a1))
    if ov.len() > 1:
        dvd = ov.diff().dt.total_seconds().drop_nulls().to_numpy()
        ov_maxgap = float(dvd.max())
        ov_gaps = int((dvd > 900).sum())
    else:
        ov_maxgap, ov_gaps = 0.0, 0
    report["candle_intel_db"] = {
        "path": str(cdb),
        "rows": int(c.height),
        "start": str(tc.min()),
        "end": str(tc.max()),
        "inside_news_analysis_window": c_inside,
        "max_gap_sec": float(cd.max()) if cd.size else 0.0,
        "gaps_gt_900s": int((cd > 900).sum()) if cd.size else 0,
        "overlap_max_gap_sec": ov_maxgap,
        "overlap_gaps_gt_900s": ov_gaps,
        "overlap_contiguous_longest_run_bars": _longest_contiguous_run(ov),
    }

    # --- any other M1 parquet/csv caches ---
    extra = []
    for pat in ("**/XAUUSD_M1*.parquet", "**/XAUUSD_M1*.csv"):
        for p in REPO.glob(pat):
            if "scratch" in str(p):
                continue
            try:
                df = pl.read_parquet(p) if p.suffix == ".parquet" else pl.read_csv(p)
                tcol = "time" if "time" in df.columns else "time_utc"
                tt = to_naive(df[tcol])
                extra.append(
                    {
                        "path": str(p.relative_to(REPO)),
                        "rows": int(df.height),
                        "start": str(tt.min()),
                        "end": str(tt.max()),
                    }
                )
            except Exception as exc:
                extra.append({"path": str(p.relative_to(REPO)), "error": str(exc)})
    report["other_m1_caches"] = extra

    print(f"  [raw csv       ] {report['raw_csv']}")
    print(f"  [candle_intel  ] {report['candle_intel_db']}")
    for e in extra:
        print(f"  [other cache   ] {e}")

    # --- verdict ---
    ci = report["candle_intel_db"]
    path_a_feasible = (
        ci["inside_news_analysis_window"] >= 20_000 and ci["overlap_gaps_gt_900s"] == 0
    )
    print("=" * 74)
    print("VERDICT")
    print(
        f"  Path A (>=20k contiguous M1 bars overlapping news_analysis): "
        f"{'FEASIBLE' if path_a_feasible else 'INFEASIBLE'}"
    )
    print(
        f"    bars in news window = {ci['inside_news_analysis_window']} "
        f"(need >= 20000); gaps>15min in overlap = {ci['overlap_gaps_gt_900s']} "
        f"(need 0); longest contiguous run = "
        f"{ci['overlap_contiguous_longest_run_bars']} bars"
    )
    print(
        f"  raw CSV ends {report['raw_csv']['end']} — "
        f"news_analysis starts {a0}; "
        f"disjunction = {(a0 - dt.datetime.fromisoformat(report['raw_csv']['end'])).total_seconds() / 86400:.2f} d"
    )
    chosen = "B" if not path_a_feasible else "A"
    print(f"  => SELECTED PATH {chosen}")

    import json

    (REPO / "artifacts" / "research" / "phase1_overlap_audit.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (REPO / "artifacts" / "research" / "phase1_overlap_audit.json").write_text(
        json.dumps(
            {
                "news_windows": {
                    "news_articles": {"start": str(n0), "end": str(n1), "n": na["n"]},
                    "news_analysis": {"start": str(a0), "end": str(a1), "n": nb["n"]},
                },
                "candle_sources": report,
                "path_a_feasible": path_a_feasible,
                "selected_path": chosen,
            },
            indent=2,
            default=str,
        )
    )
    print("\nwrote artifacts/research/phase1_overlap_audit.json")
    return 0


def _longest_contiguous_run(t: pl.Series, max_gap_sec: float = 900.0) -> int:
    if t.len() < 2:
        return int(t.len())
    d = t.diff().dt.total_seconds().drop_nulls().to_numpy()
    best = run = 1
    for g in d:
        if g <= max_gap_sec:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return int(best)


if __name__ == "__main__":
    raise SystemExit(main())
