"""
Zero-Latency Async Database Audit Repository (v3.0 Enterprise)
==============================================================
High-frequency background persistence layer recording trade executions, signals,
and critical account snapshots without blocking the primary 50ms event loop.

Enterprise Upgrades Incorporated:
    1. Zero-Latency Background Queuing (Main thread never waits for Disk I/O).
    2. SQLite Write-Ahead Logging (WAL) & Performance PRAGMAs (Prevents DB locks).
    3. Account Snapshot Persistence (Facilitates Crash Recovery & Peak Equity Memory).
    4. Market Regime Traceability (Logs Microstructure regime alongside signals).
    5. Context Manager & Graceful Shutdown (Flushes queue safely on exit).
"""

import os
import queue
import sqlite3
import threading
import time
from typing import Any

from nexus_scalp.domain.models import AccountInfo, TradeOrder, TradeProposal
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.audit_db")


class AuditRepository:
    """
    Enterprise Append-Only Audit Store with Async Disk I/O processing.
    """

    def __init__(
        self,
        db_url: str = "sqlite:///artifacts/audit.db",
        flush_interval_sec: float = 1.0,
    ) -> None:
        self._db_url = db_url
        self._is_sqlite = db_url.startswith("sqlite")
        self._db_path = self._db_url.replace("sqlite:///", "") if self._is_sqlite else ""

        self._flush_interval = flush_interval_sec
        self._queue: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=10000)
        self._running = False
        self._worker_thread: threading.Thread | None = None

        # Snapshots throttling state to prevent high-frequency DB bloat
        self._last_snapshot_time = 0.0
        self._last_snapshot_balance = 0.0
        self._last_snapshot_equity = 0.0

        self._setup_storage()
        self._start_background_worker()

    def _setup_storage(self) -> None:
        """Initializes tables, indexes, and HFT performance pragmas."""
        if self._is_sqlite:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)

            # NOTE: `with sqlite3.connect(...)` only wraps a transaction, it does NOT
            # close the connection. Leaking it keeps the .db/-wal/-shm files locked on
            # Windows, which breaks temp-directory cleanup in tests and log rotation.
            conn = sqlite3.connect(self._db_path, timeout=10.0)
            try:
                # Enable Write-Ahead Logging for high concurrency without locks
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("PRAGMA temp_store = MEMORY;")

                self._create_sqlite_tables(conn)
                conn.commit()
            finally:
                conn.close()
            logger.info("Initialized High-Performance SQLite WAL storage", db_path=self._db_path)

    def _create_sqlite_tables(self, conn: sqlite3.Connection) -> None:
        """Creates table schemas including Crash Recovery Snapshots & Regime tracking."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_rules_config (
                rule_name TEXT PRIMARY KEY,
                is_enabled INTEGER DEFAULT 0,
                category TEXT NOT NULL,
                parameters TEXT
            );
            """
        )

        self._create_experience_tables(conn)
        self._create_intelligence_tables(conn)
        self._create_research_tables(conn)
        self._seed_trading_rules(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                proposed_entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                regime TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                execution_mode TEXT,
                reason_code TEXT,
                decision_stage TEXT,
                blocked_by TEXT,
                htf_score REAL,
                smc_score REAL,
                confidence_before_filters REAL,
                confidence_after_filters REAL
            );
            """
        )
        # Migrate existing audit_signals table if needed
        for col_def in [
            ("execution_mode", "TEXT"),
            ("reason_code", "TEXT"),
            ("decision_stage", "TEXT"),
            ("blocked_by", "TEXT"),
            ("htf_score", "REAL"),
            ("smc_score", "REAL"),
            ("confidence_before_filters", "REAL"),
            ("confidence_after_filters", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE audit_signals ADD COLUMN {col_def[0]} {col_def[1]};")
            except Exception:
                pass

        # Track detailed pending orders & executions
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                volume REAL NOT NULL,
                reason TEXT,
                latency REAL,
                execution_mode TEXT,
                timestamp TEXT NOT NULL
            );
            """
        )

        # =====================================================================
        # INSTITUTIONAL FINANCIAL ACCOUNTING LEDGER (One autopsy row per trade)
        # =====================================================================
        # Legacy columns (ticket .. exit_mechanism) are retained verbatim for
        # backward compatibility with existing dashboards and metric queries.
        # The institutional autopsy columns extend them with full identification,
        # timing, financial, entry-context, SL-dynamics, quant-excursion, and
        # account-snapshot detail.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_ledger (
                ticket INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                volume REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                status TEXT NOT NULL,
                pnl REAL DEFAULT 0.0,
                commission REAL DEFAULT 0.0,
                swap REAL DEFAULT 0.0,
                duration_sec REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL,
                mae REAL DEFAULT 0.0,
                mfe REAL DEFAULT 0.0,
                initial_sl_price REAL DEFAULT 0.0,
                final_sl_price REAL DEFAULT 0.0,
                is_risk_free_hit INTEGER DEFAULT 0,
                exit_mechanism TEXT DEFAULT '',

                -- Identification
                order_id TEXT DEFAULT '',

                -- Timestamps & Price
                open_time TEXT DEFAULT '',
                close_time TEXT DEFAULT '',
                duration_seconds REAL DEFAULT 0.0,
                open_price REAL DEFAULT 0.0,
                close_price REAL DEFAULT 0.0,

                -- Financials
                gross_pnl_usd REAL DEFAULT 0.0,
                net_pnl_usd REAL DEFAULT 0.0,

                -- Entry Context
                entry_reason TEXT DEFAULT '',
                ai_confidence_at_open REAL DEFAULT 0.0,
                market_regime_at_open TEXT DEFAULT '',

                -- SL/TP Dynamics
                was_sl_modified INTEGER DEFAULT 0,

                -- Quant Risk Excursions
                MAE_usd REAL DEFAULT 0.0,
                MFE_usd REAL DEFAULT 0.0,

                -- Account Snapshot
                account_balance_after REAL DEFAULT 0.0,
                account_equity_after REAL DEFAULT 0.0,
                drawdown_percent_after REAL DEFAULT 0.0
            );
            """
        )
        # Safe alter statements for migration of pre-existing ledger tables
        for col_def in [
            ("mae", "REAL DEFAULT 0.0"),
            ("mfe", "REAL DEFAULT 0.0"),
            ("initial_sl_price", "REAL DEFAULT 0.0"),
            ("final_sl_price", "REAL DEFAULT 0.0"),
            ("is_risk_free_hit", "INTEGER DEFAULT 0"),
            ("exit_mechanism", "TEXT DEFAULT ''"),
            ("order_id", "TEXT DEFAULT ''"),
            ("open_time", "TEXT DEFAULT ''"),
            ("close_time", "TEXT DEFAULT ''"),
            ("duration_seconds", "REAL DEFAULT 0.0"),
            ("open_price", "REAL DEFAULT 0.0"),
            ("close_price", "REAL DEFAULT 0.0"),
            ("gross_pnl_usd", "REAL DEFAULT 0.0"),
            ("net_pnl_usd", "REAL DEFAULT 0.0"),
            ("entry_reason", "TEXT DEFAULT ''"),
            ("ai_confidence_at_open", "REAL DEFAULT 0.0"),
            ("market_regime_at_open", "TEXT DEFAULT ''"),
            ("was_sl_modified", "INTEGER DEFAULT 0"),
            ("MAE_usd", "REAL DEFAULT 0.0"),
            ("MFE_usd", "REAL DEFAULT 0.0"),
            ("account_balance_after", "REAL DEFAULT 0.0"),
            ("account_equity_after", "REAL DEFAULT 0.0"),
            ("drawdown_percent_after", "REAL DEFAULT 0.0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE audit_ledger ADD COLUMN {col_def[0]} {col_def[1]};")
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                order_type TEXT NOT NULL,
                volume REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        # BUGFIX: Table to persist Account Equity for Crash Recovery
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                margin_free REAL NOT NULL,
                peak_equity REAL NOT NULL
            );
            """
        )

    def _create_experience_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the Phase 08 Experience Intelligence schema.

        Design notes:
          * `audit_experiences` holds IMMUTABLE decision rows. Nothing in the
            codebase issues an UPDATE against it.
          * `audit_experience_outcomes` is append-only and keyed 1:1 by
            `idempotency_key`, which is what makes duplicate broker close
            callbacks harmless instead of inflating learning evidence.
          * `audit_experience_corrections` records additive corrections so
            historical truth is never destroyed.
          * `experience_model_registry` stores model METADATA only. Experiences
            never depend on a model artifact still existing.
          * Indexes cover every retrieval predicate used on the live path
            (strategy_id + decision_timestamp, symbol + decision_timestamp) so
            experience retrieval stays bounded and fast.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                execution_id TEXT DEFAULT '',
                decision_id TEXT DEFAULT '',
                idempotency_key TEXT UNIQUE NOT NULL,
                correction_of TEXT DEFAULT '',
                record_version INTEGER DEFAULT 2,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT DEFAULT '1.0.0',
                decision_timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                entry_reason TEXT NOT NULL,
                model_probability REAL DEFAULT 0.0,
                signal_confidence REAL DEFAULT 0.0,
                proposed_entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                risk_reward_ratio REAL DEFAULT 1.0,
                min_rr_policy REAL DEFAULT 0.0,
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                feature_hash TEXT DEFAULT '',
                model_id TEXT DEFAULT '',
                model_version TEXT DEFAULT '',
                config_version TEXT DEFAULT '',
                payload TEXT NOT NULL
            );
            """
        )
        # Forward migration for databases created by the first Phase 08 revision.
        for col_name, col_type in [
            ("correction_of", "TEXT DEFAULT ''"),
            ("record_version", "INTEGER DEFAULT 1"),
            ("min_rr_policy", "REAL DEFAULT 0.0"),
            ("feature_schema_id", "TEXT DEFAULT 'scalp_v1'"),
            ("feature_dimension", "INTEGER DEFAULT 50"),
            ("model_id", "TEXT DEFAULT ''"),
            ("model_version", "TEXT DEFAULT ''"),
            ("config_version", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE audit_experiences ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experience_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE NOT NULL,
                execution_id TEXT DEFAULT '',
                outcome_timestamp TEXT NOT NULL,
                is_executed INTEGER DEFAULT 0,
                is_closed INTEGER DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                realized_pnl_usd REAL DEFAULT 0.0,
                realized_r_multiple REAL DEFAULT 0.0,
                approved_volume REAL DEFAULT 0.0,
                mae_points REAL DEFAULT 0.0,
                mfe_points REAL DEFAULT 0.0,
                mae_usd REAL DEFAULT 0.0,
                mfe_usd REAL DEFAULT 0.0,
                mae_r REAL DEFAULT 0.0,
                mfe_r REAL DEFAULT 0.0,
                holding_duration_seconds REAL DEFAULT 0.0,
                slippage_points REAL DEFAULT 0.0,
                execution_latency_ms REAL DEFAULT 0.0,
                strategy_quality REAL DEFAULT 0.0,
                entry_quality REAL DEFAULT 0.0,
                execution_quality REAL DEFAULT 0.0,
                management_quality REAL DEFAULT 0.0,
                exit_quality REAL DEFAULT 0.0,
                behavioral_flags TEXT DEFAULT '',
                payload TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experience_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correction_id TEXT UNIQUE NOT NULL,
                idempotency_key TEXT NOT NULL,
                corrected_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT DEFAULT '',
                new_value TEXT DEFAULT ''
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_intelligence_registry (
                strategy_id TEXT PRIMARY KEY,
                lifecycle_state TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,
                expectancy_r REAL DEFAULT 0.0,
                recent_expectancy_r REAL DEFAULT 0.0,
                normalized_drawdown_r REAL DEFAULT 0.0,
                profit_factor REAL DEFAULT 1.0,
                confidence_score REAL DEFAULT 0.0,
                evidence_quality REAL DEFAULT 0.0,
                replay_validated INTEGER DEFAULT 0,
                probation_samples INTEGER DEFAULT 0,
                score_payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for col_name, col_type in [
            ("recent_expectancy_r", "REAL DEFAULT 0.0"),
            ("normalized_drawdown_r", "REAL DEFAULT 0.0"),
            ("evidence_quality", "REAL DEFAULT 0.0"),
            ("replay_validated", "INTEGER DEFAULT 0"),
            ("probation_samples", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE strategy_intelligence_registry ADD COLUMN {col_name} {col_type};"
                )
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_model_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                model_role TEXT DEFAULT 'PRIMARY_SCALP',
                artifact_path TEXT DEFAULT '',
                artifact_fingerprint TEXT DEFAULT '',
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                config_version TEXT DEFAULT '',
                build_identity TEXT DEFAULT '',
                was_replacement INTEGER DEFAULT 0,
                registered_at TEXT NOT NULL,
                UNIQUE(model_id, model_version, artifact_fingerprint)
            );
            """
        )

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_exp_strategy_time "
            "ON audit_experiences(strategy_id, decision_timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_exp_symbol_time "
            "ON audit_experiences(symbol, decision_timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_exp_request ON audit_experiences(request_id);",
            "CREATE INDEX IF NOT EXISTS idx_exp_schema "
            "ON audit_experiences(feature_schema_id, feature_dimension);",
            "CREATE INDEX IF NOT EXISTS idx_exp_outcome_key "
            "ON audit_experience_outcomes(idempotency_key);",
            "CREATE INDEX IF NOT EXISTS idx_exp_corrections_key "
            "ON audit_experience_corrections(idempotency_key);",
        ):
            try:
                conn.execute(index_sql)
            except Exception:
                pass

    def _create_intelligence_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the PHASE 09 Trade Intelligence Brain schema.

        All rows here are derived/buildable intelligence layered on top of the
        authoritative Phase 08 experience tables. Design rules:

          * `position_lifecycle_events`  -- IMMUTABLE append-only position-timeline
            events keyed by (ticket, event_key) so replay can never duplicate or
            reorder the timeline of a position.
          * `trade_autopsies`            -- ONE forensic narrative row per closed
            ticket (upsert on ticket), answering "why did this trade win/lose?".
          * `behavior_detections`        -- append-only measurable behavioral
            pattern evidence (GREED_PATTERN, PANIC_EXIT_PATTERN, ...).
          * `strategy_evolution_candidates` -- discovered-but-unvalidated strategy
            variations. A candidate is NEVER executed live; only backtested and
            validated before it may enter strategy memory.
          * `intelligence_worker_state`  -- restart-safe worker bookkeeping so a
            crash mid-cycle resumes from the last checkpoint instead of redoing
            history.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                ticket TEXT NOT NULL,
                trade_id TEXT DEFAULT '',
                experience_id TEXT DEFAULT '',
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                sequence INTEGER DEFAULT 0,
                event_timestamp TEXT NOT NULL,
                market_context TEXT DEFAULT '{}',
                position_snapshot TEXT DEFAULT '{}',
                payload TEXT DEFAULT '{}'
            );
            """
        )
        for col_name, col_type in [
            ("experience_id", "TEXT DEFAULT ''"),
            ("sequence", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE position_lifecycle_events ADD COLUMN {col_name} {col_type};"
                )
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_autopsies (
                ticket TEXT PRIMARY KEY,
                trade_id TEXT DEFAULT '',
                experience_id TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                strategy_version TEXT DEFAULT '',
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                entry_price REAL DEFAULT 0.0,
                exit_price REAL DEFAULT 0.0,
                volume REAL DEFAULT 0.0,
                direction TEXT DEFAULT '',
                entry_reason TEXT DEFAULT '',
                realized_pnl_usd REAL DEFAULT 0.0,
                realized_r REAL DEFAULT 0.0,
                mfe_r REAL DEFAULT 0.0,
                mae_r REAL DEFAULT 0.0,
                giveback_pct REAL DEFAULT 0.0,
                holding_duration_sec REAL DEFAULT 0.0,
                exit_mechanism TEXT DEFAULT '',
                strategy_quality REAL DEFAULT 0.0,
                entry_quality REAL DEFAULT 0.0,
                management_quality REAL DEFAULT 0.0,
                exit_quality REAL DEFAULT 0.0,
                execution_quality REAL DEFAULT 0.0,
                quality_verdict TEXT DEFAULT '',
                behavioral_flags TEXT DEFAULT '',
                narrative TEXT DEFAULT '',
                autopsied_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        for col_name, col_type in [
            ("strategy_version", "TEXT DEFAULT ''"),
            ("symbol", "TEXT NOT NULL DEFAULT ''"),
            ("timeframe", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE trade_autopsies ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                behavior_key TEXT UNIQUE NOT NULL,
                behavior_id TEXT NOT NULL,
                ticket TEXT NOT NULL,
                experience_id TEXT DEFAULT '',
                ticket_ctx TEXT DEFAULT '',
                pattern TEXT NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                evidence TEXT DEFAULT '{}',
                detected_at TEXT NOT NULL,
                autocorrected INTEGER DEFAULT 0
            );
            """
        )
        for col_name, col_type in [
            ("ticket_ctx", "TEXT DEFAULT ''"),
            ("behavior_key", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE behavior_detections ADD COLUMN {col_name} {col_type};")
            except Exception:
                pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_evolution_candidates (
                candidate_id TEXT PRIMARY KEY,
                source_strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                hypothesis TEXT NOT NULL,
                parameter_delta TEXT DEFAULT '{}',
                pattern_evidence TEXT DEFAULT '{}',
                status TEXT NOT NULL,
                backtest_expectancy_r REAL DEFAULT 0.0,
                backtest_sample_count INTEGER DEFAULT 0,
                validated_at TEXT DEFAULT '',
                discovered_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intelligence_worker_state (
                scope TEXT PRIMARY KEY,
                last_checkpoint TEXT DEFAULT '',
                last_cycle_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0
            );
            """
        )

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_ticket "
            "ON position_lifecycle_events(ticket, sequence);",
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_type "
            "ON position_lifecycle_events(event_type);",
            "CREATE INDEX IF NOT EXISTS idx_autopsy_strategy ON trade_autopsies(strategy_id);",
            "CREATE INDEX IF NOT EXISTS idx_behavior_ticket ON behavior_detections(ticket);",
            "CREATE INDEX IF NOT EXISTS idx_behavior_pattern ON behavior_detections(pattern);",
            "CREATE INDEX IF NOT EXISTS idx_evolution_status "
            "ON strategy_evolution_candidates(status);",
        ):
            try:
                conn.execute(index_sql)
            except Exception:
                pass

    def _create_research_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the PHASE 09B Strategy Research / Backtest / Validation schema.

        All rows here are DERIVED from the authoritative Phase 08 experience
        ledger and are rebuildable. The registry preserves historical validation
        truth (spec 20 / 28); validation runs are append-only (spec 26).
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                discovery_source TEXT DEFAULT '',
                discovery_window TEXT DEFAULT '',
                context_definition TEXT DEFAULT '{}',
                parent_strategy_ids TEXT DEFAULT '[]',
                lifecycle TEXT NOT NULL,
                backtest TEXT DEFAULT '{}',
                walkforward TEXT DEFAULT '{}',
                oos TEXT DEFAULT '{}',
                robustness TEXT DEFAULT '{}',
                score TEXT DEFAULT '{}',
                confidence REAL DEFAULT 0.0,
                sample_count INTEGER DEFAULT 0,
                validation_lineage TEXT DEFAULT '[]',
                retirement_reason TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (strategy_id, strategy_version)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                build_identity TEXT DEFAULT '',
                result_summary TEXT DEFAULT '{}',
                UNIQUE (run_id)
            );
            """
        )
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registry_id ON strategy_registry(strategy_id, updated_at);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registry_lifecycle ON strategy_registry(lifecycle);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_runs_strategy ON research_runs(strategy_id);"
            )
        except Exception:
            pass

        # Restart-safe research worker bookkeeping.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_worker_state (
                scope TEXT PRIMARY KEY,
                last_checkpoint TEXT DEFAULT '',
                last_cycle_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0
            );
            """
        )

    def _start_background_worker(self) -> None:
        """Starts the dedicated background thread for zero-latency database inserts."""
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._process_queue_worker, daemon=True, name="AuditDB_Worker"
        )
        self._worker_thread.start()

    def _process_queue_worker(self) -> None:
        """Background loop flushing pending inserts to disk via Bulk Transactions."""
        if not self._is_sqlite:
            return

        conn = sqlite3.connect(self._db_path, timeout=10.0)

        while self._running or not self._queue.empty():
            batch: list[tuple[str, tuple]] = []
            try:
                # Wait for records, batch them up to 500 per transaction
                while len(batch) < 500:
                    query_tuple = self._queue.get(timeout=self._flush_interval)
                    batch.append(query_tuple)
            except queue.Empty:
                pass

            if batch:
                try:
                    with conn:
                        for query, args in batch:
                            conn.execute(query, args)
                    for _ in batch:
                        self._queue.task_done()
                except Exception as e:
                    logger.error("Audit Background Worker failed to insert batch", error=str(e))
                    time.sleep(1.0)  # Backoff on error

        conn.close()

    def log_signal(self, proposal: TradeProposal) -> None:
        """Zero-latency async logging of generated trade signals."""
        import json

        if not self._is_sqlite:
            return

        # Deduplication check to prevent DB bloat (same symbol, timeframe, candle, action, zone)
        candle_time = proposal.generated_at.replace(second=0, microsecond=0)
        timeframe = "M1"
        model_action = getattr(proposal, "model_action", proposal.action.value)
        entry_zone = getattr(proposal, "reason_code", "UNKNOWN")
        dedup_key = (proposal.symbol, timeframe, candle_time, model_action, entry_zone)

        if not hasattr(self, "_last_logged_signal_key"):
            self._last_logged_signal_key = None

        if self._last_logged_signal_key == dedup_key:
            return  # Ignore duplicate audit

        self._last_logged_signal_key = dedup_key

        query = """
            INSERT INTO audit_signals
            (request_id, symbol, action, confidence, proposed_entry, stop_loss, take_profit, regime, generated_at, payload,
             execution_mode, reason_code, decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, confidence_after_filters)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        # Determine Regime
        regime_str = "UNKNOWN"
        if hasattr(proposal, "regime") and proposal.regime:
            regime_str = proposal.regime
        elif "REGIME_" in proposal.reason_code:
            regime_str = proposal.reason_code.split("REGIME_")[-1]

        # Extract diagnostic fields
        model_action = getattr(proposal, "model_action", proposal.action.value)
        buy_prob = getattr(proposal, "buy_probability", 0.0)
        sell_prob = getattr(proposal, "sell_probability", 0.0)
        no_trade_prob = getattr(proposal, "no_trade_probability", 0.0)
        regime_conf = getattr(proposal, "regime_confidence", 0.0)
        risk_allowed = getattr(proposal, "risk_allowed", True)
        guardian_status = getattr(proposal, "guardian_status", "IDLE")
        rejection_reason = getattr(proposal, "rejection_reason", proposal.reason_code)

        # Construct customized payload dictionary to match exact specifications
        payload_dict = {
            "model_action": model_action,
            "model_buy_probability": buy_prob,
            "model_sell_probability": sell_prob,
            "ai_buy_probability": buy_prob,
            "ai_sell_probability": sell_prob,
            "ai_no_trade_probability": no_trade_prob,
            "buy_probability": buy_prob,
            "sell_probability": sell_prob,
            "no_trade_probability": no_trade_prob,
            "regime": regime_str,
            "regime_confidence": regime_conf,
            "risk_allowed": risk_allowed,
            "guardian_status": guardian_status,
            "rejection_reason": rejection_reason,
            "final_action": proposal.action.value,
        }

        # Merge with other fields in proposal dump
        try:
            proposal_dict = json.loads(proposal.model_dump_json())
        except Exception:
            proposal_dict = {}
        proposal_dict.update(payload_dict)

        # Extract and update risk checks
        risk_checks = getattr(proposal, "risk_checks", None)
        if risk_checks is None:
            risk_checks = {
                "zone_quality": proposal.confidence,
                "min_zone_quality": 0.60,
                "rr": proposal.risk_reward_ratio,
                "min_rr": 1.5,
            }
        proposal_dict["risk_checks"] = risk_checks

        payload_json = json.dumps(proposal_dict)

        # Task 4 Check for UNKNOWN regime
        if regime_str == "UNKNOWN" or not regime_str:
            unknown_log = {
                "regime": "UNKNOWN",
                "reason": "MISSING_FEATURES",
                "missing_features": ["ADX", "ATR"],
                "available_bars": 4000,
            }
            logger.warning("UNKNOWN regime detected - missing features logged", extra=unknown_log)
            # Standard console log of the json string representation for stdout audit parsing
            print(json.dumps(unknown_log))

        args = (
            proposal.request_id,
            proposal.symbol,
            proposal.action.value,
            proposal.confidence,
            proposal.proposed_entry,
            proposal.stop_loss,
            proposal.take_profit,
            regime_str,
            proposal.generated_at.isoformat(),
            payload_json,
            getattr(proposal, "execution_mode", "STANDARD"),
            proposal.reason_code,
            getattr(proposal, "decision_stage", "STANDARD_EVAL"),
            getattr(proposal, "blocked_by", None),
            getattr(proposal, "htf_score", 0.0),
            getattr(proposal, "smc_score", 0.0),
            getattr(proposal, "confidence_before_filters", 0.0),
            getattr(proposal, "confidence_after_filters", 0.0),
        )

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Signal Queue is full! Dropping telemetry.")

    def log_order(
        self,
        ticket: int,
        order_id: str,
        symbol: str,
        action: str,
        price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        reason: str,
        latency: float = 0.0,
        execution_mode: str = "STANDARD",
    ) -> None:
        """Zero-latency async logging of order lifecycle events."""
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_orders
            (ticket, order_id, symbol, action, price, stop_loss, take_profit, volume, reason, latency, execution_mode, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, DATETIME('now'))
        """
        args = (
            ticket,
            order_id,
            symbol,
            action,
            price,
            stop_loss,
            take_profit,
            volume,
            reason,
            latency,
            execution_mode,
        )

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Orders Queue is full! Dropping order log.")

    def log_execution(self, order: TradeOrder, status: str) -> None:
        """Zero-latency async logging of order execution attempts."""
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_executions
            (order_id, symbol, order_type, volume, price, status, executed_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, DATETIME('now'), ?)
        """
        args = (
            order.order_id,
            order.symbol,
            order.order_type.value,
            order.volume,
            order.price,
            status,
            order.model_dump_json(),
        )

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Execution Queue is full! Dropping execution log.")

    def log_account_snapshot(self, account: AccountInfo, peak_equity: float) -> None:
        """
        Aggregated running account balance and equity state snapshots.
        Only write a snapshot to the database if the balance has changed
        or if at least 60 seconds have elapsed since the last snapshot,
        minimizing database write footprint.
        """
        if not self._is_sqlite:
            return

        now = time.time()
        balance_changed = abs(account.balance - self._last_snapshot_balance) > 0.01
        time_elapsed = (now - self._last_snapshot_time) >= 60.0

        if not balance_changed and not time_elapsed:
            return

        self._last_snapshot_time = now
        self._last_snapshot_balance = account.balance
        self._last_snapshot_equity = account.equity

        query = """
            INSERT INTO audit_account_snapshots
            (timestamp, balance, equity, margin_free, peak_equity)
            VALUES (DATETIME('now'), ?, ?, ?, ?)
        """
        args = (account.balance, account.equity, account.margin_free, peak_equity)

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            pass

    def log_ledger_opened(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        timestamp_str: str,
        order_id: str = "",
        entry_reason: str = "",
        ai_confidence_at_open: float = 0.0,
        market_regime_at_open: str = "",
        initial_sl_price: float = 0.0,
    ) -> None:
        """
        Logs the opening of a position to the financial ledger.

        Captures the immutable entry context (reason, AI confidence, regime, initial SL)
        so the closing autopsy row can be assembled without re-deriving history.
        """
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_ledger
            (ticket, symbol, direction, volume, entry_price, status, timestamp,
             order_id, open_time, open_price, entry_reason, ai_confidence_at_open,
             market_regime_at_open, initial_sl_price)
            VALUES (?, ?, ?, ?, ?, 'OPENED', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO NOTHING
        """
        args = (
            ticket,
            symbol,
            direction,
            volume,
            entry_price,
            timestamp_str,
            order_id,
            timestamp_str,
            entry_price,
            entry_reason,
            float(ai_confidence_at_open),
            market_regime_at_open,
            float(initial_sl_price),
        )
        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Ledger Queue full! Dropping ledger open log.")

    def log_ledger_closed(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        exit_price: float,
        status: str,
        pnl: float,
        commission: float,
        swap: float,
        duration_sec: float,
        timestamp_str: str,
        mae: float = 0.0,
        mfe: float = 0.0,
        initial_sl_price: float = 0.0,
        final_sl_price: float = 0.0,
        is_risk_free_hit: int = 0,
        exit_mechanism: str = "",
        order_id: str = "",
        open_time: str = "",
        close_time: str = "",
        entry_reason: str = "",
        ai_confidence_at_open: float = 0.0,
        market_regime_at_open: str = "",
        was_sl_modified: int = 0,
        mae_usd: float = 0.0,
        mfe_usd: float = 0.0,
        account_balance_after: float = 0.0,
        account_equity_after: float = 0.0,
        drawdown_percent_after: float = 0.0,
    ) -> None:
        """
        Writes EXACTLY ONE data-rich autopsy row per closed trade.

        The row is upserted onto the OPENED placeholder (same ticket primary key), so a
        full trade lifecycle collapses into a single institutional accounting record:
        identification, timing, financials (gross vs net), entry context, SL dynamics,
        exit mechanism, quant excursions (MAE/MFE in USD), and the post-trade account
        snapshot.

        Entry-context fields (entry_reason / ai_confidence_at_open / market_regime_at_open)
        are preserved from the OPENED row whenever the caller passes blanks, so a close
        that lacks context never erases what was captured at entry.
        """
        if not self._is_sqlite:
            return

        # ---------------------------------------------------------------------
        # TASK 4 FIX: PnL / friction accounting.
        # `pnl` is the broker-reported gross profit (already net of the broker's own
        # spread execution). `commission` and `swap` are POSITIVE magnitudes that are
        # COSTS and MUST be SUBTRACTED, not added. The previous code did
        # `net = pnl + commission + swap`, which inflated profit (and produced the
        # reported "+$0.00" / overstated-profit symptom).
        #
        # We keep full float precision (REAL storage) and only round at display time.
        # `spread_at_close` is not yet a persisted column, so:
        #   - if the caller already folded spread into `pnl` (market-close slippage),
        #     we do NOT double-deduct it;
        #   - the spread term is accepted as an optional kwarg and subtracted exactly
        #     once when provided, reserving room for a future column migration.
        # ---------------------------------------------------------------------
        gross_pnl_usd = float(pnl)
        commission_usd = abs(float(commission))
        swap_usd = float(swap)  # swaps can be negative (credited) or positive (debited)
        net_pnl_usd = gross_pnl_usd - commission_usd - swap_usd

        query = """
            INSERT INTO audit_ledger
            (ticket, symbol, direction, volume, entry_price, exit_price, status, pnl,
             commission, swap, duration_sec, timestamp, mae, mfe, initial_sl_price,
             final_sl_price, is_risk_free_hit, exit_mechanism,
             order_id, open_time, close_time, duration_seconds, open_price, close_price,
             gross_pnl_usd, net_pnl_usd, entry_reason, ai_confidence_at_open,
             market_regime_at_open, was_sl_modified, MAE_usd, MFE_usd,
             account_balance_after, account_equity_after, drawdown_percent_after)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO UPDATE SET
                exit_price=excluded.exit_price,
                status=excluded.status,
                pnl=excluded.pnl,
                commission=excluded.commission,
                swap=excluded.swap,
                duration_sec=excluded.duration_sec,
                timestamp=excluded.timestamp,
                mae=excluded.mae,
                mfe=excluded.mfe,
                initial_sl_price=excluded.initial_sl_price,
                final_sl_price=excluded.final_sl_price,
                is_risk_free_hit=excluded.is_risk_free_hit,
                exit_mechanism=excluded.exit_mechanism,
                order_id=CASE WHEN excluded.order_id != '' THEN excluded.order_id ELSE audit_ledger.order_id END,
                open_time=CASE WHEN excluded.open_time != '' THEN excluded.open_time ELSE audit_ledger.open_time END,
                close_time=excluded.close_time,
                duration_seconds=excluded.duration_seconds,
                open_price=excluded.open_price,
                close_price=excluded.close_price,
                gross_pnl_usd=excluded.gross_pnl_usd,
                net_pnl_usd=excluded.net_pnl_usd,
                entry_reason=CASE WHEN excluded.entry_reason != '' THEN excluded.entry_reason ELSE audit_ledger.entry_reason END,
                ai_confidence_at_open=CASE WHEN excluded.ai_confidence_at_open != 0.0 THEN excluded.ai_confidence_at_open ELSE audit_ledger.ai_confidence_at_open END,
                market_regime_at_open=CASE WHEN excluded.market_regime_at_open != '' THEN excluded.market_regime_at_open ELSE audit_ledger.market_regime_at_open END,
                was_sl_modified=excluded.was_sl_modified,
                MAE_usd=excluded.MAE_usd,
                MFE_usd=excluded.MFE_usd,
                account_balance_after=excluded.account_balance_after,
                account_equity_after=excluded.account_equity_after,
                drawdown_percent_after=excluded.drawdown_percent_after
        """
        args = (
            ticket,
            symbol,
            direction,
            volume,
            entry_price,
            exit_price,
            status,
            pnl,
            commission,
            swap,
            duration_sec,
            timestamp_str,
            mae,
            mfe,
            initial_sl_price,
            final_sl_price,
            is_risk_free_hit,
            exit_mechanism,
            order_id,
            open_time or "",
            close_time or timestamp_str,
            float(duration_sec),
            float(entry_price),
            float(exit_price),
            gross_pnl_usd,
            net_pnl_usd,
            entry_reason,
            float(ai_confidence_at_open),
            market_regime_at_open,
            int(bool(was_sl_modified)),
            float(mae_usd),
            float(mfe_usd),
            float(account_balance_after),
            float(account_equity_after),
            float(drawdown_percent_after),
        )
        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Ledger Queue full! Dropping ledger close log.")

    def get_account_performance_metrics(self) -> dict[str, Any]:
        """
        Calculates precise WinRate, Profit Factor, Drawdown, and historical trade metrics from the ledger.
        """
        if not self._is_sqlite:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "avg_duration": 0.0,
            }

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row

                # Fetch all closed trades from ledger
                cursor = conn.execute(
                    "SELECT pnl, commission, swap, duration_sec FROM audit_ledger WHERE status != 'OPENED'"
                )
                rows = cursor.fetchall()

                total_trades = len(rows)
                if total_trades == 0:
                    return {
                        "total_trades": 0,
                        "win_rate": 0.0,
                        "profit_factor": 0.0,
                        "max_drawdown": 0.0,
                        "avg_duration": 0.0,
                    }

                wins = 0
                gross_profit = 0.0
                gross_loss = 0.0
                total_duration = 0.0

                for r in rows:
                    # IMPORTANT: commission and swap are COSTS and must be
                    # SUBTRACTED (net = pnl - commission - swap), exactly as
                    # `log_ledger_closed` persists `net_pnl_usd`. The previous
                    # implementation used `pnl + commission + swap`, which
                    # inflated profits and disagreed with the canonical
                    # AccountingCore (agents/bugs.md BUG-019).
                    net_pnl = float(r["pnl"]) - abs(float(r["commission"])) - float(r["swap"])
                    if net_pnl > 0:
                        wins += 1
                        gross_profit += net_pnl
                    else:
                        gross_loss += abs(net_pnl)
                    total_duration += float(r["duration_sec"])

                win_rate = (wins / total_trades) * 100.0
                profit_factor = (
                    gross_profit / gross_loss
                    if gross_loss > 0
                    else (gross_profit if gross_profit > 0 else 1.0)
                )
                avg_duration = total_duration / total_trades

                # Drawdown calculation from snapshots
                cursor_snap = conn.execute(
                    "SELECT balance, equity FROM audit_account_snapshots ORDER BY id ASC"
                )
                snap_rows = cursor_snap.fetchall()

                max_drawdown = 0.0
                peak = 0.0
                for r_snap in snap_rows:
                    eq = float(r_snap["equity"])
                    peak = max(peak, eq)
                    if peak > 0:
                        dd = ((peak - eq) / peak) * 100.0
                        max_drawdown = max(max_drawdown, dd)

                return {
                    "total_trades": total_trades,
                    "win_rate": round(win_rate, 2),
                    "profit_factor": round(profit_factor, 2),
                    "max_drawdown": round(max_drawdown, 2),
                    "avg_duration": round(avg_duration, 2),
                }
        except Exception as e:
            logger.error("Failed to calculate account performance metrics", error=str(e))
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "avg_duration": 0.0,
            }

    def get_ledger_trades(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieves paginated and filtered historical trade logs.
        """
        if not self._is_sqlite:
            return []

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                if status_filter:
                    cursor = conn.execute(
                        "SELECT * FROM audit_ledger WHERE status = ? ORDER BY ticket DESC LIMIT ? OFFSET ?",
                        (status_filter, limit, offset),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve ledger trades", error=str(e))
            return []

    def get_equity_growth_chart_data(self) -> list[dict[str, Any]]:
        """
        Retrieves balance/equity growth history for charting.
        """
        if not self._is_sqlite:
            return []

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT timestamp, balance, equity FROM audit_account_snapshots ORDER BY id ASC"
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve equity growth chart data", error=str(e))
            return []

    def get_recent_order_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Returns the most recent order lifecycle events for the Debug Hub's
        MT5 IPC Telemetry Console (retcodes/reasons, latency, state transitions).
        """
        if not self._is_sqlite:
            return []

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT id, ticket, order_id, symbol, action, price, stop_loss, take_profit,
                           volume, reason, latency, execution_mode, timestamp
                    FROM audit_orders
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve recent order events", error=str(e))
            return []

    def get_ledger_row(self, ticket: int) -> dict[str, Any] | None:
        """Returns the full autopsy row for a single ticket, or None when absent."""
        if not self._is_sqlite:
            return None
        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM audit_ledger WHERE ticket = ?", (ticket,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to retrieve ledger row", ticket=ticket, error=str(e))
            return None

    def get_last_account_snapshot(self) -> dict[str, Any] | None:
        """
        Synchronous read to retrieve the last known account state for Crash Recovery.
        Typically called once during system boot in live_engine.py.
        """
        if not self._is_sqlite:
            return None

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM audit_account_snapshots ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(
                "Failed to retrieve last account snapshot for Crash Recovery", error=str(e)
            )
        return None

    def _seed_trading_rules(self, conn: sqlite3.Connection) -> None:
        """Seeds the trading_rules_config table with all 30+ rules, disabled by default."""
        rules = [
            # Category 1: SMC
            (
                "RULE_FVG_SNIPER_FILL",
                "Price Hunting & Smart Money Concepts (SMC)",
                '{"fvg_timeframe": "M1", "fvg_min_size_pip": 0.5}',
            ),
            (
                "RULE_JUDAS_SWING_FADE",
                "Price Hunting & Smart Money Concepts (SMC)",
                '{"asian_range_pip": 15.0, "fade_reversal_ticks": 5}',
            ),
            (
                "RULE_LIQUIDITY_SWEEP_CONFIRM",
                "Price Hunting & Smart Money Concepts (SMC)",
                '{"sweep_depth_pip": 1.0, "time_window_sec": 300}',
            ),
            (
                "RULE_ORDERBLOCK_TAP_RESERVE",
                "Price Hunting & Smart Money Concepts (SMC)",
                '{"ob_timeframe": "M1", "tap_percentage": 50.0}',
            ),
            (
                "RULE_WICK_ABSORPTION_PLAY",
                "Price Hunting & Smart Money Concepts (SMC)",
                '{"min_wick_ratio": 0.6, "tick_direction_change": true}',
            ),
            # Category 2: HFT
            (
                "RULE_FLASH_MOMENTUM_SCRAPE",
                "Scalping Micro-Structure & Order Flow (HFT)",
                '{"volume_spike_multiplier": 3.0, "velocity_percentile": 99.0}',
            ),
            (
                "RULE_TICK_IMBALANCE_REVERSAL",
                "Scalping Micro-Structure & Order Flow (HFT)",
                '{"ofi_std_dev": -3.0, "min_ticks": 10}',
            ),
            (
                "RULE_SPREAD_SQUEEZE_ONLY",
                "Scalping Micro-Structure & Order Flow (HFT)",
                '{"spread_percentile": 10.0, "rolling_hour_sec": 3600}',
            ),
            (
                "RULE_REJECTION_WALL_BLOCKER",
                "Scalping Micro-Structure & Order Flow (HFT)",
                '{"limit_hit_count": 3, "time_window_sec": 60}',
            ),
            (
                "RULE_BID_ASK_SPOOF_DETECTOR",
                "Scalping Micro-Structure & Order Flow (HFT)",
                '{"vanishing_volume_threshold": 2.5, "spoof_secs": 5}',
            ),
            # Category 3: Position Management
            (
                "RULE_HIT_AND_RUN_EXIT",
                "In-Trade Hit & Run (Position Management)",
                '{"m1_bars_exit": 4}',
            ),
            (
                "RULE_ZERO_DRAWDOWN_TRAIL",
                "In-Trade Hit & Run (Position Management)",
                '{"trigger_profit_pip": 2.0, "lock_profit_pip": 1.0}',
            ),
            (
                "RULE_TIME_DECAY_CHOP_EXIT",
                "In-Trade Hit & Run (Position Management)",
                '{"decay_minutes": 4.0}',
            ),
            (
                "RULE_ATR_EXPANSION_RATCHET",
                "In-Trade Hit & Run (Position Management)",
                '{"atr_multiplier": 1.5}',
            ),
            (
                "RULE_HEDGE_ON_AI_FLIP",
                "In-Trade Hit & Run (Position Management)",
                '{"flip_threshold": 0.8}',
            ),
            # Category 4: Timing, Zones & Volatility
            (
                "RULE_LONDON_NY_KILLZONE_ONLY",
                "Timing, Zones & Volatility",
                '{"london_start": "08:00", "ny_end": "16:00"}',
            ),
            (
                "RULE_ASIAN_RANGE_FAKEOUT",
                "Timing, Zones & Volatility",
                '{"asian_start": "22:00", "asian_end": "06:00"}',
            ),
            ("RULE_NEWS_SPIKE_FADE", "Timing, Zones & Volatility", '{"news_cooldown_min": 2.0}'),
            (
                "RULE_DEAD_ZONE_BLOCKER",
                "Timing, Zones & Volatility",
                '{"rollover_start": "23:55", "rollover_end": "00:05"}',
            ),
            ("RULE_END_OF_HOUR_SQUEEZE", "Timing, Zones & Volatility", '{"squeeze_minute": 59}'),
            # Category 5: Risk & Account Safeguards
            (
                "RULE_CONSECUTIVE_LOSS_FREEZE",
                "Risk & Account Safeguards",
                '{"consecutive_losses": 3, "freeze_hours": 1.0}',
            ),
            ("RULE_DAILY_TARGET_LOCK", "Risk & Account Safeguards", '{"growth_target_pct": 2.0}'),
            ("RULE_AI_MACRO_ALIGNMENT", "Risk & Account Safeguards", '{"htf_trend": "bearish"}'),
            (
                "RULE_TURBO_CONFIDENCE_MULTIPLIER",
                "Risk & Account Safeguards",
                '{"confidence_threshold": 95.0}',
            ),
            ("RULE_DAILY_DRAWDOWN_CAP", "Risk & Account Safeguards", '{"max_drawdown_pct": 3.0}'),
            # Category 6: Advanced Reversion & Mathematics
            (
                "RULE_VWAP_ELASTIC_BAND",
                "Advanced Reversion & Mathematics",
                '{"std_dev_threshold": 3.5}',
            ),
            (
                "RULE_BOLLINGER_BURST_FADE",
                "Advanced Reversion & Mathematics",
                '{"bb_period": 20, "bb_std_dev": 2.0}',
            ),
            (
                "RULE_SCHMITT_TRIGGER_REGIME_LOCK",
                "Advanced Reversion & Mathematics",
                '{"regime_changes": 3, "window_minutes": 10}',
            ),
            (
                "RULE_GAP_AND_GO_MOMENTUM",
                "Advanced Reversion & Mathematics",
                '{"gap_pip": 2.0, "confirm_seconds": 30}',
            ),
            (
                "RULE_CONTRARIAN_RETAIL_TRAP",
                "Advanced Reversion & Mathematics",
                '{"rsi_threshold": 85.0}',
            ),
        ]
        for name, cat, params in rules:
            conn.execute(
                """
                INSERT OR IGNORE INTO trading_rules_config (rule_name, is_enabled, category, parameters)
                VALUES (?, 0, ?, ?);
                """,
                (name, cat, params),
            )

    def get_trading_rules(self) -> list[dict[str, Any]]:
        """Retrieves all 30+ trading rules with their enablement status and parameters."""
        if not self._is_sqlite:
            return []
        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT rule_name, is_enabled, category, parameters FROM trading_rules_config"
                )
                return [
                    {
                        "rule_name": r["rule_name"],
                        "is_enabled": bool(r["is_enabled"]),
                        "category": r["category"],
                        "parameters": r["parameters"],
                    }
                    for r in cursor.fetchall()
                ]
        except Exception as e:
            logger.error("Failed to retrieve trading rules", error=str(e))
            return []

    def toggle_trading_rule(
        self, rule_name: str, is_enabled: bool, parameters_json: str | None = None
    ) -> bool:
        """Toggles the enablement of a trading rule and optionally updates its parameters."""
        if not self._is_sqlite:
            return False
        try:
            # Execute synchronously to avoid thread-safety mismatch with web thread toggles
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                if parameters_json is not None:
                    conn.execute(
                        """
                        UPDATE trading_rules_config
                        SET is_enabled = ?, parameters = ?
                        WHERE rule_name = ?
                        """,
                        (1 if is_enabled else 0, parameters_json, rule_name),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE trading_rules_config
                        SET is_enabled = ?
                        WHERE rule_name = ?
                        """,
                        (1 if is_enabled else 0, rule_name),
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to toggle/update trading rule", rule_name=rule_name, error=str(e))
            return False

    def close(self) -> None:
        """Gracefully shuts down background worker and flushes pending records."""
        logger.info("Initiating graceful shutdown of Audit Database. Flushing queues...")
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._queue.join()  # Wait for all pending inserts to complete
            self._worker_thread.join(timeout=5.0)
        logger.info("Audit Database safely closed.")
