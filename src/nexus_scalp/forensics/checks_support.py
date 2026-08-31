"""Shared read-only helpers for the forensic check slices.

WHERE/WHY: the private helpers every checks_* domain module calls — sqlite
read-only connectors, result constructors (_ok/_unknown/_safe), path/integrity
resolvers, config/news/shadow state probes, statistics shims. Extracted
verbatim from the former monolith ``checks.py`` (CHG-0032 Step 2).

BOUNDARY: pure helpers only — no check functions, no engine wiring, no I/O
beyond the read-only sqlite/file probes shown.

USED BY: checks_features / checks_accounting / checks_news /
checks_governance / checks_observability; re-exported by the ``checks``
facade for historical private-symbol access.

DO-NOT-PUT-HERE: any ``check_*`` function (they live in the domain modules).
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
    new_correlation_id,
)


def _ro_connect(path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    """Opens a SQLite connection in strict read-only URI mode."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _row_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return None


def _iso_age_seconds(iso: str | None) -> float | None:
    """Age in seconds of an ISO timestamp vs now; None on parse failure."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except (TypeError, ValueError):
        return None


def _fmt(ts: str | None) -> str:
    return str(ts or "")


def _safe(fn: Callable[[], CheckResult]) -> CheckResult:
    """Failure isolation: a raised check becomes UNKNOWN with evidence (never PASS)."""
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        return CheckResult(
            check_id="CHECK-RAISED",
            status=HealthStatus.UNKNOWN,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            evidence=f"check raised: {exc!r}",
            observed={"error": str(exc)},
            expected="check completes without raising",
            correlation_id=new_correlation_id(),
            detail="CHECK_RAISED",
        )
    # stamp duration (frozen dataclass -> replace)
    return CheckResult(
        check_id=result.check_id,
        status=result.status,
        timestamp=result.timestamp,
        duration_ms=(time.perf_counter() - start) * 1000.0,
        evidence=result.evidence,
        observed=result.observed,
        expected=result.expected,
        correlation_id=result.correlation_id,
        detail=result.detail,
    )


def _ok(
    check_id: str, evidence: str, observed: dict[str, Any] | None = None, expected: str = ""
) -> CheckResult:
    return CheckResult(
        check_id, HealthStatus.PASS, evidence=evidence, observed=observed or {}, expected=expected
    )


def _unknown(
    check_id: str, evidence: str, observed: dict[str, Any] | None = None, expected: str = ""
) -> CheckResult:
    """UNKNOWN is reported whenever health cannot be determined (§5)."""
    return CheckResult(
        check_id,
        HealthStatus.UNKNOWN,
        evidence=evidence,
        observed=observed or {},
        expected=expected,
    )


# ---------------------------------------------------------------------------
# Feature contract checks (INV-70D-001..006)
# ---------------------------------------------------------------------------

#: Canonical 70D family layout (indices). 50D/60D schemas are their prefix.
BASE_INDICES = range(0, 50)
NEWS_INDICES = range(50, 60)
LIQUIDITY_INDICES = range(60, 70)

#: Expected name at the first Liquidity index per the 70D contract snapshot.
EXPECTED_LIQUIDITY_INDEX_60_NAME = "bsl_distance_atr"


def _registered_families() -> dict[str, dict[str, Any]]:
    """Feature registry snapshot: id -> {dimension, supersedes, description}."""
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        out: dict[str, dict[str, Any]] = {}
        for s in FEATURE_SCHEMAS.list_schemas():
            out[s.schema_id] = {
                "dimension": s.dimension,
                "supersedes": s.supersedes,
                "description": s.description,
            }
        return out
    except Exception:
        return {}


def _champion_artifact_info() -> dict[str, Any]:
    """Best-effort champion artifact inventory (files + hashes, read-only)."""
    info: dict[str, Any] = {"found": False}
    try:
        from nexus_scalp.release.paths import app_data_root  # type: ignore[import-not-found]

        root = Path(app_data_root() if callable(app_data_root) else app_data_root)
    except Exception:
        root = Path.cwd()
    # BUG-166: the config-driven artifact probe imported the nonexistent
    # `nexus_scalp.configuration.loader` module, so the silent except
    # always fired and the check fell back to the hardcoded v1.0.0
    # artifact. The CURRENT champion (config-driven 70d_liquidity) then
    # looked "missing on disk" against its registry fingerprint -> false
    # CHECK-GOV-02 CRITICAL -> deploy-gate BLOCK on a healthy system.
    # Resolve the path the way the runtime does: AppConfig.load_from_yaml
    # (base.yaml next to the workspace root), falling back to defaults.
    artifact_path: str = ""
    try:
        from nexus_scalp.configuration.config import AppConfig
        from nexus_scalp.release.paths import get_user_config_path

        # BUG-166: same precedence as the runtime engine (cli start):
        # user config (nexus.yaml) first, then the repo base.yaml
        # template, then schema defaults. Probing only the template made
        # every user-config-driven deployment read the wrong artifact.
        for _cfg_src in (get_user_config_path(), root / "configs" / "base.yaml"):
            if not Path(_cfg_src).exists():
                continue
            artifact_path = str(AppConfig.load_from_yaml(Path(_cfg_src)).model.model_artifact_path)
            break
    except Exception:
        artifact_path = ""
    candidates: list[Path] = []
    if artifact_path:
        candidates.append(Path(artifact_path))
    # Well-known champion dir.
    candidates.append(root / "artifacts" / "models" / "scalp" / "XAUUSD" / "v1.0.0" / "model.pt")
    candidates.append(Path("artifacts") / "models" / "scalp" / "XAUUSD" / "v1.0.0" / "model.pt")
    seen: set[Path] = set()
    for cand in candidates:
        try:
            p = cand.resolve()
        except Exception:
            p = cand
        if p in seen:
            continue
        seen.add(p)
        if p.is_file():
            scaler = p.with_name("model.scaler.npz")
            info["found"] = True
            info["path"] = str(p)
            info["exists"] = True
            info["size"] = p.stat().st_size
            info["scaler_exists"] = scaler.is_file()
            info["scaler_size"] = scaler.stat().st_size if scaler.is_file() else 0
            info["artifact_hash"] = _sha256(p)[:16]
            if scaler.is_file():
                info["scaler_hash"] = _sha256(scaler)[:16]
            break
    return info


def _sha256(path: Path) -> str:
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _probe_vector(engine: Any, bars: list[list]) -> list[float] | None:
    """Calls the feature engine's deterministic hook; returns None on failure."""
    for name in ("to_tensor_input", "compute_features", "produce_vector", "features_from_bars"):
        fn = getattr(engine, name, None)
        if callable(fn):
            try:
                result = fn(bars)
                if isinstance(result, list):
                    return [float(v) for v in result]
                if hasattr(result, "tolist"):
                    return [float(v) for v in result.tolist()[0]]
                return None
            except Exception:
                continue
    return None


def _audit_path() -> Path:
    p = Path("artifacts") / "audit.db"
    return p if p.exists() else Path("artifacts/audit.db")


def _broker_ledger_divergence() -> dict[str, Any]:
    """Read-only broker-vs-ledger reconciliation summary."""
    out: dict[str, Any] = {"available": False}
    path = _audit_path()
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_broker_trades" not in tables or "audit_ledger" not in tables:
                return out
            broker_count = _row_count(conn, "audit_broker_trades") or 0
            ledger_count = _row_count(conn, "audit_ledger") or 0
            out["available"] = True
            out["broker_trades"] = broker_count
            out["ledger_rows"] = ledger_count
            out["unmatched_ratio"] = (
                round((broker_count - ledger_count) / broker_count, 4) if broker_count else 0.0
            )
            # realized PnL aggregates
            try:
                out["broker_pnl_sum"] = round(
                    float(
                        conn.execute(
                            "SELECT COALESCE(SUM(net_pnl), 0) FROM audit_broker_trades"
                        ).fetchone()[0]
                    ),
                    2,
                )
            except sqlite3.Error:
                out["broker_pnl_sum"] = None
            try:
                out["ledger_pnl_sum"] = round(
                    float(
                        conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM audit_ledger").fetchone()[0]
                    ),
                    2,
                )
            except sqlite3.Error:
                out["ledger_pnl_sum"] = None
        finally:
            conn.close()
    except Exception:
        out["available"] = False
    return out


def _parse_close_time(value: str | None) -> datetime | None:
    """Parses ledger close_time to an aware datetime; None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


def _integrity_for(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": path.exists()}
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            out["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            out["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            out["tables"] = len(_table_names(conn))
            out["size_bytes"] = path.stat().st_size
            # unexpected tables: sqlite_sequence is expected; anything else is a delta
            tables = _table_names(conn)
            out["unexpected_tables"] = sorted(
                t
                for t in tables
                if t not in {"sqlite_sequence", "schema_meta", "schema_migrations"}
            )
            meta = (
                conn.execute("SELECT key, value FROM schema_meta").fetchall()
                if "schema_meta" in tables
                else []
            )
            out["schema_meta"] = dict(meta)
            wal = Path(str(path) + "-wal")
            out["wal_size_bytes"] = wal.stat().st_size if wal.exists() else 0
            out["wal_present"] = wal.exists()
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _last_feature_vectors(conn: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
    """Reads the most recent feature_vectors (candle_intel) or experience snapshots."""
    out: list[dict[str, Any]] = []
    tables = _table_names(conn)
    if "feature_vectors" in tables:
        try:
            cols = [d[0] for d in conn.execute("SELECT * FROM feature_vectors LIMIT 0").description]
            rows = conn.execute(
                f"SELECT * FROM feature_vectors ORDER BY rowid DESC LIMIT {limit}"
            ).fetchall()
            for r in rows:
                out.append(dict(zip(cols, r, strict=False)))
        except sqlite3.Error:
            pass
    return out


def _extract_feature_columns(row: dict[str, Any]) -> list[float] | None:
    """Extracts float columns feat_0..feat_{n-1} from a row; None on absence."""
    vals: list[float] = []
    i = 0
    while True:
        key = f"feat_{i}"
        if key not in row:
            break
        try:
            vals.append(float(row[key]))
        except (TypeError, ValueError):
            return None
        i += 1
    return vals or None


def _safe_mean(vals: list[float]) -> float:
    finite = [v for v in vals if math.isfinite(v)]
    return round(sum(finite) / len(finite), 4) if finite else 0.0


def _safe_std(vals: list[float]) -> float:
    finite = [v for v in vals if math.isfinite(v)]
    if not finite:
        return 0.0
    m = sum(finite) / len(finite)
    return round((sum((v - m) ** 2 for v in finite) / len(finite)) ** 0.5, 4)


# ---------------------------------------------------------------------------
# News health (§24/§25/§26)
# ---------------------------------------------------------------------------


def _news_state(news_path: Path | None = None) -> dict[str, Any]:
    path = news_path or Path("artifacts") / "news.db"
    out: dict[str, Any] = {"exists": path.exists()}
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            out["tables"] = len(tables)
            if "news_sources" in tables:
                out["sources"] = [
                    dict(zip([d[0] for d in cur.description], r, strict=False))
                    for cur in [conn.execute("SELECT * FROM news_sources")]
                    for r in cur.fetchall()
                ]
            if "news_worker_state" in tables:
                cols = [
                    d[0]
                    for d in conn.execute("SELECT * FROM news_worker_state LIMIT 0").description
                ]
                rows = conn.execute("SELECT * FROM news_worker_state").fetchall()
                out["worker_state"] = [dict(zip(cols, r, strict=False)) for r in rows]
            if "news_health" in tables:
                cols = [d[0] for d in conn.execute("SELECT * FROM news_health LIMIT 0").description]
                rows = conn.execute("SELECT * FROM news_health").fetchall()
                out["source_health"] = [dict(zip(cols, r, strict=False)) for r in rows]
            if "news_articles" in tables:
                out["article_count"] = _row_count(conn, "news_articles") or 0
            if "news_consensus" in tables:
                out["consensus_count"] = _row_count(conn, "news_consensus") or 0
            if "news_impacts" in tables:
                out["impact_count"] = _row_count(conn, "news_impacts") or 0
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _load_runtime_config() -> Any | None:
    """Loads AppConfig from the repo config path or defaults (never raises)."""
    try:
        from nexus_scalp.configuration.config import AppConfig

        for p in (Path("configs") / "base.yaml", Path("configs/base.yaml")):
            if p.exists():
                return AppConfig.load_from_yaml(p)
        return AppConfig()
    except Exception:
        return None


def _config_mode(cfg: Any) -> str | None:
    try:
        mode = getattr(getattr(cfg, "execution", None), "mode", None)
        return str(getattr(mode, "value", mode)) if mode is not None else None
    except Exception:
        return None


def _config_news_enabled(cfg: Any) -> bool | None:
    try:
        news = getattr(cfg, "news", None)
        if news is None:
            return None
        return bool(getattr(news, "enabled", False))
    except Exception:
        return None


def _config_liquidity_enabled(cfg: Any) -> bool | None:
    try:
        model = getattr(cfg, "model", None)
        if model is None:
            return None
        return bool(getattr(model, "liquidity_features_enabled", False))
    except Exception:
        return None


def _shadow_state() -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    path = _audit_path()
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t in (
                "shadow_runs",
                "shadow_decisions",
                "shadow_promotions",
                "model_shadow_comparisons",
                "model_runtime_health",
                "model_governance_state",
            ):
                out[t] = _row_count(conn, t) if t in tables else "ABSENT"
            if "model_runtime_health" in tables:
                cols = [
                    d[0]
                    for d in conn.execute("SELECT * FROM model_runtime_health LIMIT 0").description
                ]
                rows = conn.execute(
                    "SELECT * FROM model_runtime_health ORDER BY rowid DESC LIMIT 1"
                ).fetchall()
                out["latest_runtime_health"] = [dict(zip(cols, r, strict=False)) for r in rows]
            out["available"] = True
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _ui_bundle_files() -> dict[str, Any]:
    out: dict[str, Any] = {"found": False}
    import re as _re

    for root in (Path("Web"), Path("web")):
        idx = root / "index.html"
        js = root / "app.js"
        if idx.exists() and js.exists():
            out = {
                "found": True,
                "root": str(root),
                "index_html": {"size": idx.stat().st_size, "mtime": idx.stat().st_mtime},
                "app_js": {"size": js.stat().st_size, "mtime": js.stat().st_mtime},
            }
            # Real version markers: assignment of a version constant or
            # state_version guard — NOT any line containing the substring
            # "version" (e.g. a comment or log string).
            raw = js.read_text(errors="replace")[:300000]
            patterns = (
                r"[\"']?version[\"']?\s*[:=]\s*[\"'][^\"']+[\"']",
                r"appVersion\s*=\s*[\"'][^\"']+[\"']",
                r"bundleVersion\s*=\s*[\"'][^\"']+[\"']",
                r"state_version\s*[!=]=\s*null",
            )
            markers = []
            for pat in patterns:
                for m in _re.finditer(pat, raw, _re.IGNORECASE):
                    markers.append(m.group(0))
                    if len(markers) >= 3:
                        break
                if len(markers) >= 3:
                    break
            out["version_markers"] = markers
            break
    return out
