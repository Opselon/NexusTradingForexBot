"""Wire replay routes into web/server.py (CHG-0043 part 6)."""
from pathlib import Path

p = Path('src/nexus_scalp/web/server.py')
with open(p, 'r', encoding='utf-8', newline='') as fh:
    s = fh.read()
EOL = '\r\n' if '\r\n' in s else '\n'

old = '''    from nexus_scalp.web.operator_routes import register_operator_routes

    register_operator_routes(app, get_system_state, _err, _log_err, serialize_enums)'''.replace('\n', EOL)
new = '''    from nexus_scalp.web.operator_routes import register_operator_routes

    register_operator_routes(app, get_system_state, _err, _log_err, serialize_enums)

    # REPLAY-ON-CHART session routes (CHG-0043, REPLAY_API v1): the chart's
    # operator surface for the REAL historical decision pipeline. Records
    # loader serves the LOCAL dataset cache only (no network, no MT5 on this
    # path). Bounded registry lives on app.state.
    from nexus_scalp.web.replay_routes import (
        ReplaySessionRegistry,
        register_replay_routes,
    )

    def _replay_records_loader(contract: Any, config: Any) -> list[dict[str, Any]]:
        """Local-dataset loader for replay sessions (offline, deterministic).

        Serves M1 bars from data/raw/XAUUSD_M1.parquet for the contract
        window. No network, no MT5 acquisition, no future fabrication: the
        window slice IS the causal boundary contract.
        """
        import polars as _pl

        m1 = Path("data/raw/XAUUSD_M1.parquet")
        if not m1.exists():
            raise FileNotFoundError(f"local M1 dataset missing: {m1}")
        df = _pl.read_parquet(m1)
        if df.is_empty():
            return []
        if df["time_utc"].dtype == _pl.String:
            df = df.with_columns(_pl.col("time_utc").str.to_datetime(time_zone="UTC"))
        start = contract.start_time
        end = contract.end_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        win = df.filter(
            (_pl.col("time_utc") >= start) & (_pl.col("time_utc") <= end)
        ).sort("time_utc")
        out: list[dict[str, Any]] = []
        for r in win.iter_rows(named=True):
            ts = r["time_utc"]
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
            out.append(
                {
                    "kind": "BAR",
                    "timestamp": ts,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "tick_volume": int(r["tick_volume"]),
                    "spread": float(r["spread"]),
                    "symbol": contract.symbol,
                    "timeframe": contract.timeframe,
                }
            )
        return out

    app.state.replay_sessions = ReplaySessionRegistry()
    register_replay_routes(app, app.state.replay_sessions, _replay_records_loader, _err)'''.replace('\n', EOL)

assert s.count(old) == 1, 'operator routes anchor not found'
s = s.replace(old, new, 1)
with open(p, 'wb') as fh:
    fh.write(s.encode('utf-8'))
print('server wiring added')
