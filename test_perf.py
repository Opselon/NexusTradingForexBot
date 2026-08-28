import time
import sys
import logging
sys.path.insert(0, "src")

logging.basicConfig(level=logging.ERROR)

from nexus_scalp.adapters.mt5 import mt5_adapter
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter, MT5ConnectionState

class MockMT5:
    def __init__(self):
        self._fails_left = 3

    def initialize(self, **kwargs):
        if self._fails_left > 0:
            self._fails_left -= 1
            return False
        return True

    def last_error(self):
        return (-10005, "IPC timeout")

class DummyAdapter(DirectMT5Adapter):
    def __init__(self):
        super().__init__(retries=3, timeout=5000, path="")
        self._conn_state = MT5ConnectionState()

mt5_adapter.HAS_NATIVE_MT5 = True
mt5_adapter.mt5 = MockMT5()

def test_connect():
    adapter = DummyAdapter()

    start = time.perf_counter()
    adapter.connect()
    end = time.perf_counter()
    print(f"connect() took {end - start:.6f} seconds")

test_connect()
