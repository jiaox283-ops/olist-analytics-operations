"""Shared downstream validity filters for the SQL-built ``mart_order``.

SQL owns the label and quality flags. These helpers only apply the same
eligibility rule when modules 3 and 5 read the exported CSV.
"""

import numpy as np
import pandas as pd


def valid_delay(orders: pd.DataFrame) -> pd.Series:
    mask = (orders.order_status.eq('delivered') & orders.usable_delay_label.eq(1)
            & orders.is_delayed.isin([0, 1]))
    return orders.is_delayed.where(mask)


def valid_review(orders: pd.DataFrame) -> pd.Series:
    mask = (orders.order_status.eq('delivered') & orders.flag_no_valid_review.eq(0)
            & orders.review_score.between(1, 5))
    return orders.review_score.where(mask)


def valid_duration(orders: pd.DataFrame) -> pd.Series:
    mask = (orders.order_status.eq('delivered') & orders.usable_delivery_duration.eq(1)
            & orders.delivery_days.ge(0))
    return orders.delivery_days.where(mask)


def low_review(valid_scores: pd.Series) -> pd.Series:
    return pd.Series(np.where(valid_scores.notna(), valid_scores.le(2).astype(float), np.nan),
                     index=valid_scores.index)
