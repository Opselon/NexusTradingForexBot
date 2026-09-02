import polars as pl

df = pl.read_parquet('data/raw/XAUUSD_M1.parquet').head(3000)
t0 = df.row(0, named=True)['time_utc'].replace(tzinfo=None)
print('t0:', t0)
from datetime import timedelta
win = df.filter((pl.col('time_utc') >= t0) & (pl.col('time_utc') <= t0 + timedelta(minutes=1439)))
print('rows in 24h window:', win.shape[0])
print('max ts in window:', win['time_utc'].max())
ts = win['time_utc'].to_list()
gaps = 0
for i in range(1, len(ts)):
    if (ts[i] - ts[i - 1]).total_seconds() > 60:
        gaps += 1
        if gaps <= 5:
            print('gap at', ts[i - 1], '->', ts[i], (ts[i] - ts[i - 1]))
print('total gaps:', gaps)
