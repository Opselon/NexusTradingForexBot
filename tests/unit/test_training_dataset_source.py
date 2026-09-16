"""Acquisition tests use generated fixtures; no live broker was contacted."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, ClassVar

import pytest


def write_bars(path: Path, count: int = 3002) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "tick_volume", "spread"])
        for i in range(count):
            writer.writerow([1700000040 + 60 * i, 1900, 1902, 1899, 1901, 12, 20])
    return path


def test_file_preparation_validates_real_rows_and_selects_tail(tmp_path):
    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    source = write_bars(tmp_path / "input.csv")
    original = source.read_bytes()
    events = []
    result = prepare_training_dataset(
        source_file=source, candles=3000, output_dir=tmp_path / "prepared", progress=events.append
    )
    rows = list(csv.DictReader(result.open()))
    assert result.is_file() and result != source
    assert len(rows) == 3000
    assert int(rows[0]["time"]) == 1700000160
    assert source.read_bytes() == original
    assert events[-1].stage == "dataset"
    assert events[-1].metrics["selected_rows"] == 3000


def test_broker_uses_existing_read_only_provider_and_no_synthetic_fallback(tmp_path):
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    class Broker:
        calls: ClassVar[list[Any]] = []

        def get_rate_history(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(
                    time=1700000040 + 60 * i,
                    open=1900,
                    high=1902,
                    low=1899,
                    close=1901,
                    tick_volume=12,
                    spread=20,
                    real_volume=0,
                    available=True,
                    source="BROKER_NATIVE",
                )
                for i in reversed(range(3001))
            ]

    broker = Broker()
    result = prepare_training_dataset(
        source="broker", adapter=broker, candles=3000, output_dir=tmp_path
    )
    assert broker.calls == [{"symbol": "XAUUSD", "timeframe": "M1", "count": 3001}]
    with result.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3000
    assert int(rows[0]["time"]) == 1700000100


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"source": "auto"}, "source"),
        ({"source_file": None}, "file"),
        ({"candles": -1}, "candles"),
        ({"candles": True}, "candles"),
        ({"candles": 1.5}, "candles"),
        ({"candles": 1000}, "3000"),
        ({"symbol": "EURUSD"}, "XAUUSD"),
        ({"timeframe": "M5"}, "M1"),
        ({"source": "broker", "source_file": None}, "candles"),
        ({"source": "broker"}, "both"),
    ],
)
def test_input_contract_fail_closed_before_any_acquisition(tmp_path, kwargs, match):
    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
    )

    options = {"source_file": write_bars(tmp_path / "input.csv"), "output_dir": tmp_path / "out"}
    options.update(kwargs)
    with pytest.raises(DatasetSourceError, match=match):
        prepare_training_dataset(**options)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "bad_row,match",
    [
        ("1700000040,1900,1890,1899,1901,12,20", "OHLC"),
        ("1700000040,nan,1902,1899,1901,12,20", "finite"),
        ("1700000040,1900,1902,1899,1901,-12,20", "volume"),
        ("garbage,1900,1902,1899,1901,12,20", "timestamp"),
    ],
)
def test_invalid_rows_are_rejected_not_replaced(tmp_path, bad_row, match):
    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
    )

    source = write_bars(tmp_path / "input.csv")
    with source.open("a") as handle:
        handle.write(bad_row + "\n")
    with pytest.raises(DatasetSourceError, match=match):
        prepare_training_dataset(source_file=source, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_short_history_and_missing_broker_capability_are_explicit(tmp_path):
    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
    )

    with pytest.raises(DatasetSourceError, match="3000"):
        prepare_training_dataset(
            source_file=write_bars(tmp_path / "short.csv", 2999), output_dir=tmp_path / "out"
        )
    with pytest.raises(DatasetSourceError, match="history"):
        prepare_training_dataset(
            source="broker", adapter=object(), candles=3000, output_dir=tmp_path / "out"
        )


def test_broker_rejects_paper_provenance(tmp_path):
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
    )

    bar = SimpleNamespace(
        source="PAPER_SIMULATION",
        available=True,
        time=1700000040,
        open=1900,
        high=1902,
        low=1899,
        close=1901,
        tick_volume=1,
    )
    broker = SimpleNamespace(get_rate_history=lambda **kwargs: [bar] * 3000)
    with pytest.raises(DatasetSourceError, match="BROKER_NATIVE"):
        prepare_training_dataset(
            source="broker", adapter=broker, candles=3000, output_dir=tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


def test_cancel_during_history_call_never_publishes_dataset(tmp_path):
    import threading
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset
    from nexus_scalp.model_provisioning.pipeline import TrainingCancelledError

    cancel = threading.Event()

    def fetch(**kwargs):
        cancel.set()
        return []

    events = []
    with pytest.raises(TrainingCancelledError):
        prepare_training_dataset(
            source="broker",
            adapter=SimpleNamespace(get_rate_history=fetch),
            candles=3000,
            output_dir=tmp_path / "out",
            cancel_event=cancel,
            progress=events.append,
        )
    assert events[-1].status == "cancelled"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "ready, consent, expected_install, exit_code",
    [
        (True, False, 0, 0),
        (False, False, 0, 3),
        (False, True, 1, 0),
    ],
)
def test_cli_consent_and_external_ready_worker_path(
    tmp_path, monkeypatch, ready, consent, expected_install, exit_code
):
    import json
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app
    from nexus_scalp.model_provisioning import pipeline, training_env
    from nexus_scalp.release import paths

    monkeypatch.setattr(paths, "get_runtime_workspace", lambda: tmp_path)
    source = write_bars(tmp_path / "input.csv")
    calls = []

    class Report:
        training_ready = ready
        in_process_ready = False
        backend = "cpu"
        gpu: ClassVar[dict[str, Any]] = {}
        checks: ClassVar[list[Any]] = []

        def as_dict(self):
            return {"training_ready": self.training_ready, "in_process_ready": False}

    report = Report()

    class Manager:
        def __init__(self, **kwargs):
            calls.append(("workspace", kwargs))

        def status(self, **kwargs):
            return report

        def install(self, **kwargs):
            calls.append(("install", kwargs))
            kwargs["progress"]("installing pinned stack (mock, no download)")
            report.training_ready = True
            return report

    monkeypatch.setattr(training_env, "TrainingEnvironmentManager", Manager)
    monkeypatch.setattr(
        pipeline,
        "train_local_model",
        lambda req, **kwargs: calls.append(("train", req)) or {"outcome": "CANDIDATE"},
    )
    args = [
        "model-train-local",
        "--input",
        str(source),
        "--candles",
        "3000",
        "--backend",
        "cpu",
        "--json",
        "--no-install",
    ]
    if consent:
        args.append("--prepare-environment")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert sum(name == "install" for name, _ in calls) == expected_install
    if exit_code == 0:
        assert payload["outcome"] == "CANDIDATE"
        req = next(value for name, value in calls if name == "train")
        assert req.source_file.is_file()
        assert req.source_file != source
    else:
        assert payload["error"] == "TRAINING_ENV_BLOCKED"
        assert not any(name == "train" for name, _ in calls)


def test_cli_train_help_is_complete_and_source_options_explicit():
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    result = CliRunner().invoke(app, ["model-train-local", "--help"], terminal_width=160)
    assert result.exit_code == 0
    for token in (
        "--source",
        "--prepare-environment",
        "--timeframe",
        "Ctrl+C",
        "Python",
        "governed",
        "Examples",
    ):
        assert token in result.output


def test_remote_gateway_reuses_existing_get_historical_bars_contract(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    adapter = object.__new__(RemoteMT5GatewayAdapter)
    calls = []

    def rpc(command, params):
        calls.append((command, params))
        return {
            "data": [
                dict(
                    timestamp=datetime.fromtimestamp(1700000040 + i * 60, UTC).isoformat(),
                    open=1900,
                    high=1902,
                    low=1899,
                    close=1901,
                    tick_volume=12,
                )
                for i in range(3000)
            ]
        }

    monkeypatch.setattr(adapter, "_send_request", rpc)
    path = prepare_training_dataset(
        source="broker", adapter=adapter, candles=3000, output_dir=tmp_path
    )
    assert path.is_file()
    assert calls == [
        ("GET_HISTORICAL_BARS", {"symbol": "XAUUSD", "timeframe": "M1", "count": 3001})
    ]


def test_native_history_prefers_provider_utc_over_broker_epoch(tmp_path):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    bars = [
        SimpleNamespace(
            time=1700007240 + i * 60,
            time_utc=datetime.fromtimestamp(1700000040 + i * 60, UTC),
            open=1900,
            high=1902,
            low=1899,
            close=1901,
            tick_volume=12,
            available=True,
            source="BROKER_NATIVE",
        )
        for i in range(3000)
    ]
    path = prepare_training_dataset(
        source="broker",
        adapter=SimpleNamespace(get_rate_history=lambda **kw: bars),
        candles=3000,
        output_dir=tmp_path,
    )
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert int(rows[0]["time"]) == 1700000040


def test_first_setup_without_engine_owns_only_scoped_history_connection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nexus_scalp.adapters.mt5 import mt5_adapter
    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    actions = []

    class Direct:
        def connect(self):
            actions.append("connect")
            return True

        def disconnect(self):
            actions.append("disconnect")

        def get_rate_history(self, **kwargs):
            actions.append("history")
            return [
                SimpleNamespace(
                    time=1700000040 + i * 60,
                    open=1900,
                    high=1902,
                    low=1899,
                    close=1901,
                    tick_volume=12,
                    source="BROKER_NATIVE",
                    available=True,
                )
                for i in range(3001)
            ]

    monkeypatch.setattr(mt5_adapter, "DirectMT5Adapter", Direct)
    path = prepare_training_dataset(source="broker", candles=3000, output_dir=tmp_path)
    assert path.is_file()
    assert actions == ["connect", "history", "disconnect"]


def test_scoped_history_connection_disconnects_after_error(monkeypatch):
    from types import SimpleNamespace

    from nexus_scalp.adapters.mt5 import mt5_adapter
    from nexus_scalp.model_provisioning.dataset_source import (
        DatasetSourceError,
        prepare_training_dataset,
    )

    actions = []
    monkeypatch.setattr(
        mt5_adapter,
        "DirectMT5Adapter",
        lambda: SimpleNamespace(
            connect=lambda: False, disconnect=lambda: actions.append("disconnect")
        ),
    )
    with pytest.raises(DatasetSourceError, match="MT5"):
        prepare_training_dataset(source="broker", candles=3000)
    assert actions == ["disconnect"]


def test_env_install_external_ready_is_success(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app
    from nexus_scalp.model_provisioning import training_env

    report = SimpleNamespace(
        training_ready=True, in_process_ready=False, as_dict=lambda: {"training_ready": True}
    )
    seen = []

    class Manager:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def install(self, **kwargs):
            return report

    monkeypatch.setattr(training_env, "TrainingEnvironmentManager", Manager)
    result = CliRunner().invoke(app, ["model-train-env", "--install", "--backend", "cpu", "--json"])
    # External-READY install stays rc 3 (in-process mismatch) by existing contract;
    # the JSON payload still reports the READY managed environment for training.
    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["training_ready"]
    assert "workspace" in seen[0]


def test_train_once_forwards_consent_and_explicit_broker_source(monkeypatch):
    from typer.testing import CliRunner

    from nexus_scalp.cli import provision_commands
    from nexus_scalp.cli.main import app

    calls = []
    monkeypatch.setattr(provision_commands, "train_local_main", lambda **kw: calls.append(kw))
    result = CliRunner().invoke(
        app, ["train-once", "--bars", "10000", "--prepare-environment", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["source"] == "broker"
    assert calls[0]["prepare_environment"] is True
    assert calls[0]["input_file"] is None


def test_mt5_tab_export_and_closed_candle_filter(tmp_path):
    from datetime import UTC, datetime

    from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset

    path = tmp_path / "MT5.txt"
    with path.open("w") as handle:
        handle.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
        for i in range(3000):
            stamp = datetime.fromtimestamp(1700000040 + i * 60, UTC)
            handle.write(
                stamp.strftime("%Y.%m.%d\t%H:%M:%S") + "\t1900\t1902\t1899\t1901\t12\t0\t20\n"
            )
        stamp = datetime.now(UTC).replace(second=0, microsecond=0)
        handle.write(stamp.strftime("%Y.%m.%d\t%H:%M:%S") + "\t1900\t1902\t1899\t1901\t12\t0\t20\n")
    events = []
    result = prepare_training_dataset(
        source_file=path, output_dir=tmp_path / "out", progress=events.append
    )
    assert len(list(csv.DictReader(result.open()))) == 3000
    assert events[-1].metrics["excluded_unclosed"] == 1
