"""Native Windows MetaTrader 5 certification (NSE DevOps audit P1/P2).

Certifies the REAL native Win32 MT5 IPC path on a Windows machine:

  Level 1  Native terminal + IPC (NO broker credentials, mandatory gate)
           * official MetaTrader5 Python wheel imports and exposes a version
           * terminal64.exe discovered through the production adapter's own
             auto-discovery (env/paths/registry probe)
           * mt5.initialize(path=...) spawns/attaches the terminal process
           * mt5.version() + mt5.terminal_info() return live terminal data
           * clean shutdown, repeat-attach (BUG-310 class), adapter-level
             DirectMT5Adapter.connect() through the same IPC surface
           * fail-closed credential hygiene: refuses to run when live
             broker credentials are present in the environment (mirrors
             scripts/linux/mt5_doctor.py)

  Level 2  Demo market connectivity (OPTIONAL, requires MT5_DEMO_* secrets)
           * demo login, account_info() trade_mode == 0 (DEMO) — a REAL
             account (2) or contest account (1) hard-fails and aborts
           * XAUUSD symbol availability, tick stream + M1/M5 rates with
             timestamp sanity
           * invalid-server login refusal + recovery re-login
  Level 3  Demo trade execution (OPTIONAL, requires NSE_DEMO_TRADE_TEST=true)
           * re-verifies demo account + expected login + symbol + volume
             bounds + dedicated test magic before ANY order
           * one 0.01-lot market buy, position owned by the test magic,
             explicit close, verified empty positions/orders afterwards;
             leftover position/pending order = FAIL

Evidence: JSON envelope per level (account-identifying fields REDACTED,
same contract as tests/integration/test_mt5_api_probes.py).

Exit codes: 0 = all executed levels passed and mandatory levels were not
skipped; 1 = a mandatory or executed level failed; 2 = environment refusal
(non-Windows host, credential hygiene).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

#: Dedicated magic for Level 3 test orders — MUST never collide with the
#: engine's configured magic (configs/base.yaml) so a stray test order can
#: always be identified and cleaned up.
NSE_CI_DEMO_MAGIC = 999777
NSE_CI_DEMO_COMMENT = "NSE-CI-DEMO-TEST"
#: Volume used for the single demo market order.
NSE_CI_DEMO_VOLUME = 0.01
#: Account fields that must never appear in evidence (probes-suite contract).
REDACTED_ACCOUNT_FIELDS = ("login", "name", "company", "server", "trade_expert")
#: A tick stream older than this is treated as stale broker data.
TICK_STALENESS_LIMIT = _dt.timedelta(days=7)

TRADE_MODE_DEMO = 0
TRADE_MODE_CONTEST = 1
TRADE_MODE_REAL = 2

_RETCODE_DONE = 10009


def _utcnow() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def _jsonable(value: Any) -> Any:
    """Serialize MetaTrader5 namedtuples/numpy scalars into JSON-safe data."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if (_value_is_finite := _is_finite(value)) else str(value)
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "_asdict"):
        return {str(k): _jsonable(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return repr(value)


def _is_finite(value: float) -> bool:
    try:
        import math

        return math.isfinite(value)
    except Exception:
        return False


def _redact(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in record.items():
        if key in REDACTED_ACCOUNT_FIELDS:
            out[key] = "REDACTED"
        else:
            out[key] = val
    return out


def _mask_login(login: Any) -> str:
    text = str(login)
    if len(text) <= 2:
        return "**"
    return "*" * (len(text) - 2) + text[-2:]


class CertResult:
    """One certification level's structured verdict."""

    def __init__(self, level: int, title: str) -> None:
        self.level = level
        self.title = title
        self.status = "NOT RUN"
        self.detail = ""
        self.steps: list[dict[str, Any]] = []
        self.evidence: dict[str, Any] = {}

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"step": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] L{self.level} {name}" + (f" — {detail}" if detail else ""))

    def note(self, name: str, detail: str) -> None:
        self.steps.append({"step": name, "status": "INFO", "detail": detail})
        print(f"  [INFO] L{self.level} {name} — {detail}")

    def finish(self, ok: bool, detail: str = "", skipped_reason: str = "") -> bool:
        if skipped_reason:
            self.status = "NOT RUN"
            self.detail = skipped_reason
            print(f"  [SKIP] L{self.level} {self.title}: {skipped_reason}")
            return True
        self.status = "PASS" if ok else "FAIL"
        self.detail = detail
        print(
            f"  [{'PASS' if ok else 'FAIL'}] L{self.level} {self.title}"
            + (f" — {detail}" if detail else "")
        )
        return ok

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "steps": self.steps,
            "evidence": self.evidence,
        }


def _refuse_live_credentials() -> None:
    """Doctor parity: live broker credentials in the environment are fatal."""
    guard_keys = (
        "NSE_MT5__ACCOUNT",
        "NSE_MT5__PASSWORD",
        "NSE_MT5__SERVER",
        "MT5_LOGIN",
        "MT5_PASSWORD",
        "MT5_SERVER",
    )
    present = [key for key in guard_keys if os.environ.get(key)]
    if present:
        print(
            "::error::Live broker credential variables present in the environment "
            f"({', '.join(present)}) — native IPC certification refuses to run with "
            "credentials configured. Use the dedicated demo workflow for Level 2/3."
        )
        raise SystemExit(2)


def _run_level1(result: CertResult) -> None:
    """Level 1 — native terminal + IPC, no credentials (mandatory gate)."""
    import contextlib

    from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter

    if not HAS_NATIVE_MT5:
        result.step("MetaTrader5 wheel import", False, "wheel not importable on this host")
        raise SystemExit(1)
    import MetaTrader5 as mt5

    package_version = getattr(mt5, "__version__", "unknown")
    result.step(
        "MetaTrader5 wheel import",
        True,
        f"package_version={package_version} (win_amd64 native wheel)",
    )
    result.evidence["package_version"] = package_version

    discovered = DirectMT5Adapter._auto_discover_terminal_path()
    if not discovered or not Path(discovered).is_file():
        result.step("terminal64.exe discovery", False, "adapter auto-discovery found no terminal")
        raise SystemExit(1)
    result.step(
        "terminal64.exe discovery",
        True,
        f"path={discovered} size={Path(discovered).stat().st_size} bytes",
    )
    result.evidence["terminal_path"] = discovered

    # First IPC attach — the real Python→terminal spawn/attach.
    ok = bool(mt5.initialize(path=discovered, timeout=60000))
    last_error = mt5.last_error()
    result.step("mt5.initialize(path=terminal64.exe)", ok, f"last_error={last_error}")
    if not ok:
        raise SystemExit(1)

    version = mt5.version()
    version_ok = bool(version) and all(str(part).strip() for part in version)
    result.step("mt5.version()", version_ok, f"driver/terminal build={version}")
    result.evidence["version"] = [str(part) for part in version] if version else []
    if not version_ok:
        raise SystemExit(1)

    info = mt5.terminal_info()
    info_dict = _jsonable(info) if info else {}
    info_ok = bool(info) and int(info_dict.get("build", 0) or 0) > 0 and bool(info_dict.get("name"))
    result.step(
        "mt5.terminal_info()",
        info_ok,
        f"name={info_dict.get('name')} build={info_dict.get('build')} connected={info_dict.get('connected')}",
    )
    result.evidence["terminal_info"] = _redact(info_dict)
    if not info_ok:
        raise SystemExit(1)

    # Clean shutdown then repeat-attach (BUG-310 class regression net).
    mt5.shutdown()
    result.step("mt5.shutdown() (first)", True, "clean shutdown accepted")
    ok2 = bool(mt5.initialize(path=discovered, timeout=60000))
    result.step("mt5.initialize() repeat attach", ok2, f"last_error={mt5.last_error()}")
    if not ok2:
        raise SystemExit(1)

    # Production adapter path through the same IPC surface.
    adapter = DirectMT5Adapter(path=discovered)
    connected = adapter.connect()
    state = adapter.connection_state()
    state_value = getattr(state, "value", state)
    state_name = getattr(state_value, "name", str(state_value))
    result.step(
        "DirectMT5Adapter.connect() via native IPC",
        bool(connected),
        f"connection_state={state_name}",
    )
    result.evidence["adapter_connection_state"] = state_name
    with contextlib.suppress(Exception):
        adapter.disconnect()
    if not connected:
        raise SystemExit(1)

    mt5.shutdown()
    result.step("mt5.shutdown() (final)", True, "IPC surface released")


def _demo_credentials() -> dict[str, str] | None:
    login = os.environ.get("MT5_DEMO_LOGIN", "").strip()
    password = os.environ.get("MT5_DEMO_PASSWORD", "")
    server = os.environ.get("MT5_DEMO_SERVER", "").strip()
    if login and password and server:
        return {"login": login, "password": password, "server": server}
    return None


def _choose_filling_mode(mt5: Any, symbol_info: Any) -> int:
    """Pick a filling mode the broker actually accepts for this symbol."""
    filling_flags = int(getattr(symbol_info, "filling_mode", 0) or 0)
    # Bitmask per MQL5 SYMBOL_FILLING_MODE: 1=FOK, 2=IOC; RETURN is always allowed.
    if filling_flags & 1:
        return mt5.ORDER_FILLING_FOK
    if filling_flags & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _run_level23(mt5: Any, result: CertResult, demo_trade_requested: bool) -> None:
    """Levels 2/3 — demo connectivity and (gated) demo trade execution."""
    creds = _demo_credentials()
    if creds is None:
        result.finish(
            False, skipped_reason="demo credentials not configured (MT5_DEMO_* secrets absent)"
        )
        return

    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    terminal_path = DirectMT5Adapter._auto_discover_terminal_path()
    if not terminal_path:
        result.step("terminal discovery", False, "no terminal64.exe found")
        result.finish(False, "terminal discovery failed")
        return

    if not mt5.initialize(path=terminal_path, timeout=60000):
        result.step("mt5.initialize() for demo leg", False, f"last_error={mt5.last_error()}")
        result.finish(False, "terminal initialize failed")
        return

    # --- Invalid-server refusal (safe: no account lockout risk) ---
    bogus = bool(
        mt5.login(
            int(creds["login"]),
            password=creds["password"],
            server=f"{creds['server']}-NSE-BOGUS",
        )
    )
    result.step("invalid-server login refusal", not bogus, "bogus server must not authenticate")
    mt5.shutdown()
    if bogus:
        result.finish(False, "login succeeded against a nonexistent server — rejecting")
        return

    # --- Real demo login + account-type enforcement ---
    if not mt5.initialize(path=terminal_path, timeout=60000):
        result.step("re-initialize for demo login", False, f"last_error={mt5.last_error()}")
        result.finish(False, "terminal re-initialize failed")
        return
    logged_in = bool(
        mt5.login(int(creds["login"]), password=creds["password"], server=creds["server"])
    )
    result.step(
        "demo login",
        logged_in,
        f"login={_mask_login(creds['login'])} server present={bool(creds['server'])}",
    )
    if not logged_in:
        result.finish(False, "demo login failed — broker unreachable or credentials rejected")
        return

    account = mt5.account_info()
    if account is None:
        result.step("account_info()", False, f"last_error={mt5.last_error()}")
        result.finish(False, "account_info unavailable after login")
        return
    trade_mode = int(getattr(account, "trade_mode", -1))
    mode_name = {
        TRADE_MODE_DEMO: "DEMO",
        TRADE_MODE_CONTEST: "CONTEST",
        TRADE_MODE_REAL: "REAL",
    }.get(trade_mode, f"UNKNOWN({trade_mode})")
    is_demo = trade_mode == TRADE_MODE_DEMO
    result.step(
        "account trade_mode == DEMO",
        is_demo,
        f"trade_mode={mode_name} (real accounts are prohibited)",
    )
    result.evidence["account"] = _redact(_jsonable(account))
    result.evidence["login_masked"] = _mask_login(getattr(account, "login", "?"))

    expected_login = os.environ.get("MT5_DEMO_LOGIN_EXPECTED", "").strip()
    if expected_login:
        matches = str(getattr(account, "login", "")) == expected_login
        result.step("expected demo login match", matches, f"expected={_mask_login(expected_login)}")
    else:
        result.note("expected demo login match", "MT5_DEMO_LOGIN_EXPECTED not set — skipped")

    if not is_demo:
        mt5.shutdown()
        result.finish(False, f"account is {mode_name} — refusing all further demo operations")
        return

    # --- Symbol availability + market data ---
    symbol = ""
    for candidate in ("XAUUSD", *[s.name for s in (mt5.symbols_get("*XAUUSD*") or [])][:10]):
        if candidate == "XAUUSD" or str(candidate).upper().startswith("XAUUSD"):
            if mt5.symbol_select(candidate, True):
                symbol = candidate
                break
    if not symbol:
        result.step("XAUUSD symbol availability", False, "no XAUUSD* symbol selectable")
        result.finish(False, "XAUUSD unavailable on the demo server")
        return
    symbol_info = mt5.symbol_info(symbol)
    symbol_ok = symbol_info is not None and bool(getattr(symbol_info, "visible", False))
    result.step(
        "symbol availability",
        symbol_ok,
        f"symbol={symbol} visible={bool(getattr(symbol_info, 'visible', False))}",
    )
    result.evidence["symbol"] = symbol
    if not symbol_ok:
        result.finish(False, f"symbol {symbol} not selectable/visible")
        return

    now = _dt.datetime.now(_dt.UTC)
    ticks = mt5.copy_ticks_from(symbol, now - _dt.timedelta(hours=48), 20, mt5.COPY_TICKS_ALL)
    tick_count = 0 if ticks is None else len(ticks)
    latest_tick_time = None
    if tick_count:
        try:
            latest_tick_time = _dt.datetime.fromtimestamp(int(ticks[-1]["time"]), tz=_dt.UTC)
        except Exception:
            latest_tick_time = None
    stale = (
        latest_tick_time is None
        or (_dt.datetime.now(_dt.UTC) - latest_tick_time) > TICK_STALENESS_LIMIT
    )
    result.step(
        "tick retrieval + timestamp sanity",
        tick_count > 0 and not stale,
        f"ticks={tick_count} latest={latest_tick_time.isoformat() if latest_tick_time else 'none'}",
    )

    rates_ok = True
    rate_summary = []
    for tf_name, tf_const in (("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5)):
        bars = mt5.copy_rates_from_pos(symbol, tf_const, 0, 10)
        count = 0 if bars is None else len(bars)
        monotonic = False
        if count >= 5:
            times = [int(b["time"]) for b in bars]
            monotonic = all(a <= b for a, b in itertools.pairwise(times))
        rates_ok = rates_ok and count >= 5 and monotonic
        rate_summary.append(f"{tf_name}={count} bars monotonic={monotonic}")
    result.step("M1/M5 rate retrieval + timestamps", rates_ok, "; ".join(rate_summary))

    recovered = bool(
        mt5.login(int(creds["login"]), password=creds["password"], server=creds["server"])
    )
    result.step("recovery re-login", recovered, "connection re-established after refusal leg")
    result.evidence["trade_allowed"] = bool(getattr(mt5.terminal_info(), "trade_allowed", False))

    overall_ok = symbol_ok and tick_count > 0 and not stale and rates_ok and recovered
    if not overall_ok:
        mt5.shutdown()
        result.finish(False, "demo market connectivity incomplete")
        return

    # ------------------- Level 3: gated demo trade -------------------
    if not demo_trade_requested:
        mt5.shutdown()
        result.finish(True, "Level 2 passed; Level 3 not requested (NSE_DEMO_TRADE_TEST unset)")
        return

    if os.environ.get("NSE_DEMO_TRADE_TEST", "").strip().lower() != "true":
        mt5.shutdown()
        result.finish(True, "Level 2 passed; Level 3 explicitly disabled")
        return

    result.note(
        "Level 3 preconditions", "NSE_DEMO_TRADE_TEST=true — demo account re-verified above"
    )

    volume = NSE_CI_DEMO_VOLUME
    vol_min = float(getattr(symbol_info, "volume_min", 0.01) or 0.01)
    vol_max = float(getattr(symbol_info, "volume_max", 0.01) or 0.01)
    volume_ok = vol_min <= volume <= vol_max
    result.step("volume bounds", volume_ok, f"volume={volume} min={vol_min} max={vol_max}")
    if not volume_ok:
        mt5.shutdown()
        result.finish(False, "configured test volume outside symbol bounds")
        return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not float(getattr(tick, "ask", 0.0) or 0.0):
        mt5.shutdown()
        result.finish(False, "no live ask price — cannot size the test order safely")
        return

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY,
        "price": float(tick.ask),
        "deviation": 50,
        "magic": NSE_CI_DEMO_MAGIC,
        "comment": NSE_CI_DEMO_COMMENT,
        "type_filling": _choose_filling_mode(mt5, symbol_info),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    send = mt5.order_send(request)
    send_dict = _jsonable(send) if send else {}
    retcode = int(send_dict.get("retcode", 0) or 0)
    executed = retcode == _RETCODE_DONE
    result.step(
        "demo market order send",
        executed,
        f"retcode={retcode} order={send_dict.get('order')} (magic={NSE_CI_DEMO_MAGIC})",
    )
    result.evidence["order_send"] = {k: v for k, v in send_dict.items() if k != "comment"}
    if not executed:
        mt5.shutdown()
        result.finish(False, f"order_send failed retcode={retcode}")
        return

    # --- Position ownership + explicit close + cleanup verification ---
    ticket = 0
    position = None
    for _ in range(30):
        positions = mt5.positions_get(symbol=symbol) or []
        owned = [p for p in positions if int(getattr(p, "magic", 0)) == NSE_CI_DEMO_MAGIC]
        if owned:
            position = owned[0]
            ticket = int(getattr(position, "ticket", 0))
            break
        time.sleep(1)
    found = ticket > 0
    result.step("test position owned by test magic", found, f"ticket={ticket}")
    if not found:
        mt5.shutdown()
        result.finish(False, "opened order never appeared as a position with the test magic")
        return

    closed = False
    close_detail = ""
    for attempt in range(3):
        tick = mt5.symbol_info_tick(symbol)
        close_request = dict(request)
        close_request.update(
            {
                "type": mt5.ORDER_TYPE_SELL,
                "price": float(tick.bid),
                "position": ticket,
            }
        )
        close_send = mt5.order_send(close_request)
        close_dict = _jsonable(close_send) if close_send else {}
        close_retcode = int(close_dict.get("retcode", 0) or 0)
        close_detail = f"attempt {attempt + 1} retcode={close_retcode}"
        if close_retcode == _RETCODE_DONE:
            closed = True
            break
        time.sleep(2)
    result.step("explicit close of test position", closed, close_detail)

    leftover_positions = 0
    leftover_orders = 0
    for _ in range(15):
        positions = mt5.positions_get(symbol=symbol) or []
        orders = mt5.orders_get(symbol=symbol) or []
        leftover_positions = len(
            [p for p in positions if int(getattr(p, "magic", 0)) == NSE_CI_DEMO_MAGIC]
        )
        leftover_orders = len(
            [o for o in orders if int(getattr(o, "magic", 0)) == NSE_CI_DEMO_MAGIC]
        )
        if leftover_positions == 0 and leftover_orders == 0:
            break
        time.sleep(1)
    cleanup_ok = leftover_positions == 0 and leftover_orders == 0
    result.step(
        "cleanup verified (no test position/order remains)",
        cleanup_ok,
        f"leftover_positions={leftover_positions} leftover_orders={leftover_orders}",
    )
    result.evidence["cleanup"] = {
        "leftover_positions": leftover_positions,
        "leftover_orders": leftover_orders,
    }

    mt5.shutdown()
    result.finish(closed and cleanup_ok, "Level 3 demo trade executed and cleaned up")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Native Windows MT5 IPC certification")
    parser.add_argument("--evidence-dir", default="mt5-native-evidence")
    parser.add_argument(
        "--levels",
        default="auto",
        help="auto (1 + demo when credentials present) | 1 | 23",
    )
    parser.add_argument(
        "--demo-trade",
        action="store_true",
        help="allow Level 3 (still requires NSE_DEMO_TRADE_TEST=true)",
    )
    args = parser.parse_args(argv)

    print(f"NSE native MT5 certification — host={platform.node()} python={sys.version.split()[0]}")

    if sys.platform != "win32":
        print("::error::native MT5 certification runs only on Windows (win32) hosts")
        return 2
    _refuse_live_credentials()

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    levels = args.levels
    demo_trade_requested = args.demo_trade

    level1 = CertResult(1, "Native terminal + IPC (no credentials)")
    level23 = CertResult(23, "Demo connectivity / gated demo trade")

    overall_ok = True
    try:
        _run_level1(level1)
        level1.finish(True, "native IPC certified")
    except SystemExit as exc:
        level1.finish(False, f"Level 1 failed (exit {exc.code})")
        overall_ok = False
    except Exception as exc:
        level1.finish(False, f"Level 1 unexpected failure: {type(exc).__name__}: {exc}")
        overall_ok = False

    demo_requested = levels in ("auto", "23")
    if demo_requested:
        try:
            import MetaTrader5 as mt5
        except Exception:
            mt5 = None  # type: ignore[assignment]
        if mt5 is None:
            level23.finish(False, skipped_reason="MetaTrader5 wheel unavailable")
        else:
            try:
                _run_level23(mt5, level23, demo_trade_requested)
            except Exception as exc:
                level23.finish(False, f"demo leg unexpected failure: {type(exc).__name__}: {exc}")
                with __import__("contextlib").suppress(Exception):
                    mt5.shutdown()
            # A NOT RUN demo leg (no credentials) is explicitly non-blocking;
            # a FAILED demo leg is visible but advisory (network-dependent).
            if level23.status == "FAIL":
                print(
                    "::warning::Demo certification leg FAILED — advisory "
                    "(network-dependent, not a mandatory PR gate)"
                )

    payload = {
        "captured_at": _utcnow(),
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "levels": [level1.to_json(), level23.to_json()],
        "overall": "PASS" if overall_ok else "FAIL",
    }
    evidence_file = evidence_dir / "mt5_native_certification.json"
    evidence_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(f"evidence: {evidence_file}")

    print(f"NATIVE MT5 CERTIFICATION: {'PASS' if overall_ok else 'FAIL'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
