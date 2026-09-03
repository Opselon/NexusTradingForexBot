"""Ground-truth scan of Phase 14 broker-aware symbols across key files."""

from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
FILES = [
    "src/nexus_scalp/ports/mt5_port.py",
    "src/nexus_scalp/adapters/mt5/mt5_adapter.py",
    "src/nexus_scalp/adapters/mt5/providers.py",
    "src/nexus_scalp/adapters/mt5/diagnostics.py",
    "src/nexus_scalp/adapters/mt5/remote_gateway.py",
    "src/nexus_scalp/adapters/paper/paper_adapter.py",
    "src/nexus_scalp/execution/order_manager.py",
    "src/nexus_scalp/application/live_engine.py",
    "src/nexus_scalp/domain/models.py",
]
SYMBOLS = [
    "AccountSnapshot",
    "PositionSnapshot",
    "OrderSnapshot",
    "HistoryOrderSnapshot",
    "DealSnapshot",
    "BrokerTickSnapshot",
    "get_all_positions",
    "MT5ConnectionState",
    "MT5CallDiagnostic",
    "run_mt5_call",
    "error_state",
    "get_account_snapshot",
    "get_pending_orders_snapshot",
    "get_history_orders",
    "get_history_deals",
    "get_rate_history",
]

for f in FILES:
    path = REPO / f
    if not path.exists():
        print(f"{f}: MISSING")
        continue
    src = path.read_text(encoding="utf-8", errors="replace")
    counts = {s: src.count(s) for s in SYMBOLS if s in src}
    print(f"== {f} ({len(src.splitlines())} lines)")
    for s, n in counts.items():
        if n:
            print(f"   {s}: {n}")
