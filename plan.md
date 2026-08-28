All tests are synchronous. So using `import asyncio; asyncio.run(...)` will perfectly work!

Let's summarize the changes:
1. `src/nexus_scalp/ports/mt5_port.py`
Change `def connect(self) -> bool:` to `async def connect(self) -> bool:`.
2. `src/nexus_scalp/adapters/paper/paper_adapter.py`
Change `def connect(self) -> bool:` to `async def connect(self) -> bool:`.
3. `src/nexus_scalp/adapters/mt5/remote_gateway.py`
Change `def connect(self) -> bool:` to `async def connect(self) -> bool:` and replace `time.sleep(0.5)` with `await asyncio.sleep(0.5)`. Add `import asyncio` if missing.
4. `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
Change `def connect(self) -> bool:` to `async def connect(self) -> bool:` and replace `_time.sleep(backoff_ms / 1000.0)` with `import asyncio; await asyncio.sleep(backoff_ms / 1000.0)`.
5. `src/nexus_scalp/application/live_engine.py`
Line 1446: `mt5_connected = await self.adapter.connect()`
Line 1604: `await self.adapter.connect()`
6. Tests
```bash
sed -i 's/adapter.connect()/import asyncio; asyncio.run(adapter.connect())/g' tests/integration/test_accounting_api.py
sed -i 's/adapter.connect()/import asyncio; asyncio.run(adapter.connect())/g' tests/integration/test_experience_execution_boundary.py
sed -i 's/adapter.connect()/import asyncio; asyncio.run(adapter.connect())/g' tests/integration/test_live_freshness_g29.py
sed -i 's/paper.connect()/import asyncio; asyncio.run(paper.connect())/g' tests/integration/test_live_freshness_g29.py
sed -i 's/adapter.connect()/import asyncio; asyncio.run(adapter.connect())/g' tests/unit/test_mt5_status_endpoint.py
```

Wait, if there are OTHER sync usages of `time.sleep` in `mt5_adapter.py` inside `modify_order`, `close_position`, etc. why aren't we making those async too?
The prompt specifically identifies the reconnect function:
"File: src/nexus_scalp/adapters/mt5/mt5_adapter.py:181"
"Issue: Synchronous sleep in async MT5 reconnect"
This only addresses `connect()`. It's better to stick precisely to the described issue rather than rewriting the entire `DirectMT5Adapter` and `OrderLifecycleManager` to be async.

Let's request plan review!
