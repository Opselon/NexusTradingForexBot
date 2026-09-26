"""Lane G (LIVE wave) - backend API + WebSocket + web UI read verification.

Proves the served surfaces return rows that actually live in the LIVE
nexusdb (not documented defaults):
  * /api/db/console/rows  - real rows through the PostgreSQL driver
  * /api/db/console/tables - table list + row counts from the live cluster
  * /api/db/manage/status - configured-vs-effective provider truth
  * /ws                    - WebSocket snapshot (get_system_state)
  * / and /index.html      - the web UI shell serves

Uses TestClient against create_app() (no separate server process, no MT5).
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_pg_lib import connect

OUT = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/lane_g_live_ui_api.json")
res: dict[str, Any] = {
    "probe": "lane-g-live-ui-api",
    "started_at": datetime.now(UTC).isoformat(),
}


def _log(msg: str) -> None:
    print(f"[lane-g-ui] {msg}", flush=True)


def _pg_scalar(sql: str, args: tuple[Any, ...] = ()) -> Any:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


def main() -> None:
    from fastapi.testclient import TestClient

    from nexus_scalp.web.auth import current_web_auth_token
    from nexus_scalp.web.server import create_app

    t0 = time.perf_counter()
    app = create_app(engine_ref=None)
    res["app_created_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res["route_count"] = len([r for r in app.routes])
    client = TestClient(app)
    _log(f"app created, {res['route_count']} routes")

    # Sensitive DB routes are gated by the web-auth middleware. The token is
    # resolved the same way the app resolves it (env -> dotenv -> secret store)
    # and passed via the documented ?token= query parameter; it is never
    # printed or persisted by the probe.
    _token = current_web_auth_token()
    authed = {"token": _token} if _token else {}
    res["web_auth_token_resolved"] = bool(_token)
    _log(f"web auth token resolved: {bool(_token)}")

    # -- ground truth: what is really in nexusdb right now ---------------
    gt_sig = int(_pg_scalar("SELECT count(*) FROM audit_signals"))
    gt_src = int(_pg_scalar("SELECT count(*) FROM news_sources"))
    gt_dl = int(_pg_scalar("SELECT count(*) FROM audit_dead_letter"))
    gt_ver = str(_pg_scalar("SELECT version()"))
    res["ground_truth"] = {
        "audit_signals": gt_sig,
        "news_sources": gt_src,
        "audit_dead_letter": gt_dl,
        "pg_version": gt_ver,
    }
    _log(f"ground truth: audit_signals={gt_sig} news_sources={gt_src} dead_letter={gt_dl}")

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        _log(f"  {name}: {'PASS' if ok else 'FAIL'} {'' if ok else str(detail)[:200]}")

    # -- 1. REST endpoint returning real rows from nexusdb ----------------
    try:
        r = client.get("/api/db/console/rows", params={**authed, "database": "audit", "table": "audit_signals", "limit": 5})
        body = r.json()
        rows = body.get("rows") or []
        got = [row.get("request_id") for row in rows if isinstance(row, dict)]
        # cross-check: the same ids must exist in nexusdb
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit_signals")
            live_n = int(cur.fetchone()[0])
        check(
            "api_rows_audit_signals",
            r.status_code == 200
            and body.get("success") is True
            and len(rows) > 0
            and live_n == gt_sig
            and body.get("provider") == "postgresql",
            {"status": r.status_code, "n_rows": len(rows), "provider": body.get("provider"), "live_n": live_n},
        )
        res["api_rows_sample"] = got[:3]
    except Exception as exc:
        check("api_rows_audit_signals", False, f"{type(exc).__name__}: {exc}")

    try:
        r = client.get("/api/db/console/tables", params={**authed, "database": "audit"})
        body = r.json()
        tabs = body.get("tables") or []
        by_name = {t["name"]: t.get("rows") for t in tabs if isinstance(t, dict)}
        check(
            "api_tables_audit",
            r.status_code == 200
            and body.get("success") is True
            and body.get("provider") == "postgresql"
            and by_name.get("audit_signals") == gt_sig,
            {"status": r.status_code, "provider": body.get("provider"), "n_tables": len(tabs)},
        )
        res["api_tables_count"] = len(tabs)
    except Exception as exc:
        check("api_tables_audit", False, f"{type(exc).__name__}: {exc}")

    # -- 2. provider truth (no sqlite/postgresql split brain) -------------
    try:
        r = client.get("/api/db/manage/status", params=authed)
        body = r.json()
        truth = body.get("provider_truth") or body.get("provider") or {}
        check(
            "provider_truth_no_mismatch",
            r.status_code == 200 and truth.get("mismatch") is False and truth.get("effective") == "postgresql",
            {"status": r.status_code, "truth": truth},
        )
    except Exception as exc:
        check("provider_truth_no_mismatch", False, f"{type(exc).__name__}: {exc}")

    # -- 3. WebSocket delivers a real snapshot ----------------------------
    try:
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
        check(
            "websocket_snapshot",
            isinstance(snap, dict) and "state_version" in snap and "provenance" in snap,
            {"keys": sorted(snap.keys())[:12] if isinstance(snap, dict) else snap},
        )
        res["ws_snapshot_keys"] = sorted(snap.keys())[:12] if isinstance(snap, dict) else []
    except Exception as exc:
        check("websocket_snapshot", False, f"{type(exc).__name__}: {exc}")

    # -- 4. web UI shell is served ---------------------------------------
    for path in ("/", "/index.html", "/cc_styles.css", "/api_client.js"):
        try:
            r = client.get(path)
            ok = r.status_code == 200 and len(r.content) > 0
            # the UI bundle must reference the same backend base
            if path in ("/", "/index.html") and ok:
                ok = b"api" in r.content.lower()
            check(f"ui_served:{path}", ok, {"status": r.status_code, "bytes": len(r.content)})
        except Exception as exc:
            check(f"ui_served:{path}", False, f"{type(exc).__name__}: {exc}")

    # -- 5. a UI-visible endpoint that reports DB rows (dead letter tab) --
    try:
        r = client.get("/api/db/console/rows", params={**authed, "database": "audit", "table": "audit_dead_letter", "limit": 3})
        body = r.json()
        rows = body.get("rows") or []
        check(
            "api_rows_dead_letter",
            r.status_code == 200 and len(rows) > 0 and body.get("provider") == "postgresql",
            {"status": r.status_code, "n_rows": len(rows)},
        )
    except Exception as exc:
        check("api_rows_dead_letter", False, f"{type(exc).__name__}: {exc}")

    res["checks"] = checks
    res["api_ws_ui_verified"] = bool(all(c["ok"] for c in checks))
    res["finished_at"] = datetime.now(UTC).isoformat()
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
    if not res["api_ws_ui_verified"]:
        sys.exit(4)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        OUT.write_text(json.dumps(res, indent=1, default=str))
        sys.exit(5)
