import statistics
import time
import urllib.request

TOKEN = "probe-xyz"
GO_URL = "http://127.0.0.1:8092"
PY_URL = "http://127.0.0.1:8091"

ROUTES = [
    # Static & direct (Go direct candidates)
    ("GET", "/api/v1/system/version"),
    ("GET", "/api/v1/system/capabilities"),
    ("GET", "/api/v1/features/contract"),
    ("GET", "/api/v1/features/groups"),
    ("GET", "/api/v1/config/schema"),
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    # System metadata & health (read-only, currently proxied)
    ("GET", "/api/v1/system/health"),
    ("GET", "/api/v1/system/readiness"),
    ("GET", "/api/v1/system/status"),
    ("GET", "/api/v1/system/runtime"),
    ("GET", "/api/v1/system/workers"),
    # High-frequency telemetry / frontend polling
    ("GET", "/api/status"),
    ("GET", "/api/debug/state"),
    ("GET", "/api/mt5/status"),
    ("GET", "/api/v1/runtime/mode"),
    ("GET", "/api/v1/runtime/freshness"),
    ("GET", "/api/v1/risk/summary"),
    ("GET", "/api/v1/risk/status"),
    ("GET", "/api/v1/market/regime"),
    # Domain-heavy (read-only)
    ("GET", "/api/v1/research/status"),
    ("GET", "/api/v1/research/strategies"),
]


def sample(url):
    t0 = time.perf_counter()
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            body = resp.read()
            return (time.perf_counter() - t0) * 1000, resp.status, len(body)
    except Exception as e:
        return (time.perf_counter() - t0) * 1000, 0, str(e)


def p95(xs):
    return statistics.quantiles(xs, n=20)[18] if len(xs) >= 20 else max(xs)


print(
    f"{'Route':<34} | {'Go ms':<7} | {'Py ms':<7} | {'Delta':<7} | {'Go p95':<7} | {'Py p95':<7} | {'GoCode':<7} | {'PyCode':<7} | {'Bytes':<7}"
)
print("-" * 112)

for _method, path in ROUTES:
    go_lat = []
    py_lat = []
    go_codes = []
    py_codes = []
    last_size = 0
    for _ in range(20):
        t_go, c_go, s_go = sample(GO_URL + path)
        t_py, c_py, s_py = sample(PY_URL + path)
        go_lat.append(t_go)
        py_lat.append(t_py)
        go_codes.append(c_go)
        py_codes.append(c_py)
        if isinstance(s_go, int):
            last_size = s_go
        time.sleep(0.01)

    go_mean = statistics.mean(go_lat)
    py_mean = statistics.mean(py_lat)
    delta = go_mean - py_mean
    print(
        f"{path:<34} | {go_mean:6.2f}  | {py_mean:6.2f}  | {delta:+6.2f}  | {p95(go_lat):6.2f}  | {p95(py_lat):6.2f}  | {statistics.mode(go_codes):<7} | {statistics.mode(py_codes):<7} | {last_size:<7}"
    )
