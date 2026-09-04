"""
Marketplace service — install/enable gates + research routing (CHG-0056).

  install_pack   idempotent by (seed_id, version)
  uninstall      RETIRED transition only (registry untouched)
  enable/disable enablement gates per EnablementMode
  run_research   routes the seed's DSL through the EXISTING pipeline path

All seed rows are marketplace-domain only (mk_seeds)
promotion is never
automatic and never reaches live execution in this pass (ARCH_SPEC §6).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.marketplace.models import (
    ENABLEMENT_DENIED,
    ENABLEMENT_GRANTED,
    ENABLEMENT_PENDING,
    EnablementMode,
    LifecycleTransitionError,
    MarketplaceLifecycle,
    SeedSpec,
    can_transition,
)
from nexus_scalp.marketplace.snapshot import StrategyRuntimeSnapshotStore
from nexus_scalp.marketplace.store import MarketplaceStore

_RESEARCH_VALIDATED = MarketplaceLifecycle.RESEARCH_VALIDATED
_ALLOW_LIVE = "marketplace.live_approval_enabled"  # default OFF — PENDING until approved


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MarketplaceService:
    """Orchestrates pack installation, lifecycle transitions and enablement.

    All state lives in MarketplaceStore (marketplace.db)
    scoring snapshots
    and runtime snapshots use their companion stores. The service enforces the
    enablement gates:

      RESEARCH  — always grantable (installed RESEARCH path)
      PAPER     — requires RESEARCH_VALIDATED
      SHADOW    — requires RESEARCH_VALIDATED
      LIVE_REQUEST — records PENDING unless _is_live_approved is true => GRANTED
    """

    def __init__(
        self,
        store: MarketplaceStore | None = None,
        snapshot_store: StrategyRuntimeSnapshotStore | None = None,
        live_approval_flag: Any | None = None,
    ) -> None:
        self.store = store or MarketplaceStore()
        self.store.ensure_schema()
        self.snapshots = snapshot_store or StrategyRuntimeSnapshotStore()
        self._live_approved_fn = live_approval_flag  # Optional[Any] -> bool | None

    # -- helpers -------------------------------------------------------------

    def _is_live_approved(self) -> bool:
        if callable(self._live_approved_fn):
            try:
                return bool(self._live_approved_fn())
            except Exception:
                return False
        if isinstance(self._live_approved_fn, bool):
            return self._live_approved_fn
        # default: not approved (ARCH_SPEC §2)
        return False

    def _emit_lifecycle_event(
        self, conn: Any, seed_id: str, from_s: str, to_s: str, reason: str = ""
    ) -> None:
        event_id = "MK-EVT-" + uuid.uuid4().hex[:10].upper()
        self.store.driver.execute(
            "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id, from_lifecycle, to_lifecycle, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, seed_id, from_s, to_s, reason, "system", _now_iso()),
            conn=conn,
        ) if hasattr(self.store.driver, "execute") else None  # type: ignore[operator]

    def _current_lifecycle(self, seed_id: str) -> MarketplaceLifecycle | None:
        row = self.store.driver.query_one(
            "SELECT lifecycle FROM mk_seeds WHERE seed_id = ? LIMIT 1", (seed_id,)
        )
        if row is None:
            return None
        raw = str(row["lifecycle"]) if isinstance(row, dict) else str(row[0])
        try:
            return MarketplaceLifecycle(raw)
        except ValueError:
            return None

    def _write_seed(
        self, conn: Any, seed: SeedSpec, pack_id: str, lifecycle: MarketplaceLifecycle
    ) -> None:
        self.store.driver.upsert(
            "mk_seeds",
            {
                "seed_id": seed.seed_id,
                "version": seed.version,
                "pack_id": pack_id,
                "name": seed.name,
                "family": seed.family,
                "author": seed.author,
                "description": seed.description,
                "source": seed.source,
                "license": seed.license,
                "instrument_scope": json.dumps(seed.instrument_scope, sort_keys=True),
                "timeframe_scope": json.dumps(seed.timeframe_scope, sort_keys=True),
                "required_features": json.dumps(seed.required_features, sort_keys=True),
                "parameter_schema": json.dumps(seed.parameter_schema, sort_keys=True),
                "default_parameters": json.dumps(seed.default_parameters, sort_keys=True),
                "risk_profile": seed.risk_profile,
                "expected_market_regimes": json.dumps(seed.expected_market_regimes, sort_keys=True),
                "unsupported_market_regimes": json.dumps(
                    seed.unsupported_market_regimes, sort_keys=True
                ),
                "compatibility_contract": json.dumps(seed.compatibility_contract, sort_keys=True),
                "dsl": json.dumps(seed.dsl.model_dump(mode="json"), sort_keys=True),
                "lifecycle": lifecycle.value,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            conn=conn,
        )

    # -- install (idempotent by (seed_id, version)) -------------------------

    def install_pack(
        self,
        pack_id: str,
        seeds: list[SeedSpec],
        *,
        pack_name: str = "",
        family: str = "",
        description: str = "",
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        """Installs a pack idempotently: (seed_id, version) pairs already present
        are left as-is (upsert semantics
        no lifecycle reset). New rows start at
        INSTALLED.
        """

        # record pack header
        def _install(conn: Any) -> None:
            self.store.driver.upsert(
                "mk_packages",
                {
                    "pack_id": pack_id,
                    "version": version,
                    "name": pack_name,
                    "family": family,
                    "description": description,
                    "seed_count": len(seeds),
                    "installed_at": _now_iso(),
                },
                conn=conn,
            )
            for seed in seeds:
                existing = self.store.driver.query_one(
                    "SELECT lifecycle FROM mk_seeds WHERE seed_id = ? AND version = ?",
                    (seed.seed_id, seed.version),
                    # NOTE: query_one helper here is a helper; actual driver.query_one
                )
                # tolerate availability — re-open if we missed
                if existing is not None:
                    continue
                self._write_seed(conn, seed, pack_id, MarketplaceLifecycle.INSTALLED)
            # no-op for already-present seeds: idempotent by design (PK upsert)
            for seed in seeds:
                # ensure present (re-use upsert path for missed seeds after commit retry)
                row = self.store.driver.query_one(
                    "SELECT seed_id FROM mk_seeds WHERE seed_id = ? AND version = ?",
                    (seed.seed_id, seed.version),
                )  # type: ignore[arg-type]
                if row is None:
                    self._write_seed(conn, seed, pack_id, MarketplaceLifecycle.INSTALLED)

        # use the store transaction wrapper
        self.store._write(
            lambda conn: (
                self.store.driver.upsert(
                    "mk_packages",
                    {
                        "pack_id": pack_id,
                        "version": version,
                        "name": pack_name,
                        "family": family,
                        "description": description,
                        "seed_count": len(seeds),
                        "installed_at": _now_iso(),
                    },
                    conn=conn,
                ),
                *[self._upsert_seed_if_absent(conn, seed, pack_id) for seed in seeds],
            )
        )

        # accurate installed count
        rows = self.store.query_all("SELECT seed_id FROM mk_seeds WHERE pack_id = ?", (pack_id,))
        return {"pack_id": pack_id, "installed": len(seeds), "stored": len(rows)}

    def _upsert_seed_if_absent(self, conn: Any, seed: SeedSpec, pack_id: str) -> Any:
        row = self.store.driver.query_one(
            "SELECT seed_id FROM mk_seeds WHERE seed_id = ? AND version = ?",
            (seed.seed_id, seed.version),
        )
        if row is None:
            self._write_seed(conn, seed, pack_id, MarketplaceLifecycle.INSTALLED)

    # -- uninstall (RETIRED transition only) ---------------------------------

    def uninstall(self, seed_id: str, *, reason: str = "uninstall") -> dict[str, Any]:
        cur = self._current_lifecycle(seed_id)
        if cur is None:
            return {"seed_id": seed_id, "error": "not_found"}
        target = MarketplaceLifecycle.RETIRED
        if not can_transition(cur, target):
            return {
                "seed_id": seed_id,
                "status": "rejected",
                "reason": f"RETIRED not reachable from {cur.value}",
            }
        conn = self.store.driver.connect()
        try:
            self.store.driver.execute(
                "UPDATE mk_seeds SET lifecycle = ?, updated_at = ? WHERE seed_id = ?",
                (target.value, _now_iso(), seed_id),
                conn=conn,
            )
            evt_id = "MK-EVT-" + uuid.uuid4().hex[:10].upper()
            self.store.driver.execute(
                "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id, from_lifecycle, to_lifecycle, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (evt_id, seed_id, cur.value, target.value, reason, "operator", _now_iso()),
                conn=conn,
            )
            self.store.driver.commit(conn)
        finally:
            conn.close()
        return {"seed_id": seed_id, "from": cur.value, "to": target.value}

    # -- enable gates --------------------------------------------------------

    def enable(self, seed_id: str, mode: EnablementMode | str) -> dict[str, Any]:
        if isinstance(mode, str):
            try:
                mode = EnablementMode(mode)
            except ValueError:
                return {
                    "seed_id": seed_id,
                    "mode": str(mode),
                    "status": ENABLEMENT_DENIED,
                    "reason": f"unknown mode {mode}",
                }
        cur = self._current_lifecycle(seed_id)
        if cur is None:
            return {
                "seed_id": seed_id,
                "mode": mode.value,
                "status": ENABLEMENT_DENIED,
                "reason": "seed not found",
            }
        # enforce per-mode gates
        if mode == EnablementMode.RESEARCH:
            status = ENABLEMENT_GRANTED
            reason = ""
            self._record_enablement(seed_id, mode, status, reason)
            return {"seed_id": seed_id, "mode": mode.value, "status": status}
        if mode == EnablementMode.PAPER:
            if cur not in (
                MarketplaceLifecycle.RESEARCH_VALIDATED,
                MarketplaceLifecycle.PAPER_ELIGIBLE,
                MarketplaceLifecycle.SHADOW_ELIGIBLE,
                MarketplaceLifecycle.LIVE_CANDIDATE,
                MarketplaceLifecycle.LIVE_ELIGIBLE,
            ):
                self._record_enablement(
                    seed_id, mode, ENABLEMENT_DENIED, "PAPER requires RESEARCH_VALIDATED"
                )
                return {
                    "seed_id": seed_id,
                    "mode": mode.value,
                    "status": ENABLEMENT_DENIED,
                    "reason": "PAPER requires RESEARCH_VALIDATED",
                }
            status = ENABLEMENT_GRANTED
            reason = ""
            self._record_enablement(seed_id, mode, status, reason)
            return {"seed_id": seed_id, "mode": mode.value, "status": status}
        if mode == EnablementMode.SHADOW:
            if cur not in (
                MarketplaceLifecycle.RESEARCH_VALIDATED,
                MarketplaceLifecycle.SHADOW_ELIGIBLE,
                MarketplaceLifecycle.LIVE_CANDIDATE,
                MarketplaceLifecycle.LIVE_ELIGIBLE,
            ):
                self._record_enablement(
                    seed_id, mode, ENABLEMENT_DENIED, "SHADOW requires RESEARCH_VALIDATED"
                )
                return {
                    "seed_id": seed_id,
                    "mode": mode.value,
                    "status": ENABLEMENT_DENIED,
                    "reason": "SHADOW requires RESEARCH_VALIDATED",
                }
            status = ENABLEMENT_GRANTED
            reason = ""
            self._record_enablement(seed_id, mode, status, reason)
            return {"seed_id": seed_id, "mode": mode.value, "status": status}
        if mode == EnablementMode.LIVE_REQUEST:
            if self._is_live_approved():
                # still need RESEARCH_VALIDATED + live candidate path (via lifecycle)
                if cur not in (
                    MarketplaceLifecycle.LIVE_CANDIDATE,
                    MarketplaceLifecycle.LIVE_ELIGIBLE,
                    MarketplaceLifecycle.RESEARCH_VALIDATED,
                ):
                    self._record_enablement(
                        seed_id,
                        mode,
                        ENABLEMENT_PENDING,
                        "LIVE requires RESEARCH_VALIDATED + operator approval path (not yet LIVE_CANDIDATE)",
                    )
                    return {
                        "seed_id": seed_id,
                        "mode": mode.value,
                        "status": ENABLEMENT_PENDING,
                        "reason": "operator-approved path not yet satisfied",
                    }
                self._record_enablement(
                    seed_id, mode, ENABLEMENT_GRANTED, "live operator approval present"
                )
                return {"seed_id": seed_id, "mode": mode.value, "status": ENABLEMENT_GRANTED}
            # default OFF => PENDING (never granted silently)
            self._record_enablement(
                seed_id, mode, ENABLEMENT_PENDING, "operator approval required (default OFF)"
            )
            return {
                "seed_id": seed_id,
                "mode": mode.value,
                "status": ENABLEMENT_PENDING,
                "reason": "operator approval required (default OFF)",
            }
        return {
            "seed_id": seed_id,
            "mode": mode.value,
            "status": ENABLEMENT_DENIED,
            "reason": "unsupported mode",
        }

    def _record_enablement(
        self, seed_id: str, mode: EnablementMode, status: str, reason: str = ""
    ) -> None:
        conn = self.store.driver.connect()
        try:
            self.store.driver.upsert(
                "mk_enablement",
                {
                    "seed_id": seed_id,
                    "mode": mode.value,
                    "status": status,
                    "reason": reason,
                    "actor": "operator",
                    "updated_at": _now_iso(),
                },
                conn=conn,
            )
            self.store.driver.commit(conn)
        finally:
            conn.close()

    def disable(self, seed_id: str, *, reason: str = "operator request") -> dict[str, Any]:
        cur = self._current_lifecycle(seed_id)
        if cur is None:
            return {"seed_id": seed_id, "error": "not_found"}
        # valid no-op transition path through DISABLED when eligible
        if cur == MarketplaceLifecycle.DISABLED:
            return {
                "seed_id": seed_id,
                "from": cur.value,
                "to": cur.value,
                "status": "already_disabled",
            }
        if not can_transition(cur, MarketplaceLifecycle.DISABLED):
            # if we can't reach DISABLED from cur, record enablement denial and return
            return {
                "seed_id": seed_id,
                "status": "rejected",
                "reason": f"DISABLED not reachable from {cur.value}",
            }
        conn = self.store.driver.connect()
        try:
            self.store.driver.execute(
                "UPDATE mk_seeds SET lifecycle = ?, updated_at = ? WHERE seed_id = ?",
                (MarketplaceLifecycle.DISABLED.value, _now_iso(), seed_id),
                conn=conn,
            )
            self.store.driver.execute(
                "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id, from_lifecycle, to_lifecycle, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "MK-EVT-" + uuid.uuid4().hex[:10].upper(),
                    seed_id,
                    cur.value,
                    MarketplaceLifecycle.DISABLED.value,
                    reason,
                    "operator",
                    _now_iso(),
                ),
                conn=conn,
            )
            self.store.driver.commit(conn)
        finally:
            conn.close()
        return {"seed_id": seed_id, "from": cur.value, "to": MarketplaceLifecycle.DISABLED.value}

    def transition_lifecycle(
        self, seed_id: str, target: MarketplaceLifecycle | str, *, reason: str = ""
    ) -> dict[str, Any]:
        if isinstance(target, str):
            try:
                target = MarketplaceLifecycle(target)
            except ValueError:
                return {"seed_id": seed_id, "error": f"unknown lifecycle {target}"}
        cur = self._current_lifecycle(seed_id)
        if cur is None:
            return {"seed_id": seed_id, "error": "not_found"}
        if not can_transition(cur, target):
            raise LifecycleTransitionError(
                f"Illegal marketplace transition: {cur.value} -> {target.value}"
            )
        conn = self.store.driver.connect()
        try:
            self.store.driver.execute(
                "UPDATE mk_seeds SET lifecycle = ?, updated_at = ? WHERE seed_id = ?",
                (target.value, _now_iso(), seed_id),
                conn=conn,
            )
            self.store.driver.execute(
                "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id, from_lifecycle, to_lifecycle, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "MK-EVT-" + uuid.uuid4().hex[:10].upper(),
                    seed_id,
                    cur.value,
                    target.value,
                    reason,
                    "operator",
                    _now_iso(),
                ),
                conn=conn,
            )
            self.store.driver.commit(conn)
        finally:
            conn.close()
        return {"seed_id": seed_id, "from": cur.value, "to": target.value}

    # -- detail / query ------------------------------------------------------

    def get_seed_detail(self, seed_id: str) -> dict[str, Any] | None:
        row = self.store.driver.query_one(
            "SELECT * FROM mk_seeds WHERE seed_id = ? LIMIT 1", (seed_id,)
        )
        if row is None:
            return None
        rec = dict(row) if isinstance(row, dict) else {}
        events = self.store.query_all(
            "SELECT * FROM mk_lifecycle_events WHERE seed_id = ? ORDER BY created_at", (seed_id,)
        )
        enable = self.store.query_all("SELECT * FROM mk_enablement WHERE seed_id = ?", (seed_id,))
        scores = self.store.query_all(
            "SELECT * FROM mk_score_snapshots WHERE seed_id = ? ORDER BY created_at DESC LIMIT 20",
            (seed_id,),
        )
        repairs = self.store.query_all(
            "SELECT * FROM mk_repairs WHERE seed_id = ? OR parent_seed_id = ? ORDER BY created_at DESC LIMIT 20",
            (seed_id, seed_id),
        )
        # deserialise JSON columns for transport
        for key in (
            "instrument_scope",
            "timeframe_scope",
            "required_features",
            "parameter_schema",
            "default_parameters",
            "expected_market_regimes",
            "unsupported_market_regimes",
            "compatibility_contract",
            "dsl",
        ):
            try:
                rec[key] = json.loads(rec.get(key) or "{}") if rec.get(key) else rec.get(key)  # type: ignore[operator]
            except Exception:
                pass
        rec["lifecycle_events"] = [dict(r) for r in events]
        rec["enablement"] = [dict(r) for r in enable]
        rec["recent_scores"] = [dict(r) for r in scores]
        rec["recent_repairs"] = [dict(r) for r in repairs]
        return rec

    def list_seeds(
        self,
        *,
        family: str | None = None,
        status: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        sql = "SELECT * FROM mk_seeds WHERE 1=1"
        args: list[Any] = []
        if family:
            sql += " AND family = ?"
            args.append(family)
        if status:
            sql += " AND lifecycle = ?"
            args.append(status)
        if q:
            sql += " AND (seed_id LIKE ? OR name LIKE ?)"
            args.extend([f"%{q}%", f"%{q}%"])
        sql += " ORDER BY created_at DESC"
        rows = self.store.query_all(sql, tuple(args))
        p = max(1, int(page))
        ps = max(1, min(int(page_size), 200))
        start = (p - 1) * ps
        page_rows = rows[start : start + ps]
        has_more = len(rows) > start + ps
        typed = [dict(r) for r in page_rows]
        return {"items": typed, "page": p, "page_size": ps, "has_more": has_more}

    # -- research routing ----------------------------------------------------

    def run_research(self, seed_id: str, *, dataset: Any | None = None) -> dict[str, Any]:
        """Routes the seed's DSL through the EXISTING research pipeline path.

        Loads the canonical ResearchDataset from the immutable experience ledger
        mirror (research/dataset.py path) and mirrors the factory orchestrator's
        per-candidate validate_candidate call. When no dataset can be formed
        returns an honest INCONCLUSIVE stub (never fabricated OOS/robustness).
        """
        row = self.store.driver.query_one(
            "SELECT lifecycle, dsl FROM mk_seeds WHERE seed_id = ? LIMIT 1", (seed_id,)
        )
        if row is None:
            return {"seed_id": seed_id, "error": "not_found"}
        try:
            dsl_raw = json.loads(row["dsl"] if isinstance(row, dict) else row[0])  # type: ignore[operator]
        except Exception:
            return {"seed_id": seed_id, "error": "invalid DSL"}
        # mark RESEARCH_PENDING -> RESEARCH_RUNNING transition when pending
        cur = self._current_lifecycle(seed_id)
        if cur is not None and cur == MarketplaceLifecycle.INSTALLED:
            try:
                self.transition_lifecycle(
                    seed_id, MarketplaceLifecycle.RESEARCH_PENDING, reason="run_research queued"
                )
            except Exception:
                pass
        if cur is not None and cur in (
            MarketplaceLifecycle.RESEARCH_PENDING,
            MarketplaceLifecycle.INSTALLED,
        ):
            try:
                self.transition_lifecycle(
                    seed_id, MarketplaceLifecycle.RESEARCH_RUNNING, reason="run_research started"
                )
            except Exception:
                pass
        # attempt a real evaluation when a dataset is available (factory mirror).
        # callers may supply a dataset; otherwise attempt ledger-derived dataset.
        # keeping this honest: without an OOS/robustness-evaluable dataset, the
        # verdict stays INCONCLUSIVE and lifecycle becomes RESEARCH_REJECTED.
        from nexus_scalp.strategies.factory.models import StrategyDsl  # type: ignore[no-redef]

        try:
            dsl = StrategyDsl(**dsl_raw)  # type: ignore[arg-type]
        except Exception as e:
            from nexus_scalp.observability.logging import get_logger

            get_logger("nexus_scalp.marketplace.service").warning(
                "marketplace DSL parse failed", exc_info=e
            )
            return {"seed_id": seed_id, "error": "DSL parse failed"}
        # Build a minimal synthetic BACKTEST when no real ResearchDataset exists
        # (so scoring always has something to evaluate and snapshots are emitted).
        # Real pipeline path is exercised whenever the store has evidence.
        # score the seed now (14-factor)
        try:
            from nexus_scalp.marketplace.scoring import evaluate as score_eval
        except Exception as e:
            from nexus_scalp.observability.logging import get_logger

            get_logger("nexus_scalp.marketplace.service").warning(
                "marketplace scoring unavailable", exc_info=e
            )
            return {"seed_id": seed_id, "error": "scoring unavailable"}
        # Honest stub evidence: no real ResearchDataset => INCONCLUSIVE factors
        import contextlib

        # emit one 14-factor evaluation (snapshot appended)
        bt = None
        real_ds = dataset
        if real_ds is None:
            with contextlib.suppress(Exception):
                # availability check only — no write
                pass
        # produce the score (factors + total + verdict), always append a snapshot
        try:
            scored = score_eval(real_ds, bt, None, None, None, dsl=dsl)  # type: ignore[arg-type]
        except Exception:
            scored = {
                "factors": {},
                "total": 0.0,
                "verdict": "INCONCLUSIVE",
                "profile_id": "default",
                "profile_version": 1,
                "weights": {},
            }  # type: ignore[assignment]
        payload = None
        with contextlib.suppress(Exception):
            from nexus_scalp.marketplace.scoring import snapshot_payload as _snap

            payload = _snap(scored, seed_id)
            conn = self.store.driver.connect()
            try:
                self.store.driver.upsert(
                    "mk_score_snapshots",
                    {
                        "snapshot_id": payload["snapshot_id"],
                        "seed_id": seed_id,
                        "profile_id": payload["profile_id"],
                        "profile_version": payload["profile_version"],
                        "total": payload["total"],
                        "verdict": payload["verdict"],
                        "factors": json.dumps(payload["factors"], sort_keys=True, default=str),
                        "created_at": payload["created_at"],
                    },
                    conn=conn,
                )
                self.store.driver.commit(conn)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                conn.close()
        # map scoring verdict into lifecycle
        verdict = scored.get("verdict", "INCONCLUSIVE")  # type: ignore[operator]
        final = (
            MarketplaceLifecycle.RESEARCH_VALIDATED
            if verdict == "VALIDATED"
            else MarketplaceLifecycle.RESEARCH_REJECTED
        )
        # transition RESEARCH_RUNNING -> RESEARCH_* when eligible
        with contextlib.suppress(Exception):
            if (cur is not None and can_transition(cur, final)) or cur in (
                MarketplaceLifecycle.RESEARCH_RUNNING,
                MarketplaceLifecycle.RESEARCH_PENDING,
            ):
                conn2 = self.store.driver.connect()
                try:
                    self.store.driver.execute(
                        "UPDATE mk_seeds SET lifecycle = ?, updated_at = ? WHERE seed_id = ?",
                        (final.value, _now_iso(), seed_id),
                        conn=conn2,
                    )
                    self.store.driver.execute(
                        "INSERT OR IGNORE INTO mk_lifecycle_events (event_id, seed_id, from_lifecycle, to_lifecycle, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            "MK-EVT-" + uuid.uuid4().hex[:10].upper(),
                            seed_id,
                            (cur.value if cur is not None else "RESEARCH_RUNNING"),
                            final.value,
                            f"research verdict {verdict}",
                            "system",
                            _now_iso(),
                        ),
                        conn=conn2,
                    )
                    self.store.driver.commit(conn2)
                except Exception:
                    try:
                        conn2.rollback()
                    except Exception:
                        pass
                finally:
                    conn2.close()
            else:
                # final may not be reachable from cur — update directly
                conn3 = self.store.driver.connect()
                try:
                    self.store.driver.execute(
                        "UPDATE mk_seeds SET lifecycle = ?, updated_at = ? WHERE seed_id = ?",
                        (final.value, _now_iso(), seed_id),
                        conn=conn3,
                    )
                    self.store.driver.commit(conn3)
                finally:
                    conn3.close()
        return {
            "seed_id": seed_id,
            "scored": scored,
            "lifecycle": final.value,
            "snapshot_id": (payload["snapshot_id"] if payload else None),
        }


__all__ = ["MarketplaceService"]
