"""End-to-end: a ledger turns into a policy artifact the runtime can read.

The unit tests around ``optimize`` prove the statistics. This one proves the
wiring — that what the learner writes is exactly what ``load_learned_profile``
consumes, with no key-name drift between the producer and the consumer.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from nexus_scalp.execution.lifecycle.exit_policy import load_learned_profile

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "learn_exit_policy.py"


@pytest.fixture(scope="module")
def learner():
    spec = importlib.util.spec_from_file_location("learn_exit_policy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def write_csv(path: Path, rows: list[tuple[str, float, float]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["regime", "mfe_r", "final_r"])
        writer.writerows(rows)
    return path


def round_trip_book(n: int = 40) -> list[tuple[str, float, float]]:
    rows = [("TREND", 1.0, -1.0) for _ in range(n)]
    rows += [("TREND", 2.0, 1.8) for _ in range(n)]
    return rows


def test_the_artifact_written_is_the_one_the_runtime_loads(learner, tmp_path):
    source = write_csv(tmp_path / "trades.csv", round_trip_book())
    out = tmp_path / "artifacts" / "exit_policy.json"

    assert learner.main(["--from-csv", str(source), "--out", str(out), "--min-samples", "20"]) == 0
    assert out.exists()

    loaded = load_learned_profile(tmp_path / "artifacts")
    assert loaded["TREND"] == pytest.approx(1.2)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert payload["regimes"]["TREND"]["samples"] == 80


def test_a_dry_run_writes_nothing(learner, tmp_path):
    source = write_csv(tmp_path / "trades.csv", round_trip_book())
    out = tmp_path / "exit_policy.json"
    assert learner.main(["--from-csv", str(source), "--out", str(out), "--min-samples", "20", "--dry-run"]) == 0
    assert not out.exists()


def test_a_thin_book_never_overwrites_the_live_artifact(learner, tmp_path):
    out = tmp_path / "exit_policy.json"
    out.write_text(json.dumps({"version": 3, "regimes": {"TREND": {"trigger_r": 1.5}}}), encoding="utf-8")
    source = write_csv(tmp_path / "trades.csv", [("TREND", 1.0, -1.0)] * 3)

    assert learner.main(["--from-csv", str(source), "--out", str(out), "--min-samples", "20"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["regimes"]["TREND"]["trigger_r"] == 1.5


def test_malformed_rows_are_skipped_not_fatal(learner, tmp_path):
    path = tmp_path / "trades.csv"
    path.write_text(
        "regime,mfe_r,final_r\nTREND,1.0,-1.0\nTREND,oops,-1.0\nTREND,1.2,0.9\n",
        encoding="utf-8",
    )
    assert learner.main(["--from-csv", str(path), "--dry-run"]) == 0


def test_an_empty_input_is_refused(learner, tmp_path):
    path = write_csv(tmp_path / "empty.csv", [])
    assert learner.main(["--from-csv", str(path), "--dry-run"]) == 2
