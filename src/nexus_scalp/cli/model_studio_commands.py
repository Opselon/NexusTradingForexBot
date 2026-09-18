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
from rich.panel import Panel
from rich.table import Table

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import console
from nexus_scalp.web.model_studio_routes import (
    ModelStudioBenchmarkRequest,
    ModelStudioPredictRequest,
    ModelStudioStressRequest,
    ModelStudioTrainRequest,
    execute_benchmark,
    execute_predict,
    execute_stress_test,
    execute_train,
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
        console.print(json.dumps(data, indent=2))
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
        console.print(json.dumps(res, indent=2))
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
        console.print(json.dumps(res, indent=2))
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
        console.print(json.dumps(res, indent=2))
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
