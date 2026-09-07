# Naive baselines on the eval split of the champion dataset (Phase 1 ground truth).
import numpy as np, polars as pl

DS = 'artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'
df = pl.scan_parquet(DS).select(['label', 'is_eval_sample', 'is_purged', 'timestamp']).collect()
y = df['label'].to_numpy()
ev = (df['is_eval_sample'].to_numpy() == 1) & (df['is_purged'].to_numpy() == 0)
print('eval rows:', int(ev.sum()))
ye = y[ev]


def sim(pred_dir, mask, name):
    r = np.where(ye[mask] == pred_dir, 1.0, -1.0) - 0.15
    n = int(mask.sum())
    wins = int((r > 0).sum())
    pf = r[r > 0].sum() / max(1e-9, -r[r < 0].sum())
    print(f'{name}: n={n} win={wins / n:.3f} exp={r.mean():.3f}R pf={pf:.2f} sumR={r.sum():.1f}')


all_mask = np.ones_like(ye, dtype=bool)
sim(1, all_mask, 'Always-BUY ')
sim(2, all_mask, 'Always-SELL')
print('Abstain: n=0, exp=0.0R (flat by definition)')
print('eval label dist:', np.bincount(ye, minlength=3))
