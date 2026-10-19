"""模块六 第 4 步：A/B 测试设计与功效可行性核算。

关键判断
--------
线上一共有两个想问的问题，但它们的可检验性差了一个数量级：

  问题 1「被预警的订单，干预真的能降低延迟（或降低差评）吗？」
        → 可在 Top-K 清单内部做 50/50 随机，但 Top 5% 的流量很小，
          对中等效应的实验往往需要数月到一年以上。

  问题 2「模型排序比随机排序带来多少额外收益？」
        → 需要在同容量下比较两条策略，策略差异被 (容量 × 精度差) 稀释，
          按当前精度差算出的样本量达数十万单/组，不具现实可行性。

样本量与工期均是固定历史流量下的近似规划值，不能解释为已观测干预效果。

样本量公式（两独立比例，双侧）：
    n_per_arm = (z_{1-α/2} + z_{1-β})² × [p1(1-p1) + p2(1-p2)] / (p1-p2)²
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import NormalDist

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parents[1]
RES = BASE / 'results' / 'module6'
FIG = BASE / 'figures'

ALPHA, POWER = 0.05, 0.80
Z_A = NormalDist().inv_cdf(1 - ALPHA / 2)
Z_B = NormalDist().inv_cdf(POWER)


def n_per_arm(p1: float, p2: float) -> float:
    return (Z_A + Z_B) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p1 - p2) ** 2


def mde(p: float, n: float) -> float:
    """给定每组样本量，可检出的最小绝对差（迭代求解）。"""
    lo, hi = 1e-6, min(p, 1 - p) * 0.999
    for _ in range(200):
        mid = (lo + hi) / 2
        need = n_per_arm(p, p - mid)
        if need > n:
            lo = mid
        else:
            hi = mid
    return hi


def main() -> None:
    df = pd.read_csv(RES / '02_test_scores.csv', dtype={'order_id': str})
    y = df.is_delayed.astype(int).to_numpy()
    n = len(df)
    ndays = pd.to_datetime(df.order_purchase_timestamp).dt.date.nunique()
    per_day = n / ndays

    valid_review = df.flag_no_valid_review.fillna(1).eq(0) & df.review_score.notna()
    low_review = (df.review_score <= 2) & valid_review

    # Top-K 清单基线
    pools = {}
    for frac in [0.01, 0.05, 0.10]:
        k = int(np.ceil(n * frac))
        sel = np.argsort(-df['score__Logistic_C0.1'].to_numpy(), kind='stable')[:k]
        pools[frac] = dict(
            k=k,
            delay_rate=float(y[sel].mean()),
            low_review_rate=float(low_review.to_numpy()[sel].sum() / valid_review.to_numpy()[sel].sum()),
            review_coverage=float(valid_review.to_numpy()[sel].mean()),
            share_of_orders=frac,
        )
    print('Top-K 清单基线:', json.dumps({str(k): v for k, v in pools.items()}, ensure_ascii=False, indent=1))

    # ---------- 实验 1：桶内干预有效性（Top 5%） ----------
    experiment_frac = 0.05
    p0_delay = pools[experiment_frac]['delay_rate']
    p0_review = pools[experiment_frac]['low_review_rate']
    # 只在 Top 5% 清单内随机；每组每天只能收到全体流量的 2.5%。
    bucket_per_day_per_arm = pools[experiment_frac]['k'] / ndays / 2
    eff_rows = []
    for e in [0.05, 0.10, 0.15, 0.20, 0.27, 0.35, 0.45]:
        p1 = p0_delay * (1 - e)
        n_req = n_per_arm(p0_delay, p1)
        eff_rows.append(dict(
            experiment='E1_delay_in_bucket', metric='delay_rate', baseline=p0_delay,
            effectiveness_e=e, treated_rate=p1, abs_effect=p0_delay - p1,
            rel_effect=e, n_per_arm=float(np.ceil(n_req)),
            orders_per_day_per_arm=bucket_per_day_per_arm,
            days_needed=float(np.ceil(n_req / bucket_per_day_per_arm))))
        p1r = p0_review * (1 - e)
        n_reqr = n_per_arm(p0_review, p1r)
        eff_rows.append(dict(
            experiment='E1_review_in_bucket', metric='low_review_rate', baseline=p0_review,
            effectiveness_e=e, treated_rate=p1r, abs_effect=p0_review - p1r,
            rel_effect=e, n_per_arm=float(np.ceil(n_reqr)),
            orders_per_day_per_arm=bucket_per_day_per_arm * pools[experiment_frac]['review_coverage'],
            days_needed=float(np.ceil(n_reqr / (bucket_per_day_per_arm * pools[experiment_frac]['review_coverage'])))))
    eff = pd.DataFrame(eff_rows)

    # ---------- 实验 2：排序增益（模型 vs 随机，同容量） ----------
    rank_rows = []
    for frac in [0.01, 0.05, 0.10, 0.20]:
        pt = pools.get(frac, {}).get('delay_rate')
        if pt is None:
            k = int(np.ceil(n * frac))
            sel = np.argsort(-df['score__Logistic_C0.1'].to_numpy(), kind='stable')[:k]
            pt = float(y[sel].mean())
        pb = float(y.mean())
        for e in [0.20, 0.27, 0.35]:
            # 两条策略都在全量订单上随机分配，容量 frac，人群层面差异
            p_policy_model = pb - frac * e * pt
            p_policy_random = pb - frac * e * pb
            n_req = n_per_arm(p_policy_model, p_policy_random)
            rank_rows.append(dict(experiment='E2_targeting_gain', capacity=frac,
                                  bucket_precision=pt, base_rate=pb, effectiveness_e=e,
                                  population_effect=abs(p_policy_model - p_policy_random),
                                  n_per_arm=float(np.ceil(n_req)),
                                  months_needed=float(np.ceil(n_req / (per_day / 2) / 30)),
                                  feasible=bool(n_req <= 12 * (per_day / 2) * 30)))
    rank = pd.DataFrame(rank_rows)

    # ---------- 可行样本量下的 MDE ----------
    mde_rows = []
    for weeks in [2, 4, 6, 8, 12]:
        n_arm = bucket_per_day_per_arm * 7 * weeks
        mde_rows.append(dict(horizon_weeks=weeks, n_per_arm=float(np.ceil(n_arm)),
                             total_orders=float(np.ceil(n_arm * 2)),
                             mde_delay_abs=mde(p0_delay, n_arm),
                             mde_delay_rel=mde(p0_delay, n_arm) / p0_delay,
                             mde_review_abs=mde(p0_review, n_arm * pools[experiment_frac]['review_coverage']),
                             mde_review_rel=mde(p0_review, n_arm * pools[experiment_frac]['review_coverage']) / p0_review))
    mde_tbl = pd.DataFrame(mde_rows)

    # ---------- CUPED 方差缩减潜力：用模型分作协变量 ----------
    s = df['score__Logistic_C0.1'].to_numpy()
    cuped = []
    for name, arr, mask in [('delay_rate', y.astype(float), np.ones(n, dtype=bool)),
                            ('low_review_rate', low_review.to_numpy().astype(float), valid_review.to_numpy())]:
        r = float(np.corrcoef(s[mask], arr[mask])[0, 1])
        cuped.append(dict(metric=name, corr_with_score=r, r2=r * r,
                          variance_reduction=r * r,
                          n_saving_factor=1 / (1 - r * r) if r * r < 1 else float('inf')))

    # ---------- 图 ----------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for exp, ls in [('E1_delay_in_bucket', '-'), ('E1_review_in_bucket', '--')]:
        z = eff[eff.experiment == exp]
        ax[0].plot(z.effectiveness_e, z.n_per_arm, ls, marker='o', ms=4, label=exp)
    ax[0].axhline(bucket_per_day_per_arm * 30, color='firebrick', ls=':', lw=1.2, label='1 month capacity/arm')
    ax[0].set_yscale('log')
    ax[0].set(xlabel='Intervention effectiveness e = p × δ', ylabel='Required n per arm (log)',
              title='E1: limited Top-5% traffic')
    ax[0].legend(fontsize=7, frameon=False)
    ax[0].grid(alpha=.25, which='both')

    z = rank[rank.effectiveness_e == 0.27]
    ax[1].plot(z.capacity * 100, z.n_per_arm, marker='o', color='#d1495b')
    ax[1].axhline(per_day / 2 * 30 * 12, color='firebrick', ls=':', lw=1.2, label='12 months of traffic/arm')
    ax[1].set_yscale('log')
    ax[1].set(xlabel='Policy capacity (% of orders)', ylabel='Required n per arm (log)',
              title='E2: policy-level targeting RCT is not feasible')
    ax[1].legend(fontsize=7, frameon=False)
    ax[1].grid(alpha=.25, which='both')
    fig.tight_layout()
    fig.savefig(FIG / 'm6_05_ab_power.png', dpi=160)
    plt.close(fig)

    pd.concat([eff, rank], ignore_index=True).to_csv(RES / '12_ab_power.csv', index=False, encoding='utf-8-sig')
    mde_tbl.to_csv(RES / '13_ab_mde.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(cuped).to_csv(RES / '14_ab_cuped.csv', index=False, encoding='utf-8-sig')
    (RES / '15_ab_design_params.json').write_text(json.dumps(dict(
        alpha=ALPHA, power=POWER, z_alpha=Z_A, z_beta=Z_B,
        test_orders=n, test_days=ndays, orders_per_day=per_day,
        top5_orders_per_day_per_arm=bucket_per_day_per_arm,
        pools={str(k): v for k, v in pools.items()}), ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n--- E1 桶内 RCT ---')
    print(eff[eff.metric == 'delay_rate'].round(4).to_string(index=False))
    print('\n--- E2 排序增益 RCT ---')
    print(rank.round(4).to_string(index=False))
    print('\n--- 可行周期下的 MDE ---')
    print(mde_tbl.round(4).to_string(index=False))
    print('\n--- CUPED ---')
    print(pd.DataFrame(cuped).round(4).to_string(index=False))


if __name__ == '__main__':
    main()
