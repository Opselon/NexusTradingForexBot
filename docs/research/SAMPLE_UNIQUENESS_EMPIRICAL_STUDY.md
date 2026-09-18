# ML-LABEL-002: Empirical Sample Uniqueness & Concurrency Study

## Executive Summary
Evaluation of Marcos Lopez de Prado's Sample Uniqueness Weighting algorithm on Gold M1 bars.
- **Dataset Size:** 10,000 bars
- **Evaluated Samples:** 2,856 samples
- **Mean Sample Uniqueness ($u_i$):** 0.8988
- **Kish Effective Sample Size ($N_{eff}$):** 2,765.2
- **Sample Redundancy Ratio:** 3.18%
- **Max Concurrency ($c_t$):** 4 concurrent active labels
- **Mean Concurrency ($c_t$):** 1.14

## Quantiles of Sample Uniqueness
| Quantile | Value |
|---|---|
| p05 | 0.5000 |
| p10 | 0.6458 |
| p25 | 0.8333 |
| Median (p50) | 1.0000 |
| p75 | 1.0000 |
| p90 | 1.0000 |
| p95 | 1.0000 |

## Empirical Distributions
```
--- Raw Sample Uniqueness (u_i) (N=2,856) ---
[0.31 - 0.38]:                                    16 (  0.6%)
[0.38 - 0.45]:                                    25 (  0.9%)
[0.45 - 0.52]: ###                               200 (  7.0%)
[0.52 - 0.59]:                                     7 (  0.2%)
[0.59 - 0.66]:                                    43 (  1.5%)
[0.66 - 0.73]: #                                  74 (  2.6%)
[0.73 - 0.79]: ###                               206 (  7.2%)
[0.79 - 0.86]: ###                               221 (  7.7%)
[0.86 - 0.93]: ###                               229 (  8.0%)
[0.93 - 1.00]: ##############################  1,835 ( 64.3%)
```

```
--- Concurrency (c_t) (N=7,707) ---
[1.00 - 1.30]: ##############################  6,707 ( 87.0%)
[1.30 - 1.60]:                                     0 (  0.0%)
[1.60 - 1.90]:                                     0 (  0.0%)
[1.90 - 2.20]: ####                              938 ( 12.2%)
[2.20 - 2.50]:                                     0 (  0.0%)
[2.50 - 2.80]:                                     0 (  0.0%)
[2.80 - 3.10]:                                    61 (  0.8%)
[3.10 - 3.40]:                                     0 (  0.0%)
[3.40 - 3.70]:                                     0 (  0.0%)
[3.70 - 4.00]:                                     1 (  0.0%)
```
