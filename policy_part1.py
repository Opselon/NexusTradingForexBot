    def evaluate_probabilities(
        self,
        probabilities: torch.Tensor,
        current_tick: TickData,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
        survival_mode: bool = False,
        force_log: bool = False,
        order_manager: Any = None,
        completed_bars: list[Any] | None = None,
    ) -> TradeProposal:
        """
        Evaluates conditions at maximum live speed (50ms hot path) and outputs a sized TradeProposal.
        """
        # Forensic execution trace id (PHASE 13 audit, 2026-08-20): ONE id per
        # evaluation, stamped BEFORE any gate, carried into every proposal the
        # policy emits (NO_TRADE included) so logs + audit rows + dispatch are
        # joinable by a single EXEC-... key. Observability only (INV-018) —
        # never influences a decision.
        now_exec = current_tick.timestamp
        execution_id = f"EXEC-{now_exec:%Y%m%d}-{now_exec:%H%M%S}-{uuid.uuid4().hex[:6]}"
        # Authoritative Regime Guardian Gate early in evaluation pipeline
        is_guardian_active = False
        if regime_state is not None:
            regime_type = regime_state.regime_type
            exec_type = regime_state.recommended_execution_type

            # Unsafe regimes to block
            UNSAFE_REGIMES = {
                "HIGH_SPREAD_CHOP",
                "UNKNOWN",
                "MARKET_HALTED",
                "LOW_LIQUIDITY",
                "NEWS_LOCK",
                "MACRO_NEWS_FREEZE",
            }
            reg_val = getattr(regime_type, "value", str(regime_type))
            if reg_val in UNSAFE_REGIMES or exec_type == RecommendedExecutionType.FREEZE_ALL:
                is_guardian_active = True

        if is_guardian_active:
            # Return detailed NO_TRADE proposal containing 'BLOCKED_BY_GUARDIAN'
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.NO_TRADE,
                confidence=0.0,
                proposed_entry=current_tick.bid,
                stop_loss=current_tick.bid * 0.99,
                take_profit=current_tick.bid * 1.01,
                risk_reward_ratio=1.0,
                reason_code="BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
                model_action="NO_TRADE",
                buy_probability=0.0,
                sell_probability=0.0,
                no_trade_probability=1.0,
                regime=str(regime_state.regime_type.value if regime_state else "UNKNOWN"),
                regime_confidence=float(regime_state.regime_probability if regime_state else 0.0),
                risk_allowed=False,
                guardian_status="ACTIVE",
                rejection_reason="BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
                final_action="NO_TRADE",
                decision_stage="GUARDIAN_GATE",
                blocked_by="REGIME_GUARDIAN",
                htf_score=0.0,
                smc_score=0.0,
                confidence_before_filters=0.0,
                confidence_after_filters=0.0,
            )

        probs = probabilities.squeeze().tolist()
        if not isinstance(probs, list):
            probs = [probs]

        # ---------------------------------------------------------------------
        # TASK 5 FIX: micro-throttler / tick de-duplication.
        # The hot path (50ms) is frequently invoked faster than the market feed
        # produces quotes, so the same tick (identical timestamp, or identical
        # bid AND ask) gets re-evaluated and re-logged, corrupting telemetry.
        # We detect a duplicate and return a lightweight NO_TRADE proposal WITHOUT
        # touching the persistent state (cooldown, last direction, price locks).
        # ---------------------------------------------------------------------
        tick_ts = current_tick.timestamp
        tick_bid = float(getattr(current_tick, "bid", 0.0) or 0.0)
        tick_ask = float(getattr(current_tick, "ask", 0.0) or 0.0)
        is_duplicate = (
            tick_ts is not None
            and self._dedup_last_time is not None
            and tick_ts == self._dedup_last_time
        ) or (
            tick_bid == self._dedup_last_bid and tick_ask == self._dedup_last_ask and tick_bid > 0.0
        )
        if is_duplicate:
            _pb = probs[1] if len(probs) > 1 else 0.0
            _ps = probs[2] if len(probs) > 2 else 0.0
            _pnt = probs[0] if len(probs) > 0 else 0.0
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.NO_TRADE,
                confidence=0.0,
                proposed_entry=current_tick.bid,
                stop_loss=current_tick.bid * 0.99,
                take_profit=current_tick.bid * 1.01,
                risk_reward_ratio=1.0,
                reason_code="TICK_DUPLICATE_SUPPRESSED",
                model_action="NO_TRADE",
                buy_probability=float(_pb),
                sell_probability=float(_ps),
                no_trade_probability=float(_pnt),
                regime=str(regime_state.regime_type.value if regime_state else "UNKNOWN"),
                regime_confidence=float(regime_state.regime_probability if regime_state else 0.0),
                risk_allowed=False,
                guardian_status="IDLE",
                rejection_reason="TICK_DUPLICATE_SUPPRESSED",
                final_action="NO_TRADE",
                decision_stage="DEDUP_GATE",
                blocked_by="TICK_DEDUP",
                htf_score=0.0,
                smc_score=0.0,
                confidence_before_filters=0.0,
                confidence_after_filters=0.0,
            )

        # Record the freshest tick signature for the next call.
        self._dedup_last_time = tick_ts
        self._dedup_last_bid = tick_bid
        self._dedup_last_ask = tick_ask

        raw_prob_buy = probs[1] if len(probs) > 1 else 0.0
        raw_prob_sell = probs[2] if len(probs) > 2 else 0.0

        # --- PRE-COMPUTE CHANNELS AND PARAMETERS UPFRONT FOR DIAGNOSTICS ---
        prob_buy = self._sanitize_float(raw_prob_buy, 0.0)
        prob_sell = self._sanitize_float(raw_prob_sell, 0.0)
        prob_no_trade = probs[0] if len(probs) > 0 else 0.0

        now = current_tick.timestamp
        target_entry_price = current_tick.ask
        proposed_action = ActionType.NO_TRADE

        raw_atr = getattr(feature_vector, "atr_m1", 1.50)
        atr = max(self._sanitize_float(raw_atr, 1.50), 0.50)
        current_spread = round(max(0.0, current_tick.ask - current_tick.bid), 2)

        regime_type = regime_state.regime_type if regime_state else None
        regime_str = regime_type.value if regime_type else "UNKNOWN"
        regime_conf = float(regime_state.regime_probability) if regime_state else 0.0

        # Count active open positions and active pending orders matching symbol and magic
        active_positions_count = 0
        active_pending_count = 0
        pending_price = None
        pending_ticket = None
        live_tickets: list[Any] = []
        #: Held direction(s) of currently open positions, used by the AI Reversal veto.
        held_position_dirs: dict[int, str] = {}

        if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
            live_tickets = order_manager.get_active_live_tickets()
            for ticket_info in live_tickets:
                t_symbol = ticket_info.get("symbol")
                t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                t_type = ticket_info.get("type")
                if t_symbol == "XAUUSD" and t_magic == 888101:
                    if t_type == "POSITION":
                        active_positions_count += 1
                        t_ticket = ticket_info.get("ticket")
                        t_dir = str(ticket_info.get("direction") or "").upper()
                        if t_ticket is not None and t_dir:
                            held_position_dirs[int(t_ticket)] = t_dir
                    elif t_type == "PENDING":
                        active_pending_count += 1
                        pending_price = ticket_info.get("price")
                        pending_ticket = ticket_info.get("ticket")

        # Initialize lock attributes if not present
        if not hasattr(self, "_locked_pending_ticket"):
            self._locked_pending_ticket = None
        if not hasattr(self, "_locked_pending_price"):
            self._locked_pending_price = None
        if not hasattr(self, "_locked_pending_time"):
            self._locked_pending_time = None
        if not hasattr(self, "_last_signal_time"):
            self._last_signal_time = None

        # Lock tracking
        if pending_ticket is not None:
            if self._locked_pending_ticket != pending_ticket:
                self._locked_pending_ticket = pending_ticket
                self._locked_pending_price = pending_price
                self._locked_pending_time = now
        else:
            self._locked_pending_ticket = None
            self._locked_pending_price = None
            self._locked_pending_time = None

        # ======================================================================
        # MODULE B: AI POSITION REVERSAL PROTOCOL (evaluated FIRST)
        # ======================================================================
        # If we hold an active position and the model now argues strongly for the
        # opposite direction, we must NOT stack an opposing order. We emit
        # CLOSE_POSITION with reason AI_REVERSAL_SIGNAL; order_manager closes the
        # ticket, stamps exit_mechanism=AI_REVERSAL_EXIT in the ledger, and only then
        # dispatches the new directional order.
        #
        # This gate runs before the frequency throttle, the same-level re-entry lockout
        # and the exposure gate: closing a position that the model has turned against
        # is risk-reducing and must never be suppressed by an entry-side filter.
        if active_positions_count >= 1 and held_position_dirs:
            reversal_proposal = self._evaluate_ai_reversal(
                current_tick=current_tick,
                feature_vector=feature_vector,
                held_position_dirs=held_position_dirs,
                prob_buy=prob_buy,
                prob_sell=prob_sell,
                atr=atr,
                regime_str=regime_str,
                regime_conf=regime_conf,
            )
            if reversal_proposal is not None:
                self._last_signal_time = now
                self._last_active_direction = reversal_proposal.reversal_action
                self._last_active_direction_time = now
                return reversal_proposal

        # 1. Enforce ORDER_FREQUENCY_THROTTLED check (MIN_ORDER_INTERVAL_SECONDS = 60)
        if self._last_signal_time is not None:
            elapsed = (now - self._last_signal_time).total_seconds()
            if elapsed < 60.0:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="ORDER_FREQUENCY_THROTTLED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                )

        # 2. Strict Single-Position Exposure Gate (MAX_TOTAL_EXPOSURE = 1)
        # Total of Active Open Positions + Active Pending Orders MUST NOT exceed 1.
        total_exposure = active_positions_count + active_pending_count

        if total_exposure >= MAX_TOTAL_EXPOSURE:
            # Check price proximity to find if we should return SAME_LEVEL_REENTRY_BLOCKED (threshold is $0.50)
            is_same_level = False
            if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
                for ticket_info in live_tickets:
                    t_symbol = ticket_info.get("symbol")
                    t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                    t_price = ticket_info.get("price")
                    if t_symbol == "XAUUSD" and t_magic == 888101 and t_price is not None:
                        if abs(target_entry_price - t_price) < 0.50:
                            is_same_level = True
                            break

            if is_same_level:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="SAME_LEVEL_REENTRY_BLOCKED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                )

            # If we hold 1 active open position, block entries.
            # blocked_by=EXECUTION_STATE_BLOCK: an execution-state block, NOT
            # a model rejection — the learning engine must not learn "the
            # model chose not to trade" from an unavailable execution slot.
            if active_positions_count >= 1:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="MAX_EXPOSURE_REACHED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                    blocked_by="EXECUTION_STATE_BLOCK",
                    decision_stage="EXPOSURE_GATE",
                )

            # If we hold 1 active pending order, check lock & price drift hysteresis
            if active_pending_count >= 1:
                # Calculate 50% Equilibrium price
                swing_low_20 = current_tick.bid - atr
                swing_high_20 = current_tick.ask + atr
                if completed_bars is not None and len(completed_bars) >= 20:
                    swing_low_20 = np.min([b.low for b in completed_bars[-20:]])
                    swing_high_20 = np.max([b.high for b in completed_bars[-20:]])
                new_eq_price = round(swing_low_20 + 0.50 * (swing_high_20 - swing_low_20), 2)

                time_delta = (
                    (now - self._locked_pending_time).total_seconds()
                    if self._locked_pending_time is not None
                    else 0.0
                )
                price_drift = (
                    abs(new_eq_price - self._locked_pending_price)
                    if self._locked_pending_price is not None
                    else 0.0
                )

                # 30-SECOND PENDING LOCK: never cancel/recreate a live limit order unless
                # it has been resting for more than 30s AND price has drifted >= 1.0 x ATR.
                if time_delta <= PENDING_ORDER_LOCK_SECONDS or price_drift < (1.0 * atr):
                    # Maintain the existing live limit order and return ActionType.NO_TRADE
                    # (execution-state block, not model rejection).
                    return self._build_no_trade(
                        tick=current_tick,
                        confidence=0.0,
                        reason="PENDING_ORDER_LOCKED",
                        regime_str=regime_str,
                        regime_conf=regime_conf,
                        blocked_by="EXECUTION_STATE_BLOCK",
                        decision_stage="EXPOSURE_GATE",
                    )

        tenkan = self._sanitize_float(feature_vector.tenkan_sen, current_tick.ask)
        kijun = self._sanitize_float(feature_vector.kijun_sen, current_tick.bid)
        disp = self._sanitize_float(feature_vector.live_tick_displacement, 0.0)

        ichimoku_bullish = feature_vector.is_above_kumo and (tenkan >= kijun)
        ichimoku_bearish = feature_vector.is_below_kumo and (tenkan <= kijun)

        z_score = self._sanitize_float(getattr(feature_vector, "cross_asset_z_score", 0.0), 0.0)
        abs_z = abs(z_score)
        z_score_confidence = min(0.95, round(0.40 + (abs_z / 4.0) * 0.55, 2))

        trend_strength = self._sanitize_float(getattr(feature_vector, "trend_strength", 0.0), 0.0)

        stat_arb_bullish = (z_score <= -2.0) and not ichimoku_bearish and (trend_strength >= -0.20)
        stat_arb_bearish = (z_score >= 2.0) and not ichimoku_bullish and (trend_strength <= 0.20)

        regime_type = regime_state.regime_type if regime_state else None
        exec_type = regime_state.recommended_execution_type if regime_state else None
        raw_ofi = regime_state.order_flow_imbalance if regime_state else 0.0
        ofi = self._sanitize_float(raw_ofi, 0.0)
        tick_velocity = regime_state.tick_velocity_per_sec if regime_state else 0.0

        sweep_sig = getattr(feature_vector, "liquidity_sweep_signal", 0)
        choch_bull = getattr(feature_vector, "choch_bullish", False)
        choch_bear = getattr(feature_vector, "choch_bearish", False)

        dynamic_min_displacement = max(self.range_min_displacement, atr * 0.12)
        tk_distance = abs(tenkan - kijun)
        is_inside_kumo = not feature_vector.is_above_kumo and not feature_vector.is_below_kumo
        small_displacement = abs(disp) < dynamic_min_displacement

        is_range_market = (
            (regime_type == RegimeType.RANGING_MEAN_REVERSION)
            or is_inside_kumo
            or (tk_distance < (atr * 0.20) and small_displacement)
        )

        if is_range_market and abs(ofi) >= 0.15:
            is_range_market = False

        ict_bullish = (
            feature_vector.fvg_bullish_active or feature_vector.order_block_type == 1 or choch_bull
        )
        ict_bearish = (
            feature_vector.fvg_bearish_active or feature_vector.order_block_type == -1 or choch_bear
        )

        moving_up = disp > dynamic_min_displacement or feature_vector.broke_previous_high
        moving_down = disp < -dynamic_min_displacement or feature_vector.broke_previous_low

        total_ai_prob = prob_buy + prob_sell + 1e-8
        relative_buy_bias = prob_buy / total_ai_prob
        relative_sell_bias = prob_sell / total_ai_prob
        high_velocity_momentum = tick_velocity >= 10.0

        regime_str = regime_type.value if regime_type else "UNKNOWN"
        regime_conf = regime_state.regime_probability if regime_state else 0.0

        is_guardian_active = bool(
            regime_state
            and (
                regime_type in (RegimeType.MACRO_NEWS_FREEZE, RegimeType.HIGH_SPREAD_CHOP)
                or exec_type == RecommendedExecutionType.FREEZE_ALL
            )
        )
        guardian_status = "ACTIVE" if is_guardian_active else "IDLE"

        # Initialize execution metadata
        execution_mode = "STANDARD"
        override_reason = None
        blocked_by = None
        decision_stage = "STANDARD_EVAL"

        # Pre-compute original unfiltered candidate model action
        cand_action = "NO_TRADE"
        if (sweep_sig == 1 or choch_bull) and (relative_buy_bias > 0.45 or prob_buy >= 0.30):
            cand_action = "BUY_MARKET"
        elif (sweep_sig == -1 or choch_bear) and (relative_sell_bias > 0.45 or prob_sell >= 0.30):
            cand_action = "SELL_MARKET"
        elif (ichimoku_bullish or stat_arb_bullish) and (
            moving_up or ict_bullish or relative_buy_bias > 0.50 or stat_arb_bullish
        ):
