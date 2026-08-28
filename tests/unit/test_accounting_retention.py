import pytest

from nexus_scalp.accounting.retention import (
    cohort_capture_report,
    giveback,
    giveback_ratio,
    mfe_capture_ratio,
)


class TestGivebackRatio:
    def test_normal_giveback(self):
        # MFE is 100, we realized 60. We gave back 40. Ratio is 40 / 100 = 0.4
        assert giveback_ratio(mfe=100.0, realized_profit=60.0) == pytest.approx(0.4)

    def test_full_giveback(self):
        # MFE is 100, realized is 0 (breakeven). Ratio is 100 / 100 = 1.0
        assert giveback_ratio(mfe=100.0, realized_profit=0.0) == pytest.approx(1.0)

    def test_loss_after_mfe(self):
        # MFE is 100, but ended up in a 50 loss. Giveback is 150. Ratio 150 / 100 = 1.5
        assert giveback_ratio(mfe=100.0, realized_profit=-50.0) == pytest.approx(1.5)

    def test_over_capture_impossible_but_handled(self):
        # Technically realized shouldn't exceed MFE, but if it does (e.g. data anomaly):
        # MFE 100, realized 120. Giveback is -20. Ratio is -0.2
        assert giveback_ratio(mfe=100.0, realized_profit=120.0) == pytest.approx(-0.2)

    def test_zero_mfe_returns_none(self):
        # MFE is 0, nothing was gained at peak
        assert giveback_ratio(mfe=0.0, realized_profit=-10.0) is None

    def test_negative_mfe_returns_none(self):
        # MFE is negative (trade instantly went against us)
        assert giveback_ratio(mfe=-20.0, realized_profit=-50.0) is None

    def test_near_zero_mfe(self):
        # MFE is very small but > 0. Tested to ensure division by epsilon works
        val = giveback_ratio(mfe=1e-10, realized_profit=0.0)
        # (1e-10 - 0) / max(1e-10, 1e-9) = 1e-10 / 1e-9 = 0.1
        assert val == pytest.approx(0.1)


class TestMfeCaptureRatio:
    def test_normal_capture(self):
        assert mfe_capture_ratio(mfe=100.0, realized_profit=60.0) == pytest.approx(0.6)

    def test_zero_or_negative_mfe(self):
        assert mfe_capture_ratio(mfe=0.0, realized_profit=0.0) is None
        assert mfe_capture_ratio(mfe=-10.0, realized_profit=-20.0) is None


class TestGiveback:
    def test_normal_giveback(self):
        assert giveback(mfe=100.0, realized_profit=80.0) == pytest.approx(20.0)

    def test_zero_or_negative_mfe(self):
        assert giveback(mfe=0.0, realized_profit=0.0) is None
        assert giveback(mfe=-10.0, realized_profit=-20.0) is None


class TestCohortCaptureReport:
    def test_empty_records(self):
        report = cohort_capture_report([])
        assert report["sample_trades"] == 0
        assert report["profitable_trades"] == 0
        assert report["avg_capture_ratio"] is None
        assert report["avg_giveback_ratio"] is None
        assert report["total_mfe"] is None

    def test_valid_records(self):
        records = [
            (80.0, 100.0),  # capture 0.8, giveback 0.2
            (0.0, 50.0),  # capture 0.0, giveback 1.0
            (-50.0, 50.0),  # capture -1.0, giveback 2.0
            (-20.0, -10.0),  # MFE < 0, excluded from ratios
        ]
        report = cohort_capture_report(records)
        assert report["sample_trades"] == 4
        assert report["profitable_trades"] == 1  # only 80.0 is > 0

        # Capture ratios: 0.8, 0.0, -1.0. Avg: (0.8 + 0.0 - 1.0) / 3 = -0.0667
        assert report["avg_capture_ratio"] == pytest.approx(-0.0667, abs=1e-3)
        assert report["worst_capture_ratio"] == pytest.approx(-1.0)

        # Giveback ratios: 0.2, 1.0, 2.0. Avg: (0.2 + 1.0 + 2.0) / 3 = 1.0667
        assert report["avg_giveback_ratio"] == pytest.approx(1.0667, abs=1e-3)

        # total_mfe should only sum positive MFEs: 100 + 50 + 50 = 200
        assert report["total_mfe"] == 200.0

        # total_realized = 80 + 0 - 50 - 20 = 10
        assert report["total_realized"] == 10.0
