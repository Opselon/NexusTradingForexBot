// Package handlers provides direct Go serving for feature contracts and config schema.
//
// These endpoints are classified as stateless-2xx in the routing dependency table,
// meaning they do not require Python DB/engine access and can be served directly from
// Go in-memory contracts with microsecond latency, falling back to Python if an issue
// is encountered.
package handlers

import (
	"errors"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/respond"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/infrastructure/python"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/routing"
)

// FeatureGroupIndices represents the count and index list for a feature group.
type FeatureGroupIndices struct {
	Count   int   `json:"count"`
	Indices []int `json:"indices"`
}

// FeatureContractGroups holds the three canonical groups for the 70D scalp_v3 contract.
type FeatureContractGroups struct {
	Base0To49       FeatureGroupIndices `json:"base_0_49"`
	News50To59      FeatureGroupIndices `json:"news_50_59"`
	Liquidity60To69 FeatureGroupIndices `json:"liquidity_60_69"`
}

// FeatureContractData is the payload for GET /api/v1/features/contract.
type FeatureContractData struct {
	SchemaID          string                `json:"schema_id"`
	FeatureCount      int                   `json:"feature_count"`
	FeatureSchemaHash string                `json:"feature_schema_hash"`
	RegistryCanonical bool                  `json:"registry_canonical"`
	Groups            FeatureContractGroups `json:"groups"`
	First10Names      []string              `json:"first_10_names"`
}

// FeatureFamilyGroup contains feature count and names for one family.
type FeatureFamilyGroup struct {
	Count int      `json:"count"`
	Names []string `json:"names"`
}

// FeatureFamilies holds the three feature families sorted alphabetically.
type FeatureFamilies struct {
	Base      FeatureFamilyGroup `json:"base"`
	Liquidity FeatureFamilyGroup `json:"liquidity"`
	News      FeatureFamilyGroup `json:"news"`
}

// FeatureGroupsData is the payload for GET /api/v1/features/groups.
type FeatureGroupsData struct {
	Dimension int             `json:"dimension"`
	Families  FeatureFamilies `json:"families"`
}

// ConfigSectionSchema describes one configuration section in the pydantic model schema.
type ConfigSectionSchema struct {
	Name     string   `json:"name"`
	Required bool     `json:"required"`
	Type     string   `json:"type"`
	Fields   []string `json:"fields,omitempty"`
}

// ConfigSchemaData is the payload for GET /api/v1/config/schema.
type ConfigSchemaData struct {
	Sections []ConfigSectionSchema `json:"sections"`
	Count    int                   `json:"count"`
	At       string                `json:"at"`
}

// Canonical feature names mirroring schema_contract.py.
var (
	BaseFeatureNames = []string{
		"upper_wick_ratio",
		"lower_wick_ratio",
		"body_to_range_ratio",
		"is_doji",
		"pinbar_sig",
		"engulfing_sig",
		"close_location_value",
		"consecutive_momentum_count",
		"norm_displacement",
		"rapid_reversal_spike_val",
		"dist_to_swing_high_20",
		"dist_to_swing_low_20",
		"price_compression_flag_ratio",
		"extreme_sig",
		"stop_hunt_depth",
		"liquidity_sweep_signal",
		"session_tokyo",
		"session_london",
		"session_ny",
		"session_overlap_london_ny",
		"lag_1_log_return",
		"lag_2_log_return",
		"lag_3_log_return",
		"lag_1_atr_ratio",
		"lag_1_volume_z",
		"lag_1_clv",
		"fvg_sig",
		"order_block_type",
		"choch_sig",
		"breakout_sig",
		"norm_tk_diff",
		"tk_cross_signal",
		"kumo_sig",
		"norm_kumo_width",
		"norm_rsi",
		"dist_to_ema_21",
		"dist_to_ema_50",
		"cross_asset_z_score",
		"norm_dist_to_tenkan",
		"norm_dist_to_kijun",
		"htf_h4_trend",
		"htf_h1_momentum",
		"htf_m30_structure",
		"htf_m15_confirmation",
		"support_zone_dist",
		"resistance_zone_dist",
		"feat_ob_valid_bos",
		"feat_ob_equilibrium_ratio",
		"feat_ob_liquidity_swept",
		"feat_ob_fib_50_60_alignment",
	}

	LiquidityFeatureNames = []string{
		"bsl_distance_atr",
		"ssl_distance_atr",
		"eqh_strength",
		"eql_strength",
		"htf_liquidity_score",
		"internal_liquidity_distance",
		"external_liquidity_distance",
		"liquidity_confluence",
		"liquidity_sweep_state",
		"post_sweep_displacement",
	}

	NewsFeatureNames = []string{
		"active_high_impact_events",
		"xauusd_relevance",
		"usd_relevance",
		"bullish_pressure",
		"bearish_pressure",
		"conflict_score",
		"novelty",
		"freshness",
		"confidence",
		"news_state",
	}
)

var featureContractData = FeatureContractData{
	SchemaID:          "scalp_v3",
	FeatureCount:      70,
	FeatureSchemaHash: "235b8fccc96b7e0e",
	RegistryCanonical: true,
	Groups: FeatureContractGroups{
		Base0To49: FeatureGroupIndices{
			Count:   0,
			Indices: []int{},
		},
		News50To59: FeatureGroupIndices{
			Count:   0,
			Indices: []int{},
		},
		Liquidity60To69: FeatureGroupIndices{
			Count:   0,
			Indices: []int{},
		},
	},
	First10Names: BaseFeatureNames[:10],
}

var featureGroupsData = FeatureGroupsData{
	Dimension: 70,
	Families: FeatureFamilies{
		Base: FeatureFamilyGroup{
			Count: 50,
			Names: BaseFeatureNames,
		},
		Liquidity: FeatureFamilyGroup{
			Count: 10,
			Names: LiquidityFeatureNames,
		},
		News: FeatureFamilyGroup{
			Count: 10,
			Names: NewsFeatureNames,
		},
	},
}

var configSchemaSections = []ConfigSectionSchema{
	{
		Name:     "execution",
		Required: false,
		Type:     "ExecutionConfig",
		Fields:   []string{"enabled_symbols", "magic_number", "max_slippage_points", "mode", "symbol", "timeframe"},
	},
	{
		Name:     "risk",
		Required: false,
		Type:     "RiskConfig",
		Fields:   []string{"enforce_stop_loss", "max_account_drawdown_pct", "max_allowed_lots", "max_concurrent_positions", "max_margin_usage_pct", "max_spread_points", "risk_per_trade_pct"},
	},
	{
		Name:     "paper_data",
		Required: false,
		Type:     "PaperDataConfig",
		Fields:   []string{"allow_raw_fallback", "dataset_id", "mode", "raw_bars_path"},
	},
	{
		Name:     "telegram",
		Required: false,
		Type:     "TelegramConfig",
		Fields:   []string{"admin_id", "bot_token", "enabled"},
	},
	{
		Name:     "mt5",
		Required: false,
		Type:     "MT5Config",
		Fields:   []string{"account", "password", "path", "portable_mode", "retries", "server", "timeout_ms"},
	},
	{
		Name:     "model",
		Required: false,
		Type:     "ModelConfig",
		Fields:   []string{"confidence_threshold", "feature_schema_version", "liquidity_features_enabled", "model_artifact_path"},
	},
	{
		Name:     "algo",
		Required: false,
		Type:     "AlgoConfig",
		Fields:   []string{"ai_flip_exit_enabled", "ai_flip_min_delta", "ai_flip_relative_bias_threshold", "ai_zone_confidence_threshold", "atr_sl_buffer_multiplier", "default_recovery_horizon_sec", "fvg_mitigation_sensitivity", "giveback_arm_r", "high_confidence_threshold", "max_recovery_horizon_sec", "max_spread_atr_ratio", "max_spread_pct_of_tp", "min_confirmation_duration", "min_observation_count", "min_recovery_horizon_sec", "min_risk_reward_ratio", "min_rr_high_confidence", "order_block_lookback_bars", "recovery_budget_pct_of_r", "regime_state_max_age_sec", "spread_session_gate_enabled", "spread_session_percentile", "trail_atr_multiplier", "w_drawdown_velocity", "w_hold_score", "w_market_reversal", "w_pnl_trajectory", "w_profit_retention", "w_recovery_probability"},
	},
	{
		Name:     "news",
		Required: false,
		Type:     "nexus_scalp.news.config.NewsConfig | None",
	},
	{
		Name:     "candle_intel",
		Required: false,
		Type:     "nexus_scalp.candle_intelligence.config.CandleIntelligenceConfig | None",
	},
	{
		Name:     "forensic_report",
		Required: false,
		Type:     "nexus_scalp.configuration.config.ForensicReportConfig | None",
	},
	{
		Name:     "database_hygiene",
		Required: false,
		Type:     "nexus_scalp.configuration.config.DatabaseHygieneConfig | None",
	},
	{
		Name:     "storage",
		Required: false,
		Type:     "nexus_scalp.configuration.storage_config.StorageConfig | None",
	},
	{
		Name:     "freshness",
		Required: false,
		Type:     "FreshnessConfig",
		Fields:   []string{"enabled", "max_age_sec"},
	},
	{
		Name:     "learning",
		Required: false,
		Type:     "nexus_scalp.model_lifecycle.learning_config.LearningConfig | None",
	},
}

func buildConfigSchemaData() ConfigSchemaData {
	return ConfigSchemaData{
		Sections: configSchemaSections,
		Count:    len(configSchemaSections),
		At:       respond.UTCNowISO(),
	}
}

// ContractsHandlers provides direct Go serving for feature contracts and config schema.
type ContractsHandlers struct {
	py            *python.Client
	forceFallback bool
}

// ContractsOption configures ContractsHandlers.
type ContractsOption func(*ContractsHandlers)

// WithPythonClient configures the Python client used for fallback.
func WithPythonClient(py *python.Client) ContractsOption {
	return func(h *ContractsHandlers) {
		h.py = py
	}
}

// WithForceFallback forces handlers to use the Python fallback path.
// This is primarily used for testing fallback behavior.
func WithForceFallback(force bool) ContractsOption {
	return func(h *ContractsHandlers) {
		h.forceFallback = force
	}
}

// NewContracts creates a new ContractsHandlers instance.
// Accepts py *python.Client for fallback forwarding, with optional ContractsOption parameters.
func NewContracts(py *python.Client, opts ...ContractsOption) *ContractsHandlers {
	h := &ContractsHandlers{py: py}
	for _, opt := range opts {
		opt(h)
	}
	return h
}

// FeatureContract serves GET /api/v1/features/contract directly from Go with Python fallback.
func (h *ContractsHandlers) FeatureContract(w http.ResponseWriter, r *http.Request) {
	routing.ServeWithFallback(w, r, func(w http.ResponseWriter, r *http.Request) error {
		if h.forceFallback {
			return errors.New("contracts: forced fallback to Python")
		}
		respond.OK(w, r, featureContractData)
		return nil
	}, h.fallback("/api/v1/features/contract"))
}

// Contract is an alias for FeatureContract.
func (h *ContractsHandlers) Contract(w http.ResponseWriter, r *http.Request) {
	h.FeatureContract(w, r)
}

// FeatureGroups serves GET /api/v1/features/groups directly from Go with Python fallback.
func (h *ContractsHandlers) FeatureGroups(w http.ResponseWriter, r *http.Request) {
	routing.ServeWithFallback(w, r, func(w http.ResponseWriter, r *http.Request) error {
		if h.forceFallback {
			return errors.New("contracts: forced fallback to Python")
		}
		respond.OK(w, r, featureGroupsData)
		return nil
	}, h.fallback("/api/v1/features/groups"))
}

// Groups is an alias for FeatureGroups.
func (h *ContractsHandlers) Groups(w http.ResponseWriter, r *http.Request) {
	h.FeatureGroups(w, r)
}

// ConfigSchema serves GET /api/v1/config/schema directly from Go with Python fallback.
func (h *ContractsHandlers) ConfigSchema(w http.ResponseWriter, r *http.Request) {
	routing.ServeWithFallback(w, r, func(w http.ResponseWriter, r *http.Request) error {
		if h.forceFallback {
			return errors.New("contracts: forced fallback to Python")
		}
		respond.OK(w, r, buildConfigSchemaData())
		return nil
	}, h.fallback("/api/v1/config/schema"))
}

// Schema is an alias for ConfigSchema.
func (h *ContractsHandlers) Schema(w http.ResponseWriter, r *http.Request) {
	h.ConfigSchema(w, r)
}

func (h *ContractsHandlers) fallback(path string) func(w http.ResponseWriter, r *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		if h.py == nil || !h.py.Configured() {
			respond.FailDependencyUnavailable(w, r, "schema contract unavailable")
			return
		}
		raw, err := h.py.DoRaw(r.Context(), http.MethodGet, path, nil)
		if err != nil {
			if be, ok := python.AsBoundary(err); ok {
				respond.ReplayBoundary(w, r, be)
				return
			}
			if lr, ok := python.AsLegacy(err); ok {
				writeRawJSONStatus(w, lr.Status, lr.Body)
				return
			}
			respond.FailDependencyUnavailable(w, r, "schema contract unavailable")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(raw)
	}
}
