# LABEL QUALITY CONTRACT

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_generation/label_quality.py` (`LabelQualityAudit`)  
**Scope:** Verification of causal Triple Barrier labels before sequence building and model training.

---

## 1. Principle

Financial machine learning targets must obey strict causal boundaries.
The labeling engine constructs target classes (0: `NO_TRADE`, 1: `BUY`, 2: `SELL`) via the canonical Triple Barrier method.

Before any dataset can be used to fit a model, it must pass the Label Quality Audit to guarantee that label distributions are balanced, non-collapsed, and temporally consistent across folds and market regimes.

---

## 2. Gate Verification Requirements

### 2.1 Causal Barrier Definitions
- **Horizon ($H$):** Maximum holding period in bars.
- **Barriers:** Top barrier ($+k \times \text{ATR}$ for BUY) and bottom barrier ($-k \times \text{ATR}$ for SELL).
- **Friction:** Dynamic spread and fee threshold required to confirm a positive trade return.
- **Embargo:** Post-trade cooling period ($N$ bars) to prevent overlapping event leakage.

### 2.2 Class Balance & Collapse Checks
- **Class Presence:** Both BUY and SELL classes must be present in sufficient quantities ($\ge 5\%$ of non-NO_TRADE volume).
- **NO_TRADE Dominance:** If `NO_TRADE` exceeds $96\%$ of total samples, the dataset is marked as collapsed.
- **Directional Collapse:** Rejection of datasets where one trade direction is completely absent ($0\%$).

### 2.3 Temporal and Fold Distribution Checks
- **Fold Consistency:** The spread of positive class ratios between temporal walk-forward folds must not exceed $35\%$.
- **Regime Dominance:** No single trade direction may account for $\ge 98\%$ of opportunities within any single regime (e.g. `TREND_BULL`, `TREND_BEAR`, `RANGE`).
- **Decile Concentration:** Labels must not be unnaturally clustered within a single time decile ($> 50\%$ concentration indicates news shock or feed artifact).

---

## 3. Machine-Readable Label Report Schema

Every certification pass produces a `LabelQualityReport` serialized as JSON (`<model_id>.label_quality.json`):

```json
{
  "total_rows": 10000,
  "labeled_rows": 10000,
  "label_density": 1.0,
  "class_distribution": {
    "NO_TRADE": 7800,
    "BUY": 1150,
    "SELL": 1050
  },
  "no_trade_percentage": 0.78,
  "by_fold": {
    "train": { "NO_TRADE": 5500, "BUY": 800, "SELL": 700 },
    "val": { "NO_TRADE": 1150, "BUY": 180, "SELL": 170 },
    "test": { "NO_TRADE": 1150, "BUY": 170, "SELL": 180 }
  },
  "by_regime": {},
  "by_session": {},
  "collapsed_classes": [],
  "regime_collapse": [],
  "quality_status": "PASS",
  "warnings": []
}
```

---

## 4. Certification Verdicts

| Status | Condition | Pipeline Behavior |
|--------|-----------|-------------------|
| `PASS` | All three classes active, fold spread within limits, no regime collapse | Proceeds to training |
| `WARN` | Mild class imbalance or minor time decile clustering | Proceeds with recorded warnings |
| `FAIL` | Zero BUY/SELL labels, NO_TRADE $> 96\%$, or regime collapse | Training strictly aborted |
