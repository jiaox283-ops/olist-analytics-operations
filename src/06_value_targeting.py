"""模块六 第 3 步：按期望价值排序（Expected-Value Targeting）。

为什么需要这一步
----------------
第 2 步用统一单价 L 计算盈亏平衡精准率，等价于假设"每笔延迟的损失相同"。
本脚本仅假设延迟损失随订单金额变化；数据并未提供逐单真实损失。
于是运营资源的最优分配不是「按延迟概率排序」，而是「按 延迟概率 × 单笔损失 排序」：

    期望可避免损失_i = P(延迟_i) × e × L_i
    选中条件           P(延迟_i) × e × L_i > C

本步同时输出两套口径，用于区分「模拟收益」和「离线清算收益」：

    分数情景值 = Σ_{i∈S} score_i × e × L_i
    标签情景值 = Σ_{i∈S, 真实延迟} e × L_i

两者的差异反映分数校准问题，但都不是观测到的干预收益。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parents[1]
RES = BASE / 'results' / 'module6'
FIG = BASE / 'figures'

# 单笔延迟损失 = L_fixed + L_ratio × 订单含运费金额（BRL）
LOSS_MODEL = {
    'manual': dict(L_fixed=15.0, L_ratio=0.30, C=12.0, p=0.30, delta=0.60),
    'notify': dict(L_fixed=6.0, L_ratio=0.05, C=0.6, p=0.90, delta=0.30),
}
FRACTIONS = [0.005, 0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30]


def main() -> None:
    df = pd.read_csv(RES / '02_test_scores.csv', dtype={'order_id': str})
    y = df.is_delayed.astype(int).to_numpy()
    n = len(df)
    order_value = (df.item_amount.fillna(0) + df.freight_amount.fillna(0)).clip(lower=0).to_numpy()
    print(f'订单金额：均值 {order_value.mean():.2f} / 中位 {np.median(order_value):.2f} / '
          f'P90 {np.percentile(order_value, 90):.2f} BRL')

    rows = []
    for mode, prm in LOSS_MODEL.items():
        e = prm['p'] * prm['delta']
        L_i = prm['L_fixed'] + prm['L_ratio'] * order_value
        for model in ['Logistic_C0.1', 'XGBoost_depth2', 'Rule']:
            s = df[f'score__{model}'].to_numpy()
            for rule, key in [('probability', s), ('expected_value', s * L_i)]:
                order = np.argsort(-key, kind='stable')
                for frac in FRACTIONS:
                    k = int(np.ceil(n * frac))
                    sel = order[:k]
                    hit = y[sel] == 1
                    realized = float(e * L_i[sel][hit].sum())
                    expected = float(e * (s[sel] * L_i[sel]).sum())
                    cost = k * prm['C']
                    rows.append(dict(
                        mode=mode, model=model, ranking=rule, capacity=frac, selected=k,
                        hits=int(hit.sum()),
                        precision=float(hit.mean()),
                        value_weighted_precision=float(L_i[sel][hit].sum() / L_i[sel].sum()),
                        mean_loss_per_hit=float(L_i[sel][hit].mean()) if hit.any() else float('nan'),
                        label_scenario_value=realized,
                        score_scenario_value=expected,
                        score_to_label_ratio=expected / realized if realized else float('nan'),
                        cost=cost,
                        net_label_scenario=realized - cost,
                        net_score_scenario=expected - cost,
                        e=e))
    # 随机参照
    base_rate = float(y.mean())
    for mode, prm in LOSS_MODEL.items():
        e = prm['p'] * prm['delta']
        L_i = prm['L_fixed'] + prm['L_ratio'] * order_value
        for frac in FRACTIONS:
            k = int(np.ceil(n * frac))
            rows.append(dict(mode=mode, model='Random', ranking='random', capacity=frac, selected=k,
                             hits=k * base_rate, precision=base_rate,
                             value_weighted_precision=base_rate,
                             mean_loss_per_hit=float(L_i.mean()),
                             label_scenario_value=float(e * base_rate * L_i.sum() * k / n),
                             score_scenario_value=float(e * base_rate * L_i.sum() * k / n),
                             score_to_label_ratio=1.0, cost=k * prm['C'],
                             net_label_scenario=float(e * base_rate * L_i.sum() * k / n) - k * prm['C'],
                             net_score_scenario=float(e * base_rate * L_i.sum() * k / n) - k * prm['C'],
                             e=e))
    out = pd.DataFrame(rows)
    out.to_csv(RES / '09_value_targeting.csv', index=False, encoding='utf-8-sig')

    # ---- 结论摘要：主场景（manual）下四种排序规则在固定容量 5% 的对比 ----
    key = out[(out['mode'] == 'manual') & (out.capacity == 0.05)]
    print('\n--- 人工模式 · Top 5% · 排序规则对比 ---')
    print(key[['model', 'ranking', 'selected', 'hits', 'precision', 'value_weighted_precision',
               'label_scenario_value', 'cost', 'net_label_scenario', 'score_to_label_ratio']]
          .round(2).to_string(index=False))

    # ---- 测试窗情景最大值；将不干预明确加入候选集合 ----
    no_action = pd.DataFrame([
        dict(mode=mode, model='Logistic_C0.1', ranking=ranking, capacity=0.0,
             selected=0, hits=0, label_scenario_value=0.0,
             score_scenario_value=0.0, cost=0.0, net_label_scenario=0.0,
             net_score_scenario=0.0)
        for mode in LOSS_MODEL for ranking in ['probability', 'expected_value']
    ])
    candidates = pd.concat([out[out.model == 'Logistic_C0.1'], no_action], ignore_index=True)
    best = candidates.loc[candidates.groupby(['mode', 'ranking']).net_label_scenario.idxmax()]
    best.to_csv(RES / '10_value_targeting_best.csv', index=False, encoding='utf-8-sig')
    print('\n--- Logistic 各排序规则的测试窗情景最大值（含不干预）---')
    print(best[['mode', 'ranking', 'capacity', 'selected', 'precision',
                'label_scenario_value', 'cost', 'net_label_scenario']].round(2).to_string(index=False))

    # ---- 图：金额加权 vs 概率排序（用 ROAS，避免绝对金额尺度掩盖差异） ----
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, mode in zip(axes, ['manual', 'notify']):
        for model in ['Logistic_C0.1', 'XGBoost_depth2']:
            for rule, ls in [('probability', '--'), ('expected_value', '-')]:
                z = out[(out['mode'] == mode) & (out.model == model) & (out['ranking'] == rule)]
                ax.plot(z.capacity * 100, z.label_scenario_value / z.cost, ls, marker='o', ms=3,
                        label=f'{model.split("_")[0]} · {rule}')
        z = out[(out['mode'] == mode) & (out.model == 'Random')]
        ax.plot(z.capacity * 100, z.label_scenario_value / z.cost, ':', color='gray', label='Random')
        ax.axhline(1.0, color='k', ls=':', lw=1.2)
        ax.set(xlabel='Capacity (% of test orders)',
               ylabel='Scenario value / assumed cost',
               title=f'{mode} intervention')
        ax.legend(fontsize=6.5, frameon=False)
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(FIG / 'm6_04_value_targeting.png', dpi=160)
    plt.close(fig)

    (RES / '11_value_targeting_notes.json').write_text(json.dumps(
        dict(loss_model=LOSS_MODEL,
             order_value=dict(mean=float(order_value.mean()), median=float(np.median(order_value)),
                              p90=float(np.percentile(order_value, 90))),
             note='标签情景值使用历史延迟标签；分数情景值使用未校准模型分数。两者均非实际干预收益。'),
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('已写出 results/module6/09_value_targeting.csv、10_value_targeting_best.csv、figures/m6_04_value_targeting.png')


if __name__ == '__main__':
    main()
