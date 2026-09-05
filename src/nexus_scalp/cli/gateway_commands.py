"""``nexus gateway`` — Windows MT5 bridge server (server-side of RemoteMT5GatewayAdapter).

Commands:
  nexus gateway serve  --host 0.0.0.0 --port 8080  (Windows-only, Demo guard)
  nexus gateway status --url http://WINDOWS_IP:8080 (any OS, checks the server)

No client code is affected — this only serves the existing client's wire
contract at POST /api/v1/execute (X-NSE-API-KEY / TIMESTAMP / SIGNATURE,
HMAC-SHA256 over f"{timestamp}." + raw_body).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import typer
from rich.panel import Panel

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import _emit, _error_panel, _success_panel, console
from nexus_scalp.release import exit_codes as xc

gateway_app = typer.Typer(
    name="gateway",
    help="Windows MT5 bridge: serve the existing RemoteMT5GatewayAdapter contract (Demo by default).",
    add_completion=False,
    no_args_is_help=True,
)


@gateway_app.command("serve")
def gateway_serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host (Windows)."),
    port: int = typer.Option(8080, "--port", help="Listen port."),
    api_key: str | None = typer.Option(None, "--api-key", help="Override NSE_GATEWAY_API_KEY."),
    secret: str | None = typer.Option(None, "--secret", help="Override NSE_GATEWAY_SECRET."),
    allow_live: bool = typer.Option(
        False, "--allow-live", help="Allow serving a REAL account (not Demo)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
) -> None:
    """Start the Windows Gateway Server (requires MT5 terminal on Demo).

    The Linux NCA side uses the existing ``--gateway`` client — nothing changes there.
    Point the Linux client at this server via ``NSE_GATEWAY_URL=http://WINDOWS_IP:8080``.
    """
    if sys.platform != "win32":
        msg = "gateway serve runs only on Windows (needs MetaTrader5)."
        if json_mode:
            _emit({"error": msg, "platform": sys.platform, "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(
                _error_panel(
                    "Windows only",
                    msg,
                    hint="Run this on your Windows PC with MT5.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME)

    # Env overrides for this process only (do not mutate caller's env beyond serve lifetime)
    if api_key:
        os.environ["NSE_GATEWAY_API_KEY"] = api_key
    if secret:
        os.environ["NSE_GATEWAY_SECRET"] = secret

    # Demo guard is enforced inside gateway.server._get_adapter, but surface early.
    try:
        from nexus_scalp.gateway.server import app as gateway_asgi
        from nexus_scalp.gateway.server import set_allow_live
    except Exception as exc:
        msg = f"Gateway import failed: {exc}"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(
                _error_panel(
                    "Gateway import failed",
                    msg,
                    hint="Check Python 3.11 + dependencies.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from exc

    set_allow_live(bool(allow_live))

    # Fail fast if MT5 not reachable (clear message before uvicorn binds).
    try:
        from nexus_scalp.gateway.server import _get_adapter  # direct probe

        _ = _get_adapter()
    except Exception as exc:
        msg = str(exc)
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(
                _error_panel(
                    "MT5 not ready",
                    msg,
                    hint="Start MT5 and log into your Demo account, then retry.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from exc

    if json_mode:
        _emit({"status": "starting", "host": host, "port": port, "allow_live": allow_live}, True)

    # Lazy uvicorn import — not needed on Linux at all.
    try:
        import uvicorn
    except Exception as exc:
        msg = f"uvicorn not installed: {exc} (pip install uvicorn fastapi)"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(_error_panel("uvicorn missing", msg, exit_code=xc.EXIT_RUNTIME))
        raise typer.Exit(xc.EXIT_RUNTIME) from exc

    console.print(
        Panel(
            f"[green]Gateway listening[/green] http://{host}:{port}  (Demo guard: {'OFF — REAL allowed' if allow_live else 'ON — Demo only'})\n"
            f"[dim]Client contract: POST /api/v1/execute  (X-NSE-API-KEY/TIMESTAMP/SIGNATURE)[/dim]",
            border_style="green",
            title="NSE Gateway Server",
        )
    )
    uvicorn.run(gateway_asgi, host=host, port=port, log_level="info")


@gateway_app.command("status")
def gateway_status(
    url: str = typer.Option(
        "http://127.0.0.1:8080", "--url", help="Gateway URL (e.g. http://WINDOWS_IP:8080)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
) -> None:
    """Check whether a Gateway Server is reachable (works from Linux)."""
    import hashlib
    import hmac
    import time as _time
    import urllib.error
    import urllib.request

    api_key = os.environ.get("NSE_GATEWAY_API_KEY", "default_local_key")
    secret = os.environ.get("NSE_GATEWAY_SECRET", "default_local_secret")
    # Probe via the same HMAC contract the client uses (PING) + fallback to /health
    base = url.rstrip("/")

    def _hmac_headers(body: bytes) -> dict[str, str]:
        ts = str(int(_time.time()))
        sig = hmac.new(
            secret.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-NSE-API-KEY": api_key,
            "X-NSE-TIMESTAMP": ts,
            "X-NSE-SIGNATURE": sig,
        }

    # 1) Try /health (unauthenticated, server health)
    health: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        health = None

    # 2) Try authenticated PING
    ping_ok = False
    ping_body = json.dumps({"action": "PING", "payload": {}}).encode()
    ping_err: str | None = None
    try:
        req = urllib.request.Request(
            f"{base}/api/v1/execute",
            data=ping_body,
            headers=_hmac_headers(ping_body),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            ping_ok = data.get("status") == "OK"
            if not ping_ok:
                ping_err = str(data.get("message") or data)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            ping_err = f"HTTP {exc.code}: {body[:200]}"
        except Exception:
            ping_err = f"HTTP {exc.code}"
    except Exception as exc:
        ping_err = str(exc)[:300]

    ok = bool(ping_ok)
    result: dict[str, Any] = {
        "url": base,
        "health": health,
        "ping_ok": ping_ok,
        "ping_error": ping_err,
    }

    if json_mode:
        _emit(result, True)

    if ok:
        if not json_mode:
            console.print(
                _success_panel(
                    "Gateway reachable",
                    f"{base} — PING OK\n{json.dumps(health or {}, indent=2)}",
                    border="green",
                )
            )
        return
    # Degraded but health reachable = still useful
    if health is not None:
        if not json_mode:
            console.print(
                Panel(
                    f"[yellow]Gateway health reachable but PING auth failed[/yellow]\n{base}\nhealth: {json.dumps(health, indent=2)}\nerror: {ping_err}\n[dim]Check NSE_GATEWAY_API_KEY / NSE_GATEWAY_SECRET match the server.[/dim]",
                    border_style="yellow",
                    title="Gateway status",
                )
            )
        return

    if not json_mode:
        console.print(
            _error_panel(
                "Gateway unreachable",
                f"{base} — no /health and no PING.\nerror: {ping_err}",
                hint="Is the Windows Gateway running? Check host/port/firewall.",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
    raise typer.Exit(xc.EXIT_RUNTIME)


# Register on the canonical app at import time (same pattern as other sub-apps).
app.add_typer(
    gateway_app,
    name="gateway",
    help="Windows MT5 bridge server for the existing Linux gateway client (Demo by default).",
)
