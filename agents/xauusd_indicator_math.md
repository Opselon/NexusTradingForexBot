# XAUUSD M1 — exact math for every indicator

Notation: `H,L,C,O` = High/Low/Close/Open of a bar; subscript `t` = current bar, `t-1` = previous bar. `HH(n)` / `LL(n)` = highest high / lowest low over the last `n` bars including the current one.

---

## A. Oscillators (11)

### 1. RSI (14) — Wilder's smoothing
```
ΔC_t     = C_t − C_{t-1}
Gain_t   = max(ΔC_t, 0)
Loss_t   = max(−ΔC_t, 0)

Seed (bar 14):
  AvgGain_14 = (1/14) · Σ Gain_i   for i = 1..14
  AvgLoss_14 = (1/14) · Σ Loss_i   for i = 1..14

Recursive (bar t > 14):
  AvgGain_t = (AvgGain_{t-1} · 13 + Gain_t) / 14
  AvgLoss_t = (AvgLoss_{t-1} · 13 + Loss_t) / 14

RS  = AvgGain_t / AvgLoss_t
RSI = 100 − 100 / (1 + RS)
```
Vote: `RSI > 70 → Sell` · `RSI < 30 → Buy` · else `Neutral`

### 2. Stochastic %K (14, 3, 3)
```
FastK_t = 100 · (C_t − LL(14)) / (HH(14) − LL(14))
SlowK_t = SMA(FastK, 3)     ← this is the displayed "%K"
%D_t    = SMA(SlowK, 3)
```
Vote: `SlowK > 80 → Sell` · `SlowK < 20 → Buy` · else `Neutral`

### 3. Commodity Channel Index (20)
```
TP_t   = (H_t + L_t + C_t) / 3
SMA20  = SMA(TP, 20)
MD_20  = (1/20) · Σ |TP_i − SMA20|   for the last 20 bars
CCI_t  = (TP_t − SMA20) / (0.015 · MD_20)
```
Vote: `CCI > 100 → Sell` · `CCI < −100 → Buy` · else `Neutral`

### 4. Average Directional Index (14) — with +DI / −DI
```
UpMove_t   = H_t − H_{t-1}
DownMove_t = L_{t-1} − L_t

+DM_t = UpMove_t     if UpMove_t > DownMove_t and UpMove_t > 0   else 0
−DM_t = DownMove_t   if DownMove_t > UpMove_t and DownMove_t > 0 else 0

TR_t = max(H_t−L_t, |H_t−C_{t-1}|, |L_t−C_{t-1}|)

Wilder-smoothed (same recursion pattern as RSI, period 14):
  STR_t   = STR_{t-1}   − STR_{t-1}/14   + TR_t
  S+DM_t  = S+DM_{t-1}  − S+DM_{t-1}/14  + (+DM_t)
  S−DM_t  = S−DM_{t-1}  − S−DM_{t-1}/14  + (−DM_t)

+DI_t = 100 · S+DM_t / STR_t
−DI_t = 100 · S−DM_t / STR_t
DX_t  = 100 · |+DI_t − −DI_t| / (+DI_t + −DI_t)

ADX seed (bar 14) = simple average of first 14 DX values
ADX_t (t>14) = (ADX_{t-1} · 13 + DX_t) / 14
```
Vote: `ADX < 20 → Neutral` (trend too weak) · else `+DI > −DI → Buy`, `−DI > +DI → Sell`

### 5. Awesome Oscillator
```
MP_t = (H_t + L_t) / 2
AO_t = SMA(MP, 5) − SMA(MP, 34)
```
Vote: `AO_t > 0 and AO_t > AO_{t-1} → Buy` · `AO_t < 0 and AO_t < AO_{t-1} → Sell` · else `Neutral`

### 6. Momentum (10)
```
Momentum_t = C_t − C_{t-10}
```
Vote: `> 0 → Buy` · `< 0 → Sell` · `= 0 → Neutral`

### 7. MACD Level (12, 26), signal 9
```
EMA12_t = EMA(C, 12)
EMA26_t = EMA(C, 26)
MACD_t  = EMA12_t − EMA26_t
Signal_t = EMA(MACD, 9)
Hist_t   = MACD_t − Signal_t
```
Vote: `MACD_t > Signal_t → Buy` · `MACD_t < Signal_t → Sell`

### 8. Stochastic RSI Fast (3, 3, 14, 14)
```
RSI_t computed as in §1 (period 14)
StochRSI_t = 100 · (RSI_t − LL(RSI,14)) / (HH(RSI,14) − LL(RSI,14))
%K_t = SMA(StochRSI, 3)
%D_t = SMA(%K, 3)
```
Vote: `%K > 80 → Sell` · `%K < 20 → Buy` · else `Neutral`

### 9. Williams %R (14)
```
%R_t = (HH(14) − C_t) / (HH(14) − LL(14)) · (−100)     // canonical range: −100..0
```
Vote (canonical scale): `%R > −20 → Sell` (overbought) · `%R < −80 → Buy` (oversold) · else `Neutral`
Note: some platforms display `%R + 100` (0..100 scale) instead — same thresholds shift to `>80` / `<20`. Confirm which scale your data source reports before wiring up the threshold.

### 10. Bull Bear Power (Elder Ray)
```
EMA13_t = EMA(C, 13)
BullPower_t = H_t − EMA13_t
BearPower_t = L_t − EMA13_t
BBP_t = BullPower_t + BearPower_t      // combined single value, as shown in the panel
```
Vote: `BBP_t > 0 → Buy` · `BBP_t < 0 → Sell`

### 11. Ultimate Oscillator (7, 14, 28)
```
BP_t = C_t − min(L_t, C_{t-1})
TR_t = max(H_t, C_{t-1}) − min(L_t, C_{t-1})

Avg7  = Σ(BP, 7)  / Σ(TR, 7)
Avg14 = Σ(BP, 14) / Σ(TR, 14)
Avg28 = Σ(BP, 28) / Σ(TR, 28)

UO_t = 100 · (4·Avg7 + 2·Avg14 + 1·Avg28) / (4 + 2 + 1)
```
Vote: `UO > 70 → Sell` · `UO < 30 → Buy` · else `Neutral`

---

## B. Moving averages & baselines (15)

### Simple Moving Average — SMA(n)
```
SMA(n)_t = (1/n) · Σ C_{t-i}    for i = 0..n-1
```
Computed for n = 10, 20, 30, 50, 100, 200.

### Exponential Moving Average — EMA(n)
```
k = 2 / (n + 1)
EMA(n)_t = C_t · k + EMA(n)_{t-1} · (1 − k)
Seed: EMA(n)_n = SMA(n) over the first n closes
```
Computed for n = 10, 20, 30, 50, 100, 200.

### Ichimoku Base Line (Kijun-sen), params (9, 26, 52, 26)
```
BaseLine_t = (HH(26) + LL(26)) / 2
```
(the 9 and 52 belong to the Conversion Line and Leading Span B, not the Base Line itself; 26 is also the displacement)

### Volume Weighted Moving Average — VWMA(20)
```
VWMA_t = Σ (C_{t-i} · Vol_{t-i}) / Σ Vol_{t-i}    for i = 0..19
```

### Hull Moving Average — HMA(9)
```
WMA(n)_t = Σ [(n-i) · C_{t-i}] / Σ(n-i)    for i = 0..n-1   (linear weights, most recent = weight n)

HMA(n)_t = WMA( 2·WMA(round(n/2))_t − WMA(n)_t ,  round(√n) )
```
For n = 9: half-length = round(4.5) = 5 (round-half-up convention), √9 = 3 exactly.

**Vote rule — identical for all 15 above:**
```
C_t > MA_t → Buy
C_t < MA_t → Sell
```

---

## C. Pivot levels — 5 systems

All use the prior completed period's `H, L, C` (and `O` for DeMark) — conventionally the prior **day's** OHLC, even on an M1 chart.

### Classic
```
P  = (H + L + C) / 3
R1 = 2P − L        S1 = 2P − H
R2 = P + (H − L)   S2 = P − (H − L)
R3 = H + 2(P − L)  S3 = L − 2(H − P)
```

### Fibonacci
```
P  = (H + L + C) / 3
R1 = P + 0.382(H−L)   S1 = P − 0.382(H−L)
R2 = P + 0.618(H−L)   S2 = P − 0.618(H−L)
R3 = P + 1.000(H−L)   S3 = P − 1.000(H−L)
```

### Camarilla
```
R1 = C + (H−L)·1.1/12   S1 = C − (H−L)·1.1/12
R2 = C + (H−L)·1.1/6    S2 = C − (H−L)·1.1/6
R3 = C + (H−L)·1.1/4    S3 = C − (H−L)·1.1/4
```
(P is usually just (H+L+C)/3 for reference — Camarilla's R/S don't depend on it)

### Woodie
```
P  = (H + L + 2C) / 4
R1 = 2P − L         S1 = 2P − H
R2 = P + (H − L)    S2 = P − (H − L)
R3 = H + 2(P − L)   S3 = L − 2(H − P)
```

### DeMark
```
if C < O:  X = H + 2L + C
if C > O:  X = 2H + L + C
if C = O:  X = H + L + 2C

P  = X / 4
R1 = X/2 − L
S1 = X/2 − H
```
(DeMark defines only P, R1, S1 — no R2/R3/S2/S3, which is why those cells read "—")

---

## D. Summary aggregation math (how 26 signals collapse into Sell / Neutral / Buy)

Map each indicator's vote to a number:
```
Buy → +1     Neutral → 0     Sell → −1
```
Then average within each group:
```
OscillatorsRating = (Σ votes over the 11 oscillators) / 11
MARating          = (Σ votes over the 15 MAs/baselines) / 15
OverallRating     = (Σ votes over all 26) / 26
```
Bucket the rating into a label:
```
Rating ≤ −0.5           → Strong Sell
−0.5 < Rating ≤ −0.1     → Sell
−0.1 < Rating <  0.1     → Neutral
 0.1 ≤ Rating <  0.5     → Buy
Rating ≥  0.5            → Strong Buy
```
Check against the panel's own numbers: Oscillators (Sell 4, Neutral 6, Buy 1) → (−4+1)/11 = −0.273 → **Sell** ✓. Moving Averages (Sell 7, Neutral 0, Buy 8) → (−7+8)/15 = +0.067 → **Neutral** ✓. Overall (Sell 11, Neutral 6, Buy 9) → (−11+9)/26 = −0.077 → **Neutral** ✓ — all three match the panel exactly, confirming this is the correct aggregation formula to implement.
