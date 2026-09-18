"""Model Studio CLI Commands — Neural Network Inspection, Prediction & Training (ML-UI-001).

CLI management interface for deep learning models:
  nexus model-quality       -- inspect architecture, parameter count, weights fingerprint, calibration
  nexus model-predict       -- run interactive 50D/70D forward pass with probabilities and layer norms
  nexus model-stress-test   -- automated adversarial robustness battery (flash-crash, noise, zero variance)
  nexus model-train-dataset -- dispatch model training with a chosen dataset
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer
from fastapi import HTTPException
from rich.panel import Panel
from rich.table import Table

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import console
from nexus_scalp.web.model_studio_routes import (
    ModelStudioBenchmarkRequest,
    ModelStudioDownloadRequest,
    ModelStudioHotLoadRequest,
    ModelStudioInspectFeaturesRequest,
    ModelStudioPositionDatasetRequest,
    ModelStudioPredictRequest,
    ModelStudioStressRequest,
    ModelStudioTrainRequest,
    ModelStudioVerifyRequest,
    execute_active_model,
    execute_benchmark,
    execute_download,
    execute_generate_position_dataset,
    execute_hot_load,
    execute_inspect_features,
    execute_list_models,
    execute_predict,
    execute_rollback,
    execute_stress_test,
    execute_train,
    execute_verify,
    get_studio_overview,
)


@app.command("model-quality")
def model_quality_command(
    dimension: int = typer.Option(50, "--dim", "-d", help="Model dimension (50 or 70)"),
    benchmark: bool = typer.Option(False, "--bench", "-b", help="Run 100-pass latency benchmark"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Inspect model quality, parameter count, weights fingerprint, and calibration."""
    overview = get_studio_overview(None)

    bench_data = None
    if benchmark:
        req = ModelStudioBenchmarkRequest(dimension=dimension, iterations=100)
        bench_data = execute_benchmark(req, None)

    data = {
        "status": "OK",
        "dimension": dimension,
        "source": overview["model_source"],
        "architecture": overview["architecture"],
        "parameters_total": overview["parameter_count"],
        "parameters_trainable": overview["trainable_parameters"],
        "weights_fingerprint": overview["weights_sha256"][:16],
        "scaler_status": overview["scaler_stats"].get("status", "ABSENT"),
        "benchmark": bench_data,
        "inspected_at": datetime.now(UTC).isoformat(),
    }

    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return

    table = Table(title=f"ScalpNet {dimension}D Model Quality & Neural Audit", border_style="cyan")
    table.add_column("Property", style="bold white")
    table.add_column("Value", style="green")

    table.add_row("Architecture", str(overview["architecture"]))
    table.add_row("Feature Dimension", f"{dimension}D")
    table.add_row("Source", str(overview["model_source"]))
    table.add_row("Total Parameters", f"{overview['parameter_count']:,}")
    table.add_row("Trainable Parameters", f"{overview['trainable_parameters']:,}")
    table.add_row("Weights SHA256 (prefix)", str(overview["weights_sha256"])[:16])
    table.add_row("Scaler Status", str(overview["scaler_stats"].get("status", "ABSENT")))

    if bench_data:
        table.add_row("Latency P50", f"{bench_data['latency_p50_ms']} ms")
        table.add_row("Latency P99", f"{bench_data['latency_p99_ms']} ms")
        table.add_row("Throughput", f"{bench_data['throughput_inferences_per_sec']} inf/s")

    console.print(table)


@app.command("model-predict")
def model_predict_command(
    dimension: int = typer.Option(50, "--dim", "-d", help="Feature dimension (50 or 70)"),
    fetch_70d: bool = typer.Option(
        False, "--fetch-70d", help="Fetch live Base+News+Liquidity components"
    ),
    noise: float = typer.Option(0.0, "--noise", help="Add perturbation noise sigma"),
    threshold: float = typer.Option(
        0.35, "--threshold", "-t", help="Signal policy confidence threshold"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Run an interactive neural network prediction test with uncertainty metrics."""
    req = ModelStudioPredictRequest(
        dimension=dimension,
        fetch_live_70d=fetch_70d,
        perturbation_sigma=noise,
        simulate_policy_threshold=threshold,
        inspect_layers=True,
        compute_saliency=True,
    )

    res = execute_predict(req, None)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    probs = res["probabilities"]
    conf = res["confidence"]
    entropy = res["shannon_entropy_bits"]
    decision = res["policy_simulation"]["final_action"]

    color = (
        "green" if decision == "BUY_MARKET" else "red" if decision == "SELL_MARKET" else "yellow"
    )
    panel_content = (
        f"[bold]Decision:[/bold] [{color}]{decision}[/{color}]\n"
        f"[bold]Confidence:[/bold] {conf * 100:.1f}%\n"
        f"[bold]Top-2 Margin:[/bold] {res['confidence_margin'] * 100:.1f}%\n"
        f"[bold]Uncertainty (Entropy):[/bold] {entropy:.4f} bits\n\n"
        f"[bold]Probabilities:[/bold]\n"
        f"  NO_TRADE:    {probs['no_trade'] * 100:.1f}%\n"
        f"  BUY_MARKET:  {probs['buy'] * 100:.1f}%\n"
        f"  SELL_MARKET: {probs['sell'] * 100:.1f}%\n\n"
        f"[bold]Inference Latency:[/bold] {res['latency_ms']['total_e2e']} ms"
    )
    console.print(
        Panel(panel_content, title=f"Neural Prediction ({dimension}D)", border_style=color)
    )


@app.command("model-stress-test")
def model_stress_test_command(
    dimension: int = typer.Option(50, "--dim", "-d", help="Model dimension (50 or 70)"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Run automated 6-step adversarial stress testing on the model."""
    req = ModelStudioStressRequest(dimension=dimension)
    res = execute_stress_test(req, None)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(title=f"Adversarial Stress Test Suite ({dimension}D)", border_style="magenta")
    table.add_column("Test Case", style="bold white")
    table.add_column("Result", style="bold")
    table.add_column("Detail", style="dim")

    for r in res["results"]:
        status_str = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        table.add_row(r["test"], status_str, r["detail"])

    console.print(table)
    overall_style = "green" if res["all_passed"] else "red"
    console.print(
        f"[bold {overall_style}]Overall Result: {'ALL TESTS PASSED' if res['all_passed'] else 'FAILURES DETECTED'}[/bold {overall_style}]"
    )


@app.command("model-train-dataset")
def model_train_dataset_command(
    dataset: str = typer.Option("", "--dataset", help="Path to parquet/csv dataset"),
    dimension: int = typer.Option(50, "--dim", help="Feature dimension (50 or 70)"),
    epochs: int = typer.Option(3, "--epochs", "-e", help="Training epochs"),
    batch_size: int = typer.Option(256, "--batch", "-b", help="Batch size"),
    lr: float = typer.Option(5e-4, "--lr", help="Learning rate"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Dispatch model training on a chosen dataset into the engine lifecycle."""
    req = ModelStudioTrainRequest(
        dataset_path=dataset,
        dimension=dimension,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=lr,
        seed=seed,
    )
    res = execute_train(req)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    console.print(
        Panel(
            f"[bold green]Training Dispatched Successfully[/bold green]\n\n"
            f"[bold]Run ID:[/bold] {res['run_id']}\n"
            f"[bold]Dataset:[/bold] {res['target_dataset']}\n"
            f"[bold]Dimension:[/bold] {dimension}D\n"
            f"[bold]Epochs:[/bold] {epochs}\n"
            f"[bold]Learning Rate:[/bold] {lr}\n"
            f"[bold]Status:[/bold] {res['state']['status']}",
            title="Model Studio Training Runner",
            border_style="green",
        )
    )


@app.command("dataset-download")
def dataset_download_command(
    symbol: str = typer.Option("XAUUSD", "--symbol", "-s", help="Symbol name (e.g. XAUUSD)"),
    timeframe: str = typer.Option("M1", "--timeframe", "-tf", help="Timeframe: M1, M3, M5, or M15"),
    bars: int = typer.Option(10000, "--bars", "-n", help="Number of candles to download"),
    source: str = typer.Option("synthetic", "--source", help="Data source: synthetic, mt5, or csv"),
    csv_path: str = typer.Option("", "--csv", help="Path to CSV if source=csv"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Download/ingest historical market candles with strict schema validation."""
    req = ModelStudioDownloadRequest(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        source=source,
        csv_path=csv_path or None,
    )
    res = execute_download(req)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(title=f"Market Dataset Download — {symbol} ({timeframe})", border_style="cyan")
    table.add_column("Property", style="bold white")
    table.add_column("Value", style="green")

    table.add_row("Status", res["status"])
    table.add_row("Rows Ingested", f"{res['rows']:,}")
    table.add_row("Symbol / Timeframe", f"{res['symbol']} / {res['timeframe']}")
    table.add_row("Source", res["source"])
    table.add_row("Target Path", res["dataset_path"])
    table.add_row("Size", res["size_display"])
    table.add_row("Throughput", f"{res['throughput_bars_sec']:.0f} bars/sec")
    console.print(table)


@app.command("dataset-inspect")
def dataset_inspect_command(
    dataset: str = typer.Option("", "--dataset", "-d", help="Path to parquet/csv dataset"),
    dimension: int = typer.Option(50, "--dim", help="Feature dimension (50 or 70)"),
    max_rows: int = typer.Option(500, "--rows", help="Rows to inspect"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Inspect dataset features, normalize values, and report statistical distribution."""
    req = ModelStudioInspectFeaturesRequest(
        dataset_path=dataset,
        dimension=dimension,
        max_rows=max_rows,
    )
    res = execute_inspect_features(req)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(
        title=f"Feature Inspection & Normalization ({dimension}D — {res['rows_processed']} rows)",
        border_style="blue",
    )
    table.add_column("#", style="dim", justify="right")
    table.add_column("Family", style="cyan")
    table.add_column("Feature Name", style="bold white")
    table.add_column("Raw Mean", justify="right")
    table.add_column("Raw Std", justify="right")
    table.add_column("Norm Sample", justify="right", style="green")
    table.add_column("Status", justify="center")

    for f in res["features"][:30]:
        status_style = "green" if f["status"] == "HEALTHY" else "yellow"
        table.add_row(
            str(f["index"]),
            f["family"],
            f["name"],
            f"{f['raw_mean']:.3f}",
            f"{f['raw_std']:.3f}",
            f"{f['normalized_sample']:.3f}",
            f"[{status_style}]{f['status']}[/{status_style}]",
        )

    console.print(table)
    console.print(
        f"[bold]Summary:[/bold] Total Features: {res['total_features']} | "
        f"[green]Healthy: {res['healthy_features']}[/green] | "
        f"[yellow]Clamped: {res['clamped_features']}[/yellow] | "
        f"[red]NaN: {res['nan_features']}[/red]"
    )


@app.command("position-dataset-generate")
def position_dataset_generate_command(
    dataset: str = typer.Option("", "--dataset", "-d", help="Path to source market candles"),
    dimension: int = typer.Option(50, "--dim", help="Feature dimension (50 or 70)"),
    bars: int = typer.Option(5000, "--bars", "-n", help="Bars limit"),
    max_holding: int = typer.Option(30, "--max-holding", help="Max holding bars"),
    target_atr: float = typer.Option(2.0, "--target-atr", help="Target ATR multiplier"),
    friction: float = typer.Option(0.25, "--friction", help="Friction in pips"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Generate Layer-2 Position Management dataset with mathematical labeling & anti-leakage."""
    req = ModelStudioPositionDatasetRequest(
        source_dataset_path=dataset,
        dimension=dimension,
        bars_limit=bars,
        max_holding_bars=max_holding,
        target_atr_multiplier=target_atr,
        friction_pips=friction,
    )
    res = execute_generate_position_dataset(req)

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(
        title="Layer-2 Position Management Dataset Generation",
        border_style="magenta",
    )
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="green")

    table.add_row("Status", res["status"])
    table.add_row("Total Position Samples", f"{res['total_samples']:,}")
    table.add_row("Simulated Trades", f"{res['simulated_trades']:,}")
    table.add_row("Mean Continuation Value", f"{res['mean_continuation_value']:+.3f} R")
    table.add_row("Mean Holding Bars", f"{res['mean_holding_bars']:.1f} bars")
    table.add_row(
        "Actions Distribution",
        f"KEEP: {res['actions_distribution'].get('KEEP', 0)} | "
        f"CLOSE: {res['actions_distribution'].get('CLOSE', 0)} | "
        f"REDUCE: {res['actions_distribution'].get('REDUCE', 0)}",
    )
    table.add_row(
        "Chronological Splits",
        f"Train: {res['splits'].get('train', 0)} | "
        f"Val: {res['splits'].get('val', 0)} | "
        f"OOS: {res['splits'].get('oos', 0)}",
    )
    table.add_row("Output Parquet Path", res.get("dataset_path") or res.get("output_path", ""))
    table.add_row("SHA-256 (prefix)", res["sha256"][:16])

    console.print(table)


@app.command("model-list")
def model_list_command(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """List all registered neural model checkpoints in the SQLite catalog."""
    res = execute_list_models()
    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(
        title=f"AI Hub Model Registry Catalog ({res['count']} checkpoints)",
        border_style="cyan",
    )
    table.add_column("Model ID", style="bold white")
    table.add_column("Dim", style="cyan")
    table.add_column("Stage", style="yellow")
    table.add_column("Loss", style="green")
    table.add_column("Val Loss", style="green")
    table.add_column("Fine-Tune", style="magenta")
    table.add_column("Active", style="bold green")

    active_id = res.get("active_champion_id")
    for m in res.get("models", []):
        is_act = "★ CHAMPION" if m.get("id") == active_id or m.get("is_active") else ""
        table.add_row(
            str(m.get("id")),
            f"{m.get('dimension')}D",
            str(m.get("stage", "STAGING")),
            f"{m.get('final_loss', 0.0):.4f}",
            f"{m.get('final_val_loss', 0.0):.4f}",
            "YES" if m.get("fine_tune_enabled") else "NO",
            is_act,
        )

    console.print(table)


@app.command("model-hot-load")
def model_hot_load_command(
    model_id: str = typer.Argument(..., help="Model ID, filename, or relative path to .pt file"),
    fine_tune: bool = typer.Option(
        False, "--fine-tune/--no-fine-tune", help="Enable fine-tuning mode"
    ),
    attach_scaler: bool = typer.Option(
        True, "--attach-scaler/--no-attach-scaler", help="Attach matching scaler"
    ),
    operator: str = typer.Option("CLI_USER", "--operator", help="Operator identity"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Hot-load a model checkpoint and scaler into live memory without restarting."""
    req = ModelStudioHotLoadRequest(
        model_id=model_id,
        fine_tune_enabled=fine_tune,
        attach_scaler=attach_scaler,
        operator=operator,
    )
    res = execute_hot_load(req)
    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    panel = Panel(
        f"[bold green]⚡ Model Hot-Loaded Successfully into Active Runtime Memory[/bold green]\n\n"
        f"[bold white]Model ID:[/bold white] {res['model_id']}\n"
        f"[bold white]Dimension:[/bold white] {res['dimension']}D\n"
        f"[bold white]Architecture:[/bold white] {res['architecture']}\n"
        f"[bold white]Weights SHA256:[/bold white] {res['weights_sha256'][:16]}...\n"
        f"[bold white]Scaler Attached:[/bold white] {'YES' if res['scaler_attached'] else 'NO (default unit scaler)'}\n"
        f"[bold white]Fine-Tune Enabled:[/bold white] {'YES' if res['fine_tune_enabled'] else 'NO'}\n"
        f"[bold white]Warmup Latency:[/bold white] {res['warmup_latency_us']} µs\n"
        f"[bold white]Stage:[/bold white] {res['stage']}\n"
        f"[bold white]Loaded At:[/bold white] {res['loaded_at']}",
        title="AI Hub Hot-Loader",
        border_style="green",
    )
    console.print(panel)


@app.command("model-active")
def model_active_command(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Show details of the currently hot-loaded active champion model in memory."""
    res = execute_active_model()
    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    if res.get("status") == "NO_ACTIVE_MODEL":
        console.print("[yellow]No model is currently hot-loaded in memory.[/yellow]")
        return

    act = res.get("active_model", {})
    panel = Panel(
        f"[bold green]Active Champion Model State[/bold green]\n\n"
        f"[bold white]Model ID:[/bold white] {act.get('model_id')}\n"
        f"[bold white]Dimension:[/bold white] {act.get('dimension')}D\n"
        f"[bold white]Architecture:[/bold white] {act.get('architecture')}\n"
        f"[bold white]Weights SHA256:[/bold white] {act.get('weights_sha256')[:16]}...\n"
        f"[bold white]Weights Path:[/bold white] {act.get('weights_path')}\n"
        f"[bold white]Scaler Path:[/bold white] {act.get('scaler_path') or 'None'}\n"
        f"[bold white]Fine-Tune Mode:[/bold white] {'ENABLED' if act.get('fine_tune_enabled') else 'DISABLED'}\n"
        f"[bold white]Inference Count:[/bold white] {act.get('inference_count')}\n"
        f"[bold white]Stage:[/bold white] {act.get('stage')}\n"
        f"[bold white]Loaded At:[/bold white] {act.get('loaded_at')}",
        title="AI Hub Active Runtime Model",
        border_style="cyan",
    )
    console.print(panel)


@app.command("model-rollback")
def model_rollback_command(
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Roll back active model to the previously active champion model from history."""
    try:
        res = execute_rollback()
    except HTTPException as err:
        if json_output:
            typer.echo(json.dumps({"status": "ERROR", "detail": err.detail}, indent=2))
        else:
            console.print(f"[bold red]✗ {err.detail}[/bold red]")
        raise typer.Exit(code=1) from err

    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    console.print(f"[bold green]✓ {res['message']}[/bold green]")


@app.command("model-verify")
def model_verify_command(
    model_id: str = typer.Argument(..., help="Model ID or checkpoint path to verify"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON envelope"),
) -> None:
    """Run pre-load verification battery (tensors, NaN check, weights variance, smoke inference)."""
    req = ModelStudioVerifyRequest(model_id=model_id)
    res = execute_verify(req)
    if json_output:
        typer.echo(json.dumps(res, indent=2))
        return

    table = Table(
        title=f"Pre-Load Verification Battery for {model_id}",
        border_style="green" if res.get("all_passed") else "red",
    )
    table.add_column("Check", style="bold white")
    table.add_column("Passed", style="cyan")
    table.add_column("Detail", style="green")

    for c in res.get("checks", []):
        table.add_row(
            c.get("name"),
            "✓ YES" if c.get("passed") else "✗ NO",
            str(c.get("detail")),
        )

    console.print(table)
