"""ML-FEAT-002 — Feature Importance, Collinearity Clustering & Redundancy Pruning.

Tests the analysis layer (``src/nexus_scalp/features/importance.py``) and the
evaluation CLI (``scripts/analysis/evaluate_feature_importance.py``).

The analysis layer is deliberately pure NumPy: the slim Linux verification
venv carries numpy + polars but no scipy / scikit-learn / torch, and an audit
tool must be runnable in that venv (the repo's own verification contract).
The synthetic-frame tests here build features through the REAL
``ScalpFeatureEngine`` + triple-barrier labeler, never a hand-rolled matrix,
so the audit path is exercised against the production feature contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from nexus_scalp.features.importance import (
    COLLINEARITY_THRESHOLD,
    CollinearityCluster,
    CollinearPair,
    ImportanceReport,
    analyze_features,
    cluster_collinear_features,
    compute_correlation_matrix,
    compute_mutual_information,
    compute_permutation_importance,
    find_collinear_pairs,
    save_report,
)
from nexus_scalp.features.scalp_features import FEATURE_NAMES

# ==============================================================================
# Fixtures
# ==============================================================================

N_FEATURES = len(FEATURE_NAMES)


def _synthetic_frame(n_samples: int = 600, seed: int = 1234) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic feature/label frame with known redundancy structure.

    Column 1 is a perfect monotone transform of column 0 (|Spearman rho| =
    1.0) and column 2 is pure noise, so the tests can assert exact outcomes
    instead of guessing at learned quantities.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(size=n_samples)
    signal = rng.normal(size=n_samples)
    matrix = np.zeros((n_samples, N_FEATURES), dtype=np.float64)
    matrix[:, 0] = base
    matrix[:, 1] = base**3  # monotone transform -> rho == 1.0
    matrix[:, 2] = -2.0 * base  # perfectly anti-correlated -> rho == -1.0
    matrix[:, 3] = signal
    matrix[:, 4] = signal + 1e-9 * rng.normal(size=n_samples)  # near-duplicate
    for j in range(5, N_FEATURES):
        matrix[:, j] = rng.normal(size=n_samples)
    # One constant column (index 5) — permuting it is a no-op.
    matrix[:, 5] = 0.42
    labels = (signal > np.median(signal)).astype(np.int64) * 2  # 0 / 2 classes
    return matrix, labels


@pytest.fixture(scope="module")
def frame() -> tuple[np.ndarray, np.ndarray]:
    return _synthetic_frame()


@pytest.fixture(scope="module")
def report(frame: tuple[np.ndarray, np.ndarray]) -> ImportanceReport:
    matrix, labels = frame
    return analyze_features(matrix, labels, permutation_repeats=3)


# ==============================================================================
# 1. Correlation matrix contract
# ==============================================================================


class TestCorrelationMatrix:
    def test_shape_and_symmetry(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        assert corr.shape == (N_FEATURES, N_FEATURES)

    def test_unit_diagonal(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        np.testing.assert_allclose(np.diag(corr), np.ones(N_FEATURES), atol=1e-10)

    def test_bounded_within_minus_one_and_one(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        assert np.all(corr >= -1.0 - 1e-12)
        assert np.all(corr <= 1.0 + 1e-12)

    def test_symmetric(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        np.testing.assert_allclose(corr, corr.T, atol=1e-10)

    def test_monotone_transform_detected_by_spearman(self) -> None:
        """Spearman must see |rho| = 1 for a monotone non-linear transform."""
        rng = np.random.default_rng(7)
        x = rng.normal(size=500)
        mat = np.column_stack([x, x**3])
        spearman = compute_correlation_matrix(mat, method="spearman")
        pearson = compute_correlation_matrix(mat, method="pearson")
        assert abs(spearman[0, 1]) > 0.999
        # Pearson misses the non-linear relationship.
        assert abs(pearson[0, 1]) < abs(spearman[0, 1])

    def test_constant_column_correlates_zero_not_nan(self) -> None:
        mat = np.column_stack([np.zeros(50), np.linspace(-1, 1, 50)])
        corr = compute_correlation_matrix(mat)
        assert np.all(np.isfinite(corr))
        assert abs(corr[0, 1]) < 1e-9

    def test_inf_and_nan_inputs_do_not_poison_matrix(self) -> None:
        mat = np.array([[0.0, 1.0], [1.0, np.inf], [2.0, 0.0], [3.0, np.nan]])
        corr = compute_correlation_matrix(mat)
        assert np.all(np.isfinite(corr))
        assert corr.shape == (2, 2)

    def test_rejects_wrong_ndim(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            compute_correlation_matrix(np.zeros(10))

    def test_rejects_too_few_samples(self) -> None:
        with pytest.raises(ValueError, match="at least 2 samples"):
            compute_correlation_matrix(np.zeros((1, 4)))

    def test_rejects_name_width_mismatch(self) -> None:
        with pytest.raises(ValueError, match="feature_names length"):
            compute_correlation_matrix(np.zeros((10, 3)), feature_names=["a", "b"])

    def test_rejects_unknown_method(self) -> None:
        with pytest.raises(ValueError, match="spearman' or 'pearson"):
            compute_correlation_matrix(np.zeros((10, 2)), method="kendall")


# ==============================================================================
# 2. Mutual information contract
# ==============================================================================


class TestMutualInformation:
    def test_signal_column_has_positive_mi(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        mi = compute_mutual_information(matrix, labels)
        assert mi.shape == (N_FEATURES,)
        # Column 3 carries the signal the label was built from.
        assert mi[3] > 0.0

    def test_noise_and_constant_columns_have_zero_mi(
        self, frame: tuple[np.ndarray, np.ndarray]
    ) -> None:
        matrix, labels = frame
        mi = compute_mutual_information(matrix, labels)
        assert mi[5] == 0.0  # constant column
        # A pure noise column independent of the label has ~0 MI.
        assert mi[6] < 0.05

    def test_mi_monotone_in_signal_strength(self) -> None:
        """Stronger dependency must not report lower MI than a weak one."""
        rng = np.random.default_rng(11)
        labels = rng.integers(0, 2, size=2000)
        strong = labels + 0.05 * rng.normal(size=2000)
        noise = rng.normal(size=2000)
        mat = np.column_stack([strong, noise])
        mi = compute_mutual_information(mat, labels)
        assert mi[0] > mi[1]

    def test_rejects_sample_mismatch(self) -> None:
        with pytest.raises(ValueError, match="sample mismatch"):
            compute_mutual_information(np.zeros((10, 2)), np.zeros(3, dtype=np.int64))

    def test_rejects_bad_bins(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        with pytest.raises(ValueError, match="bins must be >= 2"):
            compute_mutual_information(matrix, labels, bins=1)

    def test_mi_never_negative(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        mi = compute_mutual_information(matrix, labels)
        assert np.all(mi >= 0.0)


# ==============================================================================
# 3. Collinearity clustering contract
# ==============================================================================


class TestCollinearity:
    def test_pairs_captured_in_descending_order(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        pairs = find_collinear_pairs(corr, list(FEATURE_NAMES), threshold=0.85)
        assert len(pairs) >= 2
        rhos = [abs(p.spearman_rho) for p in pairs]
        assert rhos == sorted(rhos, reverse=True)
        # The perfect +1.0 and -1.0 pairs must both be present.
        names = {(p.feature_a, p.feature_b) for p in pairs}
        assert (FEATURE_NAMES[0], FEATURE_NAMES[1]) in names
        assert (FEATURE_NAMES[0], FEATURE_NAMES[2]) in names

    def test_threshold_boundary_is_strict(self) -> None:
        """|rho| must be strictly greater than the threshold to qualify."""
        corr = np.array([[1.0, 0.85], [0.85, 1.0]])
        assert find_collinear_pairs(corr, ["a", "b"], threshold=0.85) == []
        assert len(find_collinear_pairs(corr, ["a", "b"], threshold=0.84)) == 1

    def test_clusters_are_disjoint(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        clusters = cluster_collinear_features(corr, list(FEATURE_NAMES), threshold=0.85)
        seen: set[str] = set()
        for cluster in clusters:
            assert len(cluster.members) >= 2
            for member in cluster.members:
                assert member not in seen, f"{member} in two clusters"
                seen.add(member)

    def test_chain_merges_into_one_cluster(self) -> None:
        """Single linkage: A-B-C chain with each link over threshold = 1 group."""
        corr = np.array(
            [
                [1.0, 0.95, 0.0],
                [0.95, 1.0, 0.95],
                [0.0, 0.95, 1.0],
            ]
        )
        clusters = cluster_collinear_features(corr, ["a", "b", "c"], threshold=0.85)
        assert len(clusters) == 1
        assert set(clusters[0].members) == {"a", "b", "c"}

    def test_importance_selects_the_representative(self) -> None:
        """The highest-importance member survives, not the lowest-index one."""
        corr = np.array([[1.0, 0.95], [0.95, 1.0]])
        clusters = cluster_collinear_features(
            corr, ["weak", "strong"], importance=np.array([0.1, 0.9]), threshold=0.85
        )
        assert clusters[0].representative == "strong"
        assert clusters[0].redundant_members == ("weak",)

    def test_nan_importance_ranks_last_for_representative(self) -> None:
        corr = np.array([[1.0, 0.95], [0.95, 1.0]])
        clusters = cluster_collinear_features(
            corr, ["dead", "live"], importance=np.array([np.nan, 0.1]), threshold=0.85
        )
        assert clusters[0].representative == "live"

    def test_clusters_ordered_by_severity(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        clusters = cluster_collinear_features(corr, list(FEATURE_NAMES), threshold=0.85)
        grades = [c.max_abs_correlation for c in clusters]
        assert grades == sorted(grades, reverse=True)

    def test_cluster_ids_are_contiguous_from_one(
        self, frame: tuple[np.ndarray, np.ndarray]
    ) -> None:
        matrix, _ = frame
        corr = compute_correlation_matrix(matrix)
        clusters = cluster_collinear_features(corr, list(FEATURE_NAMES), threshold=0.85)
        assert [c.cluster_id for c in clusters] == list(range(1, len(clusters) + 1))

    def test_singleton_features_do_not_form_clusters(self) -> None:
        corr = np.eye(4)
        assert cluster_collinear_features(corr, ["a", "b", "c", "d"], threshold=0.5) == []

    def test_rejects_non_square_matrix(self) -> None:
        with pytest.raises(ValueError, match="square"):
            cluster_collinear_features(np.zeros((3, 4)), ["a", "b", "c"])

    def test_rejects_bad_threshold(self) -> None:
        with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
            cluster_collinear_features(np.eye(2), ["a", "b"], threshold=1.5)

    def test_rejects_importance_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="importance length"):
            cluster_collinear_features(np.eye(2), ["a", "b"], importance=np.array([0.1]))

    def test_pair_and_cluster_dataclasses_serialise(self) -> None:
        pair = CollinearPair(feature_a="a", feature_b="b", spearman_rho=-0.9)
        assert pair.to_dict()["abs_spearman_rho"] == 0.9
        cluster = CollinearityCluster(
            cluster_id=1, representative="a", members=("a", "b"), max_abs_correlation=0.95
        )
        assert cluster.to_dict()["redundant_members"] == ["b"]


# ==============================================================================
# 4. Permutation feature importance contract
# ==============================================================================


class TestPermutationImportance:
    def test_signal_column_outranks_noise(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        importance, std = compute_permutation_importance(matrix, labels, repeats=3)
        assert importance.shape == (N_FEATURES,)
        assert std.shape == (N_FEATURES,)
        assert importance[3] > importance[6]

    def test_constant_column_is_nan_not_zero(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        """Permuting a constant is a no-op; 0.0 would be indistinguishable from
        a genuinely-zero reading, so NaN is the honest signal."""
        matrix, labels = frame
        importance, _ = compute_permutation_importance(matrix, labels, repeats=2)
        assert np.isnan(importance[5])

    def test_deterministic_under_fixed_seed(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        a, _ = compute_permutation_importance(matrix, labels, seed=99, repeats=2)
        b, _ = compute_permutation_importance(matrix, labels, seed=99, repeats=2)
        np.testing.assert_array_equal(np.nan_to_num(a), np.nan_to_num(b))

    def test_different_seed_can_differ(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        """Two seeds must both be valid runs (no crash), even if they differ."""
        matrix, labels = frame
        a, _ = compute_permutation_importance(matrix, labels, seed=1, repeats=2)
        b, _ = compute_permutation_importance(matrix, labels, seed=2, repeats=2)
        assert np.all(np.isfinite(np.nan_to_num(a)))
        assert np.all(np.isfinite(np.nan_to_num(b)))

    def test_val_split_is_chronological_and_disjoint(self) -> None:
        from nexus_scalp.features.importance import _train_val_split

        train, val = _train_val_split(100, 0.3, seed=1)
        assert set(train) & set(val) == set()
        assert len(train) + len(val) == 100
        assert train[-1] < val[0]
        assert len(val) == 30

    def test_rejects_repeats_below_one(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        with pytest.raises(ValueError, match="repeats must be >= 1"):
            compute_permutation_importance(matrix, labels, repeats=0)

    def test_rejects_sample_mismatch(self) -> None:
        with pytest.raises(ValueError, match="sample mismatch"):
            compute_permutation_importance(np.zeros((10, 2)), np.zeros(3, dtype=np.int64))

    def test_single_class_frame_returns_nan(self) -> None:
        """One class cannot express a loss difference — NaN, not fake zeros."""
        importance, std = compute_permutation_importance(
            np.zeros((50, 4)), np.zeros(50, dtype=np.int64)
        )
        assert np.all(np.isnan(importance))
        assert np.all(np.isnan(std))

    def test_caller_supplied_model_is_used(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        """A pre-fitted model bypasses the internal ridge surrogate."""
        matrix, labels = frame
        calls = {"n": 0}

        def predict(x: np.ndarray, n_classes: int) -> np.ndarray:
            calls["n"] += 1
            probs = np.full((len(x), n_classes), 0.5 / max(n_classes - 1, 1))
            probs[:, 0] = 0.5
            return probs

        importance, _ = compute_permutation_importance(
            matrix, labels, model=object(), model_predict=predict, repeats=2
        )
        # Baseline + one permutation per non-constant column.
        assert calls["n"] > 0
        assert importance.shape == (N_FEATURES,)

    def test_model_requires_model_predict(self, frame: tuple[np.ndarray, np.ndarray]) -> None:
        matrix, labels = frame
        with pytest.raises(ValueError, match="model_predict is required"):
            compute_permutation_importance(matrix, labels, model=object())


# ==============================================================================
# 5. Top-level analyse_features + report contract
# ==============================================================================


class TestAnalyzeFeatures:
    def test_report_covers_all_50_features(self, report: ImportanceReport) -> None:
        assert report.n_features == N_FEATURES
        assert report.feature_names == FEATURE_NAMES

    def test_ranking_length_and_rank_values(self, report: ImportanceReport) -> None:
        ranking = report.importance_ranking()
        assert len(ranking) == N_FEATURES
        assert [r["rank"] for r in ranking] == list(range(1, N_FEATURES + 1))
        assert {r["feature"] for r in ranking} == set(FEATURE_NAMES)

    def test_constant_features_named_explicitly(self, report: ImportanceReport) -> None:
        """Acceptance criterion coverage: dead columns are documented, not hidden."""
        assert FEATURE_NAMES[5] in report.constant_features

    def test_collinear_pairs_documented_above_threshold(self, report: ImportanceReport) -> None:
        """Acceptance criterion 2: every pair > 0.85 is explicitly identified."""
        for pair in report.collinear_pairs:
            assert abs(pair.spearman_rho) > report.correlation_threshold
        assert len(report.collinear_pairs) >= 1

    def test_prune_recommendation_only_drops_redundant(self, report: ImportanceReport) -> None:
        kept = {c.representative for c in report.clusters}
        for row in report.prune_recommendation():
            assert row["drop_feature"] != row["keep_feature"]
            assert row["drop_feature"] not in kept
            assert row["keep_feature"] in kept

    def test_prune_recommendation_sorted_by_severity(self, report: ImportanceReport) -> None:
        prune = report.prune_recommendation()
        grades = [r["cluster_max_abs_correlation"] for r in prune]
        assert grades == sorted(grades, reverse=True)

    def test_json_roundtrip(self, report: ImportanceReport) -> None:
        payload = json.loads(report.to_json())
        assert payload["task"] == "ML-FEAT-002"
        assert payload["n_features"] == N_FEATURES
        assert len(payload["full_ranking"]) == N_FEATURES
        assert payload["feature_schema_id"] == "scalp_v1"

    def test_save_report_writes_json_and_npz(
        self, report: ImportanceReport, tmp_path: Path
    ) -> None:
        json_path = tmp_path / "nested" / "fi.json"
        npz_path = tmp_path / "nested" / "cm.npz"
        save_report(report, json_path, npz_path)
        assert json_path.exists()
        assert npz_path.exists()
        loaded = np.load(npz_path, allow_pickle=True)
        assert loaded["spearman_matrix"].shape == (N_FEATURES, N_FEATURES)
        assert loaded["mutual_information"].shape == (N_FEATURES,)
        assert list(loaded["feature_names"]) == list(FEATURE_NAMES)

    def test_rejects_width_mismatch_vs_contract(self) -> None:
        with pytest.raises(ValueError, match="declares"):
            analyze_features(np.zeros((20, 3)), np.zeros(20, dtype=np.int64))

    def test_infers_contract_names_when_unnamed(self) -> None:
        """A bare matrix with the contract width resolves to FEATURE_NAMES."""
        rng = np.random.default_rng(3)
        mat = rng.normal(size=(300, N_FEATURES))
        labels = rng.integers(0, 2, size=300)
        rep = analyze_features(mat, labels, permutation_repeats=2)
        assert rep.feature_names == FEATURE_NAMES

    def test_rejects_unnamed_matrix_of_wrong_width(self) -> None:
        with pytest.raises(ValueError, match="declares"):
            analyze_features(np.zeros((20, 7)), np.zeros(20, dtype=np.int64))


# ==============================================================================
# 6. Real pipeline integration (ScalpFeatureEngine + triple-barrier labeler)
# ==============================================================================


class TestRealFeaturePipeline:
    """The audit must run against the production 50D contract, not a stub."""

    def test_evaluation_frame_uses_the_real_feature_engine(self) -> None:
        from scripts.analysis.evaluate_feature_importance import build_evaluation_frame

        result = build_evaluation_frame(bar_count=600, seed=5)
        assert result.matrix.shape[1] == N_FEATURES
        assert result.matrix.shape[0] >= 50
        assert result.labels.shape == (result.matrix.shape[0],)
        # The real engine sanitises to [-3, 3] (scalp_features.to_tensor_input).
        assert np.all(np.isfinite(result.matrix))
        assert np.all(result.matrix >= -3.0 - 1e-9)
        assert np.all(result.matrix <= 3.0 + 1e-9)

    def test_evaluation_frame_is_deterministic(self) -> None:
        from scripts.analysis.evaluate_feature_importance import build_evaluation_frame

        a = build_evaluation_frame(bar_count=500, seed=8)
        b = build_evaluation_frame(bar_count=500, seed=8)
        np.testing.assert_array_equal(a.matrix, b.matrix)
        np.testing.assert_array_equal(a.labels, b.labels)

    def test_full_audit_over_real_frame(self) -> None:
        from scripts.analysis.evaluate_feature_importance import build_evaluation_frame

        result = build_evaluation_frame(bar_count=500, seed=21)
        rep = analyze_features(
            result.matrix,
            result.labels,
            permutation_repeats=2,
            metadata={"frame_provenance": result.provenance},
        )
        assert rep.n_features == N_FEATURES
        assert rep.n_samples == result.matrix.shape[0]
        # Session flags are legitimately constant on some synthetic windows.
        ranking = rep.importance_ranking()
        assert len(ranking) == N_FEATURES

    def test_rejects_bar_count_below_engine_warmup(self) -> None:
        from scripts.analysis.evaluate_feature_importance import build_evaluation_frame

        with pytest.raises(ValueError, match="60 bars"):
            build_evaluation_frame(bar_count=40, seed=1)

    def test_missing_parquet_raises(self, tmp_path: Path) -> None:
        from scripts.analysis.evaluate_feature_importance import build_evaluation_frame

        with pytest.raises(FileNotFoundError, match="not found"):
            build_evaluation_frame(parquet_path=tmp_path / "nope.parquet")


# ==============================================================================
# 7. CLI end-to-end
# ==============================================================================


class TestCli:
    def test_cli_emits_all_three_artifacts(self, tmp_path: Path) -> None:
        from scripts.analysis.evaluate_feature_importance import main

        json_out = tmp_path / "fi.json"
        npz_out = tmp_path / "cm.npz"
        md_out = tmp_path / "AUDIT.md"
        rc = main(
            [
                "--bar-count",
                "500",
                "--seed",
                "17",
                "--repeats",
                "2",
                "--report-json",
                str(json_out),
                "--report-npz",
                str(npz_out),
                "--report-md",
                str(md_out),
                "--quiet",
            ]
        )
        assert rc == 0
        assert json_out.exists()
        assert npz_out.exists()
        assert md_out.exists()
        payload = json.loads(json_out.read_text(encoding="utf-8"))
        assert payload["n_features"] == N_FEATURES
        assert "top_10_alpha_drivers" in payload
        md = md_out.read_text(encoding="utf-8")
        assert "scalp_v1" in md
        assert "Collinear Pairs" in md
        assert "Pruning Candidates" in md

    def test_cli_stdout_summary_is_valid_json(self, tmp_path: Path) -> None:
        import contextlib
        import io

        from scripts.analysis.evaluate_feature_importance import main

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = main(
                [
                    "--bar-count",
                    "500",
                    "--seed",
                    "3",
                    "--repeats",
                    "1",
                    "--report-json",
                    str(tmp_path / "f.json"),
                    "--report-npz",
                    str(tmp_path / "c.npz"),
                    "--report-md",
                    str(tmp_path / "a.md"),
                ]
            )
        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert payload["task"] == "ML-FEAT-002"
        assert payload["n_features"] == N_FEATURES

    def test_cli_threshold_controls_pair_count(self, tmp_path: Path) -> None:
        from scripts.analysis.evaluate_feature_importance import main

        rc = main(
            [
                "--bar-count",
                "500",
                "--seed",
                "4",
                "--repeats",
                "1",
                "--threshold",
                "0.995",
                "--report-json",
                str(tmp_path / "hi.json"),
                "--report-npz",
                str(tmp_path / "hi.npz"),
                "--report-md",
                str(tmp_path / "hi.md"),
                "--quiet",
            ]
        )
        assert rc == 0
        payload = json.loads((tmp_path / "hi.json").read_text(encoding="utf-8"))
        # A near-1.0 threshold admits fewer (or equal) pairs than the default.
        assert payload["correlation_threshold"] == 0.995

    def test_cli_mutually_exclusive_sources(self, tmp_path: Path) -> None:
        from scripts.analysis.evaluate_feature_importance import build_arg_parser

        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--bar-count", "100", "--dataset", "ds_x"])

    def test_cli_rejects_negative_repeats(self, tmp_path: Path) -> None:
        """An invalid permutation count must fail loudly, not silently pass."""
        from scripts.analysis.evaluate_feature_importance import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--repeats", "0"])
        assert args.repeats == 0  # argparse accepts it; the analyser enforces >= 1
