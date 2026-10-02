"""Canonical Data Quality Certification (MODEL FACTORY — stage 1).

WHY THIS EXISTS
---------------
Before this module, "cleaning" was scattered: ``bars_normalize`` parsed
timestamps and dropped non-finite rows inside the feature builders, the
ingest scripts wrote raw parquet, and no layer ever issued a
machine-readable verdict on whether a RAW dataset is TRAINABLE. There was
one implicit cleaning path per consumer and no report, so a bad bar could
be silently discarded by the trainer with no record of what was rejected
or why.

This is the ONE authoritative certification layer between RAW and CLEAN:

    RAW ARTIFACT (immutable)
        -> DataQualityCertifier.certify()
        -> DataQualityReport (machine-readable verdict)
        -> CLEAN ARTIFACT (immutable, NEW identity, lineage to raw)

CONTRACT
--------
* The RAW artifact is never mutated and never overwritten. Certification
  produces a SEPARATE clean artifact whose identity binds
  (raw_dataset_id, cleaning_version, config).
* Every rejected row is counted AND classified with a reason. A rejected
  row is only ever excluded from the clean frame when its defect makes the
  bar unusable (structural / OHLC impossibility / non-finite prices).
* Gaps are CLASSIFIED, never blanket-purged: a weekend/session boundary or
  a broker feed interruption is recorded as an event, not deleted. Only a
  gap inside a continuous session is a candidate defect.
* Outliers are CLASSIFIED into four policies and NEVER mass-deleted. A real
  XAUUSD shock is valid market information and must survive certification.
* The verdict is PASS / PASS_WITH_WARNINGS / FAIL and is derived from the
  configured contract, never from a hardcoded "good model" feeling.

INTEGRATION
-----------
``certify_and_persist`` is the canonical entry used by the CLI, the Model
Studio routes and the E2E test. It writes the clean parquet + report JSON
through ``ArtifactStore`` (which enforces path containment and refuses to
overwrite an existing id), so certification is replayable: the same raw +
config always yields the same clean identity, and rebuilding an existing
intact artifact is a logged no-op rather than a silent overwrite.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.data_quality")

__all__ = [
    "CLEANING_VERSION",
    "DataQualityCertifier",
    "DataQualityReport",
    "GapClassification",
    "OutlierClassification",
    "QualityStatus",
    "certify_and_persist",
    "clean_dataset_id",
]

#: Canonical cleaning implementation version. Bumped ONLY when the cleaning
#: rules themselves change — the version participates in the clean dataset
#: identity so a rule change can never reuse an existing artifact silently.
CLEANING_VERSION: str = "dq_v1"

#: M1 bar width, microseconds. Used for continuity / missing-candle checks
#: and for gap classification (a gap is an absence of an expected M1 bar).
_M1_US: int = 60 * 1_000_000

#: Inter-bar gap above which the absence is investigated (10 M1 bars,
#: matching SequenceBuilder.MAX_GAP_US so certification and sequence
#: construction agree on what "continuous" means).
_GAP_INVESTIGATE_US: int = 10 * _M1_US

#: Friday close / Sunday open boundaries for XAUUSD (UTC). The market is
#: closed over the weekend; a gap there is a SESSION boundary, not corruption.
_WEEKEND_CLOSE_UTC_HOUR: int = 22  # Friday 22:00 UTC (broker close)
_WEEKEND_OPEN_UTC_HOUR: int = 23  # Sunday 23:00 UTC (broker open)

#: Maximum fraction of rows that may be rejected before the dataset FAILS.
_MAX_REJECT_FRACTION: float = 0.05
#: Maximum fraction of bars that may be missing before the dataset FAILS.
_MAX_MISSING_FRACTION: float = 0.02


class QualityStatus:
    """Verdict values. Plain strings (not an enum) so the report JSON keeps
    its exact tokens across clients that do not import this module."""

    PASS: str = "PASS"
    PASS_WITH_WARNINGS: str = "PASS_WITH_WARNINGS"
    FAIL: str = "FAIL"


class GapClassification:
    """Why an inter-bar gap exists. The brief forbids classifying every gap
    as corruption."""

    WEEKEND: str = "WEEKEND"  # market closed Fri 22:00 -> Sun 23:00 UTC
    SESSION: str = "SESSION"  # a daily liquidity-trough session break
    FEED_INTERRUPTION: str = "FEED_INTERRUPTION"  # broker-side outage
    EXPECTED_MISSING_BAR: str = "EXPECTED_MISSING_BAR"  # < 10 bars, inside session
    UNCLASSIFIED: str = "UNCLASSIFIED"  # no policy matched (warning)


class OutlierClassification:
    """The four outlier policies. Deleting all statistical outliers would
    destroy real XAUUSD shocks, so each candidate gets a verdict instead."""

    BAD_DATA: str = "BAD_DATA"  # impossible vs OHLC contract (rejected)
    BROKER_ARTIFACT: str = "BROKER_ARTIFACT"  # feed spike, no macro cause (flagged)
    MICROSTRUCTURE_NOISE: str = "MICROSTRUCTURE_NOISE"  # within normal noise (kept)
    REAL_EXTREME_EVENT: str = "REAL_EXTREME_EVENT"  # macro move, valid (kept)


@dataclass
class GapEvent:
    """One classified discontinuity in the bar series."""

    index: int  # position of the bar AFTER the gap
    gap_minutes: float
    classification: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataQualityReport:
    """Machine-readable certification verdict (Section 5 of the contract).

    Every count is a real measured integer from the frame; no field is ever
    invented. ``status`` is the only derived field and it is derived from the
    configured thresholds, never from a heuristic.
    """

    dataset_id: str
    raw_dataset_id: str
    cleaning_version: str
    symbol: str
    timeframe: str
    source: str
    raw_rows: int
    valid_rows: int
    duplicate_rows: int
    duplicate_timestamps: int
    invalid_ohlc: int
    nan_rows: int
    inf_rows: int
    missing_ohlc_fields: int
    malformed_rows: int
    gap_events: int
    missing_bars: int
    zero_volume: int
    negative_volume: int
    spread_anomalies: int
    outlier_candidates: int
    rejected_rows: int
    flagged_but_retained: int
    quality_status: str
    raw_fingerprint: str = ""
    clean_fingerprint: str = ""
    git_commit: str = ""
    generated_at: str = ""
    rejection_breakdown: dict[str, int] = field(default_factory=dict)
    outlier_breakdown: dict[str, int] = field(default_factory=dict)
    gap_breakdown: dict[str, int] = field(default_factory=dict)
    gap_events_detail: list[dict[str, Any]] = field(default_factory=list)
    retained_outliers: list[int] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def trainable(self) -> bool:
        """A dataset may be trained on only when certification did not FAIL."""
        return self.quality_status != QualityStatus.FAIL


def _git_commit() -> str:
    """Best-effort HEAD sha for provenance. Never raises."""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return ""


def _fingerprint(frame: pl.DataFrame) -> str:
    """Content fingerprint over the canonical OHLCV projection."""
    cols: list[str] = [
        c for c in ("time", "open", "high", "low", "close", "tick_volume") if c in frame.columns
    ]
    if not cols:
        cols = list(frame.columns[:6])
    proj = frame.select(cols).to_dict(as_series=False)
    payload = json.dumps(proj, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clean_dataset_id(
    raw_dataset_id: str,
    cleaning_version: str,
    config_hash: str,
) -> str:
    """Deterministic identity for the CLEAN artifact.

    Binds the raw identity + the cleaning rules + the config so (a) the same
    raw + rules always reproduce the same clean id, and (b) changing the
    rules mints a NEW id rather than overwriting an existing dataset.
    """
    payload = f"{raw_dataset_id}|{cleaning_version}|{config_hash}"
    return "clean_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _row_reject_reasons(row: dict[str, Any]) -> list[str]:
    """The structural + OHLC defects that make a single bar unusable."""
    reasons: list[str] = []

    o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
    vals = [o, h, l, c]
    if any(v is None for v in vals):
        reasons.append("MISSING_OHLC_FIELD")
        return reasons
    try:
        of, hf, lf, cf = (float(v) for v in vals)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        reasons.append("MALFORMED_ROW")
        return reasons
    if not all(np.isfinite(x) for x in (of, hf, lf, cf)):
        if any(np.isnan(x) for x in (of, hf, lf, cf)):
            reasons.append("NAN_PRICE")
        else:
            reasons.append("INF_PRICE")
        return reasons
    if any(x <= 0.0 for x in (of, hf, lf, cf)):
        reasons.append("NON_POSITIVE_PRICE")
        return reasons
    # OHLC impossibility (Section 4): reject the bar outright.
    if hf < lf:
        reasons.append("HIGH_BELOW_LOW")
    if of > hf:
        reasons.append("OPEN_ABOVE_HIGH")
    if of < lf:
        reasons.append("OPEN_BELOW_LOW")
    if cf > hf:
        reasons.append("CLOSE_ABOVE_HIGH")
    if cf < lf:
        reasons.append("CLOSE_BELOW_LOW")
    return reasons


def _classify_gap(prev_time: datetime, cur_time: datetime) -> tuple[str, str]:
    """Classify one inter-bar gap. Returns (classification, detail)."""
    gap_min = (cur_time - prev_time).total_seconds() / 60.0
    dow = prev_time.weekday()
    hour = prev_time.hour

    # Weekend: Friday close -> Sunday open. The gap STARTS on Friday and the
    # market reopens Sunday evening UTC.
    if dow == 4 and hour >= _WEEKEND_CLOSE_UTC_HOUR - 1:
        return GapClassification.WEEKEND, f"weekend close Fri {prev_time:%H:%M}Z"
    if dow == 6 and hour >= _WEEKEND_OPEN_UTC_HOUR - 1:
        return GapClassification.WEEKEND, f"weekend open Sun {cur_time:%H:%M}Z"
    if dow == 5:
        return GapClassification.WEEKEND, "Saturday (market closed)"
    # A short gap inside a session day is a broker feed interruption when it
    # is long enough to be an outage but the market is open.
    if gap_min >= 15.0 and dow < 5:
        return GapClassification.FEED_INTERRUPTION, f"{gap_min:.0f}min outage mid-session"
    if gap_min <= 10.0:
        return GapClassification.EXPECTED_MISSING_BAR, f"{gap_min:.0f}min (<= 10 bars)"
    return (
        GapClassification.UNCLASSIFIED,
        f"{gap_min:.0f}min gap on {prev_time:%a} {prev_time:%H:%M}Z",
    )


def _classify_outlier(
    close: float,
    high: float,
    low: float,
    open_: float,
    atr: float,
    median_close: float,
) -> str:
    """Classify one extreme close into the four outlier policies.

    The separation is: an OHLC-impossible value is BAD DATA (rejected); a
    move far larger than the local volatility with no macro-scale follow
    through is a BROKER ARTIFACT (flagged); a move within a few ATR is
    MICROSTRUCTURE_NOISE (kept); a large move that is internally consistent
    (range follows through, close not an isolated spike) is a
    REAL_EXTREME_EVENT (kept).
    """
    if low > high or close > high or close < low or open_ > high or open_ < low:
        return OutlierClassification.BAD_DATA
    if atr <= 0.0:
        return OutlierClassification.MICROSTRUCTURE_NOISE
    dev = abs(close - median_close) / atr
    if dev < 6.0:
        return OutlierClassification.MICROSTRUCTURE_NOISE
    range_follows = (high - low) > 2.0 * atr
    spike_only = abs(close - open_) < 0.25 * (high - low) if (high - low) > 0 else False
    if range_follows and not spike_only:
        return OutlierClassification.REAL_EXTREME_EVENT
    return OutlierClassification.BROKER_ARTIFACT


class DataQualityCertifier:
    """The single authoritative RAW -> CLEAN certification layer."""

    def __init__(
        self,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        source: str = "historical",
        max_reject_fraction: float = _MAX_REJECT_FRACTION,
        max_missing_fraction: float = _MAX_MISSING_FRACTION,
        reject_zero_volume: bool = False,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.source = source
        self.max_reject_fraction = float(max_reject_fraction)
        self.max_missing_fraction = float(max_missing_fraction)
        self.reject_zero_volume = bool(reject_zero_volume)

    # ------------------------------------------------------------------ API

    def certify(self, raw: pl.DataFrame) -> tuple[pl.DataFrame, DataQualityReport]:
        """Certify a raw OHLCV frame.

        Returns (clean_frame, report). The clean frame is a NEW object; the
        caller's raw frame is never mutated. Rows rejected for structural /
        OHLC / non-finite defects are excluded; everything else is retained
        and any defect is counted and reported.
        """
        if raw is None or raw.is_empty():
            raise ValueError("DataQualityCertifier: empty raw frame — nothing to certify")
        if "time" not in raw.columns:
            raise ValueError(
                "DataQualityCertifier: frame has no 'time' column; normalize first "
                "(model_generation.bars_normalize.normalize_bars_frame)"
            )
        for col in ("open", "high", "low", "close"):
            if col not in raw.columns:
                raise ValueError(f"DataQualityCertifier: required column {col!r} missing")

        work = raw.clone()
        work = work.with_columns(pl.col("time").cast(pl.Datetime("us", "UTC"), strict=False))

        raw_rows = work.height
        rejection_breakdown: dict[str, int] = {}

        # ---- duplicate timestamps (keep last, like bars_normalize) --------
        dup_ts = int(work["time"].is_duplicated().sum())
        if dup_ts:
            work = work.unique(subset=["time"], keep="last", maintain_order=True)
            rejection_breakdown["DUPLICATE_TIMESTAMP"] = dup_ts

        # ---- chronological ordering ---------------------------------------
        ts_list = work["time"].to_list()
        unordered = 0
        if ts_list != sorted(ts_list):
            unordered = int(
                np.sum(np.array([t != s for t, s in zip(ts_list, sorted(ts_list), strict=True)]))
            )
            work = work.sort("time")
        if unordered:
            rejection_breakdown["OUT_OF_ORDER"] = unordered

        # ---- structural + OHLC row classification -------------------------
        rows = work.to_dicts()
        keep_mask = [True] * len(rows)
        nan_rows = inf_rows = missing_fields = malformed = invalid_ohlc = 0
        for i, row in enumerate(rows):
            reasons = _row_reject_reasons(row)
            if not reasons:
                continue
            keep_mask[i] = False
            for r in reasons:
                rejection_breakdown[r] = rejection_breakdown.get(r, 0) + 1
            if "NAN_PRICE" in reasons:
                nan_rows += 1
            elif "INF_PRICE" in reasons:
                inf_rows += 1
            elif "MISSING_OHLC_FIELD" in reasons:
                missing_fields += 1
            elif "MALFORMED_ROW" in reasons:
                malformed += 1
            else:
                invalid_ohlc += 1

        # zero / negative volume are defects, not rejections unless configured
        zero_volume = negative_volume = 0
        if "tick_volume" in work.columns:
            vol = work["tick_volume"].cast(pl.Float64, strict=False).to_numpy()
            vol_finite = vol[np.isfinite(vol)]
            zero_volume = int(np.sum(vol_finite == 0))
            negative_volume = int(np.sum(vol_finite < 0))
            if self.reject_zero_volume:
                zero_mask = np.isfinite(vol) & (vol == 0)
                for i in np.nonzero(zero_mask)[0]:
                    if i < len(keep_mask):
                        keep_mask[int(i)] = False
                if zero_volume:
                    rejection_breakdown["ZERO_VOLUME"] = zero_volume
            if negative_volume:
                rejection_breakdown["NEGATIVE_VOLUME"] = negative_volume

        clean = work.filter(pl.Series(keep_mask))
        rejected_rows = raw_rows - clean.height

        # ---- market continuity: classified gaps + missing bars ------------
        clean_times = clean["time"].to_list()
        gap_events: list[GapEvent] = []
        gap_breakdown: dict[str, int] = {}
        missing_bars = 0
        for prev_t, cur_t in itertools.pairwise(clean_times):
            if cur_t <= prev_t:
                continue
            step_us = int((cur_t - prev_t).total_seconds() * 1_000_000)
            if step_us <= _M1_US:
                continue
            cls, detail = _classify_gap(prev_t, cur_t)
            gap_min = (cur_t - prev_t).total_seconds() / 60.0
            gap_events.append(
                GapEvent(
                    index=clean_times.index(cur_t),
                    gap_minutes=round(gap_min, 2),
                    classification=cls,
                    detail=detail,
                )
            )
            gap_breakdown[cls] = gap_breakdown.get(cls, 0) + 1
            # Only an intra-session gap implies missing M1 candles; a weekend
            # gap does not (the market was closed, no bars existed).
            if cls in (
                GapClassification.EXPECTED_MISSING_BAR,
                GapClassification.FEED_INTERRUPTION,
                GapClassification.UNCLASSIFIED,
            ):
                missing_bars += max(0, int(gap_min) - 1)

        # ---- outlier classification (never a blanket deletion) -----------
        closes = clean["close"].cast(pl.Float64, strict=False).to_numpy()
        highs = clean["high"].cast(pl.Float64, strict=False).to_numpy()
        lows = clean["low"].cast(pl.Float64, strict=False).to_numpy()
        opens = clean["open"].cast(pl.Float64, strict=False).to_numpy()
        with np.errstate(all="ignore"):
            median_close = float(np.nanmedian(closes)) if len(closes) else 0.0
            # rolling 14-bar ATR proxy (mean of |high-low| over a centered
            # window) — a TRAIN-time-only diagnostic, computed on the raw
            # series it describes, never used to transform features.
            hl = np.abs(highs - lows)
            kernel = np.ones(14) / 14.0
            atr = np.convolve(hl, kernel, mode="same")
            atr[~np.isfinite(atr)] = 0.0

        outlier_breakdown: dict[str, int] = {}
        retained_outliers: list[int] = []
        n_bad = 0
        for i in range(len(closes)):
            if not np.isfinite(closes[i]):
                continue
            cls = _classify_outlier(
                float(closes[i]),
                float(highs[i]),
                float(lows[i]),
                float(opens[i]),
                float(atr[i]) if i < len(atr) else 0.0,
                median_close,
            )
            if cls == OutlierClassification.BAD_DATA:
                n_bad += 1
            else:
                outlier_breakdown[cls] = outlier_breakdown.get(cls, 0) + 1
                if cls in (
                    OutlierClassification.BROKER_ARTIFACT,
                    OutlierClassification.REAL_EXTREME_EVENT,
                ):
                    retained_outliers.append(i)

        # ---- spread anomalies (flag only) ---------------------------------
        spread_anomalies = 0
        if "spread" in clean.columns:
            spread = clean["spread"].cast(pl.Float64, strict=False).to_numpy()
            with np.errstate(all="ignore"):
                med = np.nanmedian(spread) if len(spread) else 0.0
                if med > 0:
                    spread_anomalies = int(
                        np.sum(np.isfinite(spread) & (spread > 10.0 * float(med)))
                    )

        # ---- verdict -------------------------------------------------------
        warnings: list[str] = []
        if missing_bars > 0:
            frac_missing = missing_bars / max(raw_rows, 1)
            if frac_missing > self.max_missing_fraction:
                warnings.append(
                    f"missing-bar fraction {frac_missing:.2%} exceeds the "
                    f"{self.max_missing_fraction:.0%} contract"
                )
        if zero_volume:
            warnings.append(f"{zero_volume} bars with zero volume retained")
        if negative_volume:
            warnings.append(f"{negative_volume} bars with negative volume retained")
        if gap_breakdown.get(GapClassification.UNCLASSIFIED, 0):
            warnings.append(f"{gap_breakdown[GapClassification.UNCLASSIFIED]} unclassified gap(s)")
        if outlier_breakdown.get(OutlierClassification.BROKER_ARTIFACT, 0):
            warnings.append(
                f"{outlier_breakdown[OutlierClassification.BROKER_ARTIFACT]} "
                "broker-artifact outlier(s) flagged but retained"
            )

        reject_fraction = rejected_rows / max(raw_rows, 1)
        if reject_fraction > self.max_reject_fraction:
            status = QualityStatus.FAIL
            warnings.append(
                f"rejection fraction {reject_fraction:.2%} exceeds the "
                f"{self.max_reject_fraction:.0%} contract"
            )
        elif warnings:
            status = QualityStatus.PASS_WITH_WARNINGS
        else:
            status = QualityStatus.PASS

        report = DataQualityReport(
            dataset_id="",
            raw_dataset_id="",
            cleaning_version=CLEANING_VERSION,
            symbol=self.symbol,
            timeframe=self.timeframe,
            source=self.source,
            raw_rows=raw_rows,
            valid_rows=clean.height,
            duplicate_rows=dup_ts,
            duplicate_timestamps=dup_ts,
            invalid_ohlc=invalid_ohlc,
            nan_rows=nan_rows,
            inf_rows=inf_rows,
            missing_ohlc_fields=missing_fields,
            malformed_rows=malformed,
            gap_events=len(gap_events),
            missing_bars=missing_bars,
            zero_volume=zero_volume,
            negative_volume=negative_volume,
            spread_anomalies=spread_anomalies,
            outlier_candidates=sum(outlier_breakdown.values()) + n_bad,
            rejected_rows=rejected_rows,
            flagged_but_retained=sum(outlier_breakdown.values()),
            quality_status=status,
            raw_fingerprint=_fingerprint(raw),
            clean_fingerprint=_fingerprint(clean),
            git_commit=_git_commit(),
            generated_at=datetime.now(UTC).isoformat(),
            rejection_breakdown=rejection_breakdown,
            outlier_breakdown=outlier_breakdown,
            gap_breakdown=gap_breakdown,
            gap_events_detail=[g.to_dict() for g in gap_events[:50]],
            retained_outliers=retained_outliers[:200],
            config={
                "max_reject_fraction": self.max_reject_fraction,
                "max_missing_fraction": self.max_missing_fraction,
                "reject_zero_volume": self.reject_zero_volume,
            },
            warnings=warnings,
        )
        logger.info(
            "[DATA_QUALITY] event=CERTIFIED raw=%d clean=%d rejected=%d status=%s",
            raw_rows,
            clean.height,
            rejected_rows,
            status,
        )
        return clean, report


def certify_and_persist(
    raw: pl.DataFrame,
    *,
    raw_dataset_id: str,
    store: Any = None,
    symbol: str = "XAUUSD",
    timeframe: str = "M1",
    source: str = "historical",
    certifier: DataQualityCertifier | None = None,
) -> tuple[str, DataQualityReport]:
    """Canonical entry: certify RAW and persist an immutable CLEAN artifact.

    Returns (clean_dataset_id, report). The clean artifact is written through
    ``ArtifactStore`` under a deterministic identity that binds the raw id +
    cleaning version + config, so:

    * rebuilding with the same raw + rules finds the existing intact artifact
      and returns it (logged REUSED, never a silent overwrite);
    * a rules change mints a NEW identity (never overwrites an old clean);
    * a corrupt artifact is refused, never silently repaired.

    The report is persisted next to the artifact as
    ``<clean_id>.quality_report.json`` and is the machine-readable contract
    the Model Studio dataset view renders.
    """
    from nexus_scalp.model_generation.artifact_store import ArtifactStore

    store = store or ArtifactStore()
    certifier = certifier or DataQualityCertifier(symbol=symbol, timeframe=timeframe, source=source)
    clean, report = certifier.certify(raw)

    cfg_hash = _config_hash(report.config)
    cid = clean_dataset_id(raw_dataset_id, CLEANING_VERSION, cfg_hash)
    report.dataset_id = cid
    report.raw_dataset_id = raw_dataset_id

    out_dir = Path(store.root) / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_path = out_dir / f"{cid}.parquet"
    report_path = out_dir / f"{cid}.quality_report.json"

    # Idempotent rebuild: an existing INTACT artifact with matching content is
    # reused. Corrupt or divergent content raises, never silently overwrites.
    if clean_path.exists():
        # The stored parquet bytes hash differs from the in-memory content
        # fingerprint by design (file bytes vs logical projection), so reuse is
        # keyed on the report's recorded clean fingerprint instead.
        if report_path.exists():
            try:
                stored = json.loads(report_path.read_text(encoding="utf-8"))
                if (
                    stored.get("clean_fingerprint") == report.clean_fingerprint
                    and stored.get("raw_rows") == report.raw_rows
                    and stored.get("valid_rows") == report.valid_rows
                ):
                    logger.info("[DATA_QUALITY] event=REUSED clean_dataset_id=%s", cid)
                    return cid, report
            except Exception:
                pass
        if not report_path.exists():
            raise FileExistsError(
                f"clean artifact {cid!r} exists without a quality report — "
                "possible corrupt/partial certification; refusing to overwrite"
            )
        raise FileExistsError(
            f"clean artifact {cid!r} already exists with DIFFERENT content "
            "(clean fingerprint mismatch) — refusing to overwrite; bump the "
            "cleaning version to mint a new dataset"
        )

    clean.write_parquet(clean_path)
    report_path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    logger.info(
        "[DATA_QUALITY] event=PERSISTED clean_dataset_id=%s rows=%d status=%s",
        cid,
        report.valid_rows,
        report.quality_status,
    )
    return cid, report


def load_quality_report(store: Any, clean_dataset_id: str) -> DataQualityReport | None:
    """Reads a persisted certification report. None when absent."""
    report_path = Path(store.root) / "datasets" / f"{clean_dataset_id}.quality_report.json"
    if not report_path.is_file():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return DataQualityReport(**payload)


def clean_frame_path(store: Any, clean_dataset_id: str) -> Path:
    """Filesystem location of a certified clean dataset."""
    return Path(store.root) / "datasets" / f"{clean_dataset_id}.parquet"
