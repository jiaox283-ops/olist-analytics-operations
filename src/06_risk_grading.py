"""Create descriptive risk tiers from frozen module-four test scores.

The tiers are ranks within the historical test window, not calibrated
probabilities or deployable score thresholds. Logistic is a challenger seen
after the test period; XGBoost is the validation-selected frozen model.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / 'module6'
BOUNDARIES = [(0.0, 0.01, 'P0'), (0.01, 0.05, 'P1'),
              (0.05, 0.20, 'P2'), (0.20, 1.0, 'P3')]
MODELS = ['XGBoost_depth2', 'Logistic_C0.1', 'Rule']


def main() -> None:
    df = pd.read_csv(OUT / '02_test_scores.csv', dtype={'order_id': str})
    if df.order_id.duplicated().any() or not df.is_delayed.isin([0, 1]).all():
        raise ValueError('Test score rows must have unique IDs and binary labels')
    n = len(df)
    base = float(df.is_delayed.mean())
    rows = []
    for model in MODELS:
        scores = df[f'score__{model}'].to_numpy()
        ranked = np.argsort(-scores, kind='stable')
        for lower, upper, tier in BOUNDARIES:
            start = 0 if lower == 0 else int(np.ceil(n * lower))
            stop = n if upper == 1 else int(np.ceil(n * upper))
            chosen = ranked[start:stop]
            hits = int(df.is_delayed.to_numpy()[chosen].sum())
            rows.append(dict(model=model, tier=tier, rank_start=start + 1,
                             rank_end=stop, orders=len(chosen), delays=hits,
                             delay_rate=hits / len(chosen), lift=hits / len(chosen) / base,
                             score_min=float(scores[chosen].min()),
                             score_max=float(scores[chosen].max()),
                             test_base_rate=base))
    pd.DataFrame(rows).to_csv(OUT / '16_risk_tiers.csv', index=False, encoding='utf-8-sig')


if __name__ == '__main__':
    main()
