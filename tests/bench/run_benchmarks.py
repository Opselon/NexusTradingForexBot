#!/usr/bin/env python3
"""Deterministic micro-benchmarks for the decision path (CI + local).

Why a bespoke runner instead of pytest-benchmark: the numbers must be
comparable between a developer laptop and a shared GitHub runner without a
plugin, a fixture graph, or a torch import. Each benchmark is a plain callable
exercised through the real code path, with a budget (an absolute ceiling that
catches algorithmic regressions) and an optional comparison against a recorded
baseline (catches drift on the same machine).

Verdict logic
-------------
* ``FAIL`` — any benchmark exceeded its budget (always fatal with
  ``--fail-on-regression``).
* ``REGRESSED`` — a benchmark exists in the baseline and is slower than
  ``baseline * (1 + max_regression_pct/100)`` and the delta exceeds the noise
  floor (measured by re-running the fastest benchmark twice).
* ``PASS`` — everything within budget and noise.

Exit codes: 0 pass, 1 regression/budget failure, 2 harness error.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import platform
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

JSON_PAYLOAD = (
    b'{"status":"ok","version":42,"rows":['
    + b",".join(
        b'{"ticket":%d,"symbol":"XAUUSD","pnl":%d.%02d}' % (i, i, i % 100) for i in range(2500)
    )
    + b"]}"
)


@dataclass
class BenchResult:
    name: str
    calls: int
    mean_us: float
    median_us: float
    p95_us: float
    budget_us: float
    baseline_us: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def over_budget(self) -> bool:
        return self.p95_us > self.budget_us

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "calls": self.calls,
            "mean_us": round(self.mean_us, 3),
            "median_us": round(self.median_us, 3),
            "p95_us": round(self.p95_us, 3),
            "budget_us": self.budget_us,
            "baseline_us": self.baseline_us,
            "over_budget": self.over_budget,
            "meta": self.meta,
        }


def measure(
    name: str, fn: Callable[[], Any], *, calls: int, budget_us: float, warmup: int = 5
) -> BenchResult:
    for _ in range(warmup):
        fn()
    gc.collect()
    samples: list[float] = []
    total = 0.0
    for _ in range(calls):
        start = time.perf_counter_ns()
        fn()
        elapsed = (time.perf_counter_ns() - start) / 1000.0
        samples.append(elapsed)
        total += elapsed
    samples.sort()
    return BenchResult(
        name=name,
        calls=calls,
        mean_us=total / calls,
        median_us=statistics.median(samples),
        p95_us=samples[max(0, int(len(samples) * 0.95) - 1)],
        budget_us=budget_us,
    )


# --------------------------------------------------------------------------- #
# Subjects under test (real objects, no mocks)
# --------------------------------------------------------------------------- #
def build_exit_policy_subject():
    from nexus_scalp.domain.enums import OrderType
    from nexus_scalp.domain.models import Position, SymbolInfo
    from nexus_scalp.execution.lifecycle.exit_policy import (
        ExitPolicyConfig,
        ExitProtectionPolicy,
    )

    policy = ExitProtectionPolicy(ExitPolicyConfig())
    position = Position(
        ticket=1,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=0.10,
        price_open=2400.00,
        sl=2390.00,
        tp=2430.00,
        profit=90.0,
        magic=20260101,
    )
    info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=20,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    def call():
        policy.plan(
            position=position,
            risk_usd=150.0,
            atr_price=1.20,
            contract_size=100.0,
            spread_price=0.12,
            hold_seconds=90.0,
            mfe_usd=90.0,
            symbol_info=info,
            regime="TREND",
        )

    return call, policy, position, info


def build_calibration_subject():
    import numpy as np

    from nexus_scalp.models.calibration import (
        apply_temperature,
        expected_calibration_error,
        fit_temperature,
    )

    rng = np.random.default_rng(11)
    logits = rng.normal(size=(64, 4))
    probs = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    labels = rng.integers(0, 4, size=64)
    big_probs = rng.dirichlet(np.ones(4), size=8000)
    big_labels = big_probs.argmax(axis=1)

    return {
        "apply": lambda: apply_temperature(probs, 1.7),
        "ece": lambda: expected_calibration_error(big_probs, big_labels),
        "fit": lambda: fit_temperature(probs, labels),
    }


def build_web_subject():
    from nexus_scalp.web.performance import PerfConfig, PerfMiddleware

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(JSON_PAYLOAD)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": JSON_PAYLOAD})

    middleware = PerfMiddleware(app, PerfConfig(min_compress_bytes=512, max_buffer_bytes=4_000_000))
    loop = asyncio.new_event_loop()

    def call(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/state",
            "headers": headers,
            "query_string": b"",
        }
        loop.run_until_complete(middleware(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent[1:])
        return {"status": start["status"], "headers": dict(start["headers"]), "body": body}

    return call, len(JSON_PAYLOAD), loop


def build_optimizer_subject():
    from nexus_scalp.experience.exit_optimizer import ExitObservation, optimize

    rows = []
    for i in range(3000):
        regime = ("TREND", "RANGE", "DEFAULT")[i % 3]
        mfe = 0.8 + (i % 17) * 0.15
        final = -1.0 if i % 3 == 0 else mfe - 0.2
        rows.append(ExitObservation(regime, mfe, final))
    return lambda: optimize(rows, min_samples=50)


# --------------------------------------------------------------------------- #
# Suites
# --------------------------------------------------------------------------- #
def run_benchmarks(max_regression_pct: float, baseline: dict[str, Any]) -> dict[str, Any]:
    results: list[BenchResult] = []

    plan, _policy, _position, _info = build_exit_policy_subject()
    results.append(measure("exit_policy.plan (no modify)", plan, calls=4000, budget_us=250.0))

    cal = build_calibration_subject()
    results.append(
        measure("calibration.apply_temperature (64x4)", cal["apply"], calls=20000, budget_us=60.0)
    )
    results.append(measure("calibration.ece (8k rows)", cal["ece"], calls=40, budget_us=12_000.0))

    web_call, raw_bytes, _loop = build_web_subject()
    gzip_result = measure(
        "web.middleware.gzip (JSON)",
        lambda: web_call([(b"accept-encoding", b"gzip")]),
        calls=400,
        budget_us=3_000.0,
    )
    first = web_call([(b"accept-encoding", b"gzip")])
    etag = first["headers"][b"etag"].decode()
    gzip_result.meta = {
        "raw_bytes": raw_bytes,
        "wire_bytes": len(first["body"]),
        "ratio": round(raw_bytes / max(1, len(first["body"])), 2),
    }
    results.append(gzip_result)

    revalidate = measure(
        "web.middleware.304 revalidate",
        lambda: web_call([(b"if-none-match", etag.encode())]),
        calls=3000,
        budget_us=2_000.0,
    )
    not_modified = web_call([(b"if-none-match", etag.encode())])
    revalidate.meta = {"status": not_modified["status"], "body_bytes": len(not_modified["body"])}
    results.append(revalidate)

    results.append(
        measure(
            "exit_optimizer.optimize (3k obs)",
            build_optimizer_subject(),
            calls=6,
            budget_us=400_000.0,
        )
    )

    # Noise floor: the same cheap call measured twice on THIS machine. Any
    # delta under it is measurement, not regression.
    first_pass = measure("noise.floor.a", cal["apply"], calls=4000, budget_us=1e9)
    second_pass = measure("noise.floor.b", cal["apply"], calls=4000, budget_us=1e9)
    noise_pct = (
        abs(second_pass.median_us - first_pass.median_us) / max(first_pass.median_us, 1e-9) * 100.0
    )
    noise_pct = round(max(noise_pct, 3.0), 2)

    verdict = "PASS"
    regressed: list[str] = []
    for result in results:
        base = baseline.get(result.name, {}).get("median_us") if baseline else None
        result.baseline_us = base
        if result.over_budget:
            verdict = "FAIL"
        elif base and (result.median_us - base) / base * 100.0 > max(max_regression_pct, noise_pct):
            regressed.append(result.name)
    if regressed and verdict != "FAIL":
        verdict = "REGRESSED"

    return {
        "tool": f"nse-bench/1 (python {platform.python_version()}, {platform.machine()})",
        "verdict": verdict,
        "regressed": regressed,
        "noise_floor_pct": noise_pct,
        "max_regression_pct": max_regression_pct,
        "benchmarks": [r.as_dict() for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-json", type=Path, default=None, help="recorded medians to compare against"
    )
    parser.add_argument(
        "--report-json", type=Path, default=None, help="where to write the full report"
    )
    parser.add_argument(
        "--write-baseline", action="store_true", help="overwrite the baseline with this run"
    )
    parser.add_argument(
        "--fail-on-regression", action="store_true", help="non-zero exit on FAIL/REGRESSED"
    )
    parser.add_argument("--max-regression-pct", type=float, default=20.0)
    args = parser.parse_args(argv)

    baseline: dict[str, Any] = {}
    if args.baseline_json and args.baseline_json.exists():
        try:
            baseline = json.loads(args.baseline_json.read_text(encoding="utf-8")).get(
                "benchmarks", {}
            )
        except Exception as error:  # pragma: no cover - defensive
            print(f"warning: unreadable baseline ({error}); continuing without it", file=sys.stderr)

    report = run_benchmarks(args.max_regression_pct, baseline)

    print(f"{'benchmark':<44}{'median':>12}{'p95':>12}{'budget':>12}  status")
    print("-" * 92)
    for bench in report["benchmarks"]:
        status = "OVER BUDGET" if bench["over_budget"] else "ok"
        print(
            f"{bench['name']:<44}{bench['median_us'] / 1000:>10.3f}ms"
            f"{bench['p95_us'] / 1000:>10.3f}ms{bench['budget_us'] / 1000:>10.1f}ms  {status}"
        )
        if bench["meta"]:
            print(f"{'':<44}{bench['meta']}")
    print("-" * 92)
    print(f"verdict: {report['verdict']}  noise floor: ±{report['noise_floor_pct']}%")
    if report["regressed"]:
        print("regressed: " + ", ".join(report["regressed"]))

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.write_baseline and args.baseline_json:
        payload = {
            "tool": report["tool"],
            "benchmarks": {
                b["name"]: {"median_us": b["median_us"], "budget_us": b["budget_us"]}
                for b in report["benchmarks"]
            },
        }
        args.baseline_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {args.baseline_json}")

    if args.fail_on_regression and report["verdict"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
