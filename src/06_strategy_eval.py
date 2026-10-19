"""模块六 第 2 步：三种预警方案的离线策略比较与成本敏感性分析。

核心逻辑（targeting / uplift-free 的保守 ROI 模型）
------------------------------------------------------
对一笔被选中的订单：
    成本      C                                   （人工/自动化干预单价，无论是否延迟都要付）
    收益      P(延迟) × e × L                     （只有真延迟且干预成功才回本）
    e = p × δ  （p 干预成功率，δ 成功时损失削减比例）
    ⇒ 单笔净收益 = P(延迟) × e × L − C
    ⇒ 盈亏平衡所需的最低延迟概率  P* = C / (e × L)
    ⇒ 盈亏平衡所需的最低精准率    prec* = C / (e × L)   （离线用实际精准率代入）

这是条件情景测算。e、L、C 均未经线上验证，历史标签不是干预后的反事实；
因此净值既不是已实现收益，也不能称为保守下限。

所有金额均为**假设单价下的离线清算值**，不是真实已实现收益。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parents[1]
RES = BASE / 'results' / 'module6'
FIG = BASE / 'figures'
STRATEGIES = ['Rule', 'Logistic_C0.1', 'XGBoost_depth2']
LABEL = {'Rule': 'Rule (promise-days lookup)',
         'Logistic_C0.1': 'Logistic (C=0.1)',
         'XGBoost_depth2': 'XGBoost (depth 2, frozen champion)',
         'Random': 'Random'}
COLOR = {'Rule': '#8c8c8c', 'Logistic_C0.1': '#1f6fb4', 'XGBoost_depth2': '#d1495b', 'Random': '#c9c9c9'}

# 两档干预模式 × 三档乐观程度。全部为显式假设，不来自数据。
SCENARIOS = {
    # 模式 A：人工加急（外呼 + 改派 + 跟踪），目标是"真的不延迟"
    'A1_manual_pess': dict(mode='manual', C=20.0, L=40.0, p=0.20, delta=0.50),
    'A2_manual_base': dict(mode='manual', C=12.0, L=60.0, p=0.30, delta=0.60),
    'A3_manual_opt': dict(mode='manual', C=8.0, L=120.0, p=0.40, delta=0.75),
    # 模式 B：假设存在低成本、能减少延迟相关损失的自动动作。
    # 若实际动作只是通知且目标为减少差评，不能用延迟标签估计收益。
    'B1_notify_pess': dict(mode='notify', C=1.0, L=8.0, p=0.80, delta=0.20),
    'B2_notify_base': dict(mode='notify', C=0.6, L=12.0, p=0.90, delta=0.30),
    'B3_notify_opt': dict(mode='notify', C=0.3, L=20.0, p=0.95, delta=0.45),
}
MAIN_SCENARIO = 'A2_manual_base'

FRACTIONS = [0.005, 0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30]
DAILY_CAPS = [5, 10, 20, 50]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间，订单层面近似。"""
    if n == 0:
        return (float('nan'), float('nan'))
    ph = k / n
    den = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / den
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (centre - half, centre + half)


def select_overall(score: np.ndarray, frac: float) -> np.ndarray:
    k = int(np.ceil(len(score) * frac))
    return np.argsort(-score, kind='stable')[:k]


def select_daily(df: pd.DataFrame, score_col: str, kday: int) -> np.ndarray:
    """日终批处理：当天订单全部按分数降序，取前 kday 笔。"""
    idx = []
    for _, g in df.groupby('date', sort=True):
        order = np.lexsort((g.order_id.to_numpy(), -g[score_col].to_numpy()))
        idx.extend(g.index.to_numpy()[order[:kday]])
    return np.asarray(idx)


def evaluate(y: np.ndarray, sel: np.ndarray, scenario: dict) -> dict:
    e = scenario['p'] * scenario['delta']
    k = len(sel)
    hits = int(y[sel].sum())
    prec = hits / k
    total = int(y.sum())
    net_per_order = prec * e * scenario['L'] - scenario['C']
    gross = hits * e * scenario['L']
    cost = k * scenario['C']
    return dict(selected=k, hits=hits, precision=prec, recall=hits / total,
                lift=prec / float(y.mean()),
                prec_ci_low=wilson(hits, k)[0], prec_ci_high=wilson(hits, k)[1],
                breakeven_precision=scenario['C'] / (e * scenario['L']),
                e=e,
                gross_value=gross, cost=cost, net_value=gross - cost,
                net_per_selected=net_per_order,
                roas=(gross / cost) if cost else float('nan'))


def main() -> None:
    df = pd.read_csv(RES / '02_test_scores.csv', dtype={'order_id': str})
    df['date'] = pd.to_datetime(df.order_purchase_timestamp).dt.date
    y = df.is_delayed.astype(int).to_numpy()
    n, base_rate = len(df), float(y.mean())
    ndays = df.date.nunique()
    print(f'测试集 {n} 单 / {ndays} 天 / 延迟率 {base_rate:.4%} / 日均 {n/ndays:.1f} 单')

    score_cols = {s: f'score__{s}' for s in STRATEGIES}

    # ---------- 1. 策略 × 容量：识别效果（不涉及金额） ----------
    rows = []
    for s in STRATEGIES:
        sc = df[score_cols[s]].to_numpy()
        for frac in FRACTIONS:
            sel = select_overall(sc, frac)
            r = evaluate(y, sel, SCENARIOS[MAIN_SCENARIO])
            rows.append(dict(strategy=s, selection='overall', capacity=frac, **r))
        for cap in DAILY_CAPS:
            sel = select_daily(df, score_cols[s], cap)
            r = evaluate(y, sel, SCENARIOS[MAIN_SCENARIO])
            rows.append(dict(strategy=s, selection='daily', capacity=cap, **r))
    # 随机基准：期望值解析计算，避免单次抽样的偶然性
    for frac in FRACTIONS:
        k = int(np.ceil(n * frac))
        rows.append(dict(strategy='Random', selection='overall', capacity=frac,
                         selected=k, hits=k * base_rate, precision=base_rate, recall=frac,
                         lift=1.0, prec_ci_low=float('nan'), prec_ci_high=float('nan'),
                         breakeven_precision=SCENARIOS[MAIN_SCENARIO]['C'] /
                         (SCENARIOS[MAIN_SCENARIO]['p'] * SCENARIOS[MAIN_SCENARIO]['delta'] * SCENARIOS[MAIN_SCENARIO]['L']),
                         e=SCENARIOS[MAIN_SCENARIO]['p'] * SCENARIOS[MAIN_SCENARIO]['delta'],
                         gross_value=k * base_rate * SCENARIOS[MAIN_SCENARIO]['p'] *
                         SCENARIOS[MAIN_SCENARIO]['delta'] * SCENARIOS[MAIN_SCENARIO]['L'],
                         cost=k * SCENARIOS[MAIN_SCENARIO]['C'],
                         net_value=k * (base_rate * SCENARIOS[MAIN_SCENARIO]['p'] * SCENARIOS[MAIN_SCENARIO]['delta'] *
                                        SCENARIOS[MAIN_SCENARIO]['L'] - SCENARIOS[MAIN_SCENARIO]['C']),
                         net_per_selected=base_rate * SCENARIOS[MAIN_SCENARIO]['p'] *
                         SCENARIOS[MAIN_SCENARIO]['delta'] * SCENARIOS[MAIN_SCENARIO]['L'] -
                         SCENARIOS[MAIN_SCENARIO]['C'],
                         roas=float((base_rate * SCENARIOS[MAIN_SCENARIO]['p'] * SCENARIOS[MAIN_SCENARIO]['delta'] *
                                     SCENARIOS[MAIN_SCENARIO]['L']) / SCENARIOS[MAIN_SCENARIO]['C'])))
    comp = pd.DataFrame(rows)
    comp.to_csv(RES / '03_strategy_comparison.csv', index=False, encoding='utf-8-sig')

    # ---------- 2. 六种假设下的净收益（策略 × 容量 × 场景） ----------
    sens_rows = []
    for sc_name, sc in SCENARIOS.items():
        e = sc['p'] * sc['delta']
        for strat in STRATEGIES + ['Random']:
            sub = comp[(comp.strategy == strat)]
            for _, q in sub.iterrows():
                prec = q.precision
                sens_rows.append(dict(
                    scenario=sc_name, mode=sc['mode'], C=sc['C'], L=sc['L'],
                    p_success=sc['p'], delta=sc['delta'], effectiveness_e=e,
                    strategy=strat, selection=q.selection, capacity=q.capacity,
                    selected=int(q.selected), hits=float(q.hits), precision=prec,
                    breakeven_precision=sc['C'] / (e * sc['L']),
                    net_per_selected=prec * e * sc['L'] - sc['C'],
                    net_value=(prec * e * sc['L'] - sc['C']) * q.selected,
                    roas=(prec * e * sc['L']) / sc['C']))
    sens = pd.DataFrame(sens_rows)
    sens.to_csv(RES / '06_sensitivity_grid.csv', index=False, encoding='utf-8-sig')

    # ---------- 3. 盈亏平衡：每种方案在每个容量下"需要多高的 L/C 才回本" ----------
    be_rows = []
    for strat in STRATEGIES + ['Random']:
        for _, q in comp[comp.strategy == strat].iterrows():
            for e in [0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.60]:
                # prec * e * (L/C) = 1  ⇒  L/C = 1/(prec*e)
                be_rows.append(dict(strategy=strat, selection=q.selection, capacity=q.capacity,
                                    precision=q.precision, effectiveness_e=e,
                                    required_LC_ratio=(1 / (q.precision * e)) if q.precision > 0 else float('inf')))
    be = pd.DataFrame(be_rows)
    be.to_csv(RES / '04_break_even.csv', index=False, encoding='utf-8-sig')

    # ---------- 4. 容量曲线（主场景下的净收益随容量变化） ----------
    curve_rows = []
    for strat in STRATEGIES + ['Random']:
        sc = df[f'score__{strat}'].to_numpy() if strat in STRATEGIES else None
        for frac in np.round(np.arange(0.002, 0.305, 0.002), 4):
            k = int(np.ceil(n * frac))
            if strat == 'Random':
                prec = base_rate
            else:
                prec = float(y[np.argsort(-sc, kind='stable')[:k]].mean())
            sc0 = SCENARIOS[MAIN_SCENARIO]
            e = sc0['p'] * sc0['delta']
            curve_rows.append(dict(strategy=strat, capacity=float(frac), selected=k, precision=prec,
                                   net_per_selected=prec * e * sc0['L'] - sc0['C'],
                                   net_value=(prec * e * sc0['L'] - sc0['C']) * k))
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(RES / '05_capacity_curve.csv', index=False, encoding='utf-8-sig')

    # ---------- 5. 图 ----------
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for strat in STRATEGIES + ['Random']:
        sub = comp[(comp.strategy == strat) & (comp.selection == 'overall')]
        ax[0].plot(sub.capacity * 100, sub.precision * 100, marker='o', ms=4,
                   color=COLOR[strat], label=LABEL[strat])
        ax[1].plot(sub.capacity * 100, sub.lift, marker='o', ms=4,
                   color=COLOR[strat], label=LABEL[strat])
    ax[0].axhline(base_rate * 100, ls='--', c='gray', lw=1)
    ax[0].set(xlabel='Capacity (% of test orders)', ylabel='Precision among selected (%)',
              title='Hit rate of the selected orders')
    ax[1].axhline(1.0, ls='--', c='gray', lw=1)
    ax[1].set(xlabel='Capacity (% of test orders)', ylabel='Lift vs base rate',
              title='Lift over random targeting')
    for a in ax:
        a.legend(fontsize=7, frameon=False)
        a.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(FIG / 'm6_01_strategy_compare.png', dpi=160)
    plt.close(fig)

    # 净收益全部为负时，绝对金额图看不出差异；改用 ROAS 对照 1.0 盈亏线
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    for sc_name, sc in SCENARIOS.items():
        z = sens[(sens.scenario == sc_name) & (sens.strategy == 'Logistic_C0.1') &
                 (sens.selection == 'overall')].sort_values('capacity')
        ax[0].plot(z.capacity * 100, z.roas, marker='o', ms=3,
                   ls='-' if sc['mode'] == 'manual' else '--',
                   color='#d1495b' if sc['mode'] == 'manual' else '#1f6fb4',
                   alpha=.85, label=sc_name)
    ax[0].axhline(1.0, color='k', ls=':', lw=1.2)
    ax[0].text(0.6, 1.03, 'break-even', fontsize=7)
    ax[0].set(xlabel='Capacity (% of test orders)',
              ylabel='ROAS  (avoided loss / intervention cost)',
              title='Logistic: does any capacity pay back?')
    ax[0].legend(fontsize=6.5, frameon=False, ncol=1)
    ax[0].grid(alpha=.25)

    z0 = sens[(sens.scenario == MAIN_SCENARIO) & (sens.selection == 'overall')]
    for strat in STRATEGIES + ['Random']:
        z = z0[z0.strategy == strat].sort_values('capacity')
        ax[1].plot(z.capacity * 100, z.roas, marker='o', ms=3, color=COLOR[strat], label=LABEL[strat])
    ax[1].axhline(1.0, color='k', ls=':', lw=1.2)
    ax[1].set(xlabel='Capacity (% of test orders)', ylabel='ROAS',
              title=f'Targeting gain is real but insufficient @ {MAIN_SCENARIO}')
    ax[1].legend(fontsize=7, frameon=False)
    ax[1].grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(FIG / 'm6_02_roas_curve.png', dpi=160)
    plt.close(fig)

    # 决策边界：在 (干预有效性 e, 损失成本比 L/C) 平面上，哪些组合才值得投
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.4))
    for ax, frac in zip(axes, [0.01, 0.05]):
        prec = float(comp[(comp.strategy == 'Logistic_C0.1') & (comp.selection == 'overall') &
                          (np.isclose(comp.capacity, frac))].precision.iloc[0])
        e_grid = np.linspace(0.02, 0.60, 120)
        r_grid = np.logspace(0, np.log10(100), 120)
        E, R = np.meshgrid(e_grid, r_grid)
        roas = prec * E * R
        cf = ax.contourf(E, R, np.log10(roas), levels=np.linspace(-1.2, 1.0, 23), cmap='RdYlGn')
        ax.contour(E, R, roas, levels=[1.0], colors='k', linewidths=1.6)
        for name, sc in SCENARIOS.items():
            e, r = sc['p'] * sc['delta'], sc['L'] / sc['C']
            ax.scatter([e], [r], s=34, c='k', marker='o' if sc['mode'] == 'manual' else 's', zorder=5)
            ax.annotate(name, (e, r), fontsize=5.8, xytext=(3.5, 3.5), textcoords='offset points')
        ax.set_yscale('log')
        ax.set(xlabel='Intervention effectiveness  e = p × δ',
               ylabel='Loss-to-cost ratio  L / C',
               title=f'Logistic @ Top {frac:.0%}  (precision {prec:.1%})')
    cb = fig.colorbar(cf, ax=axes, fraction=.025, pad=.02)
    cb.set_label('log10(ROAS)   ·   black line = break-even (ROAS = 1)')
    fig.savefig(FIG / 'm6_03_break_even.png', dpi=160, bbox_inches='tight')
    plt.close(fig)

    # ---------- 6. 测试窗情景摘要：最大值经过测试窗事后选择，不能作为上线承诺 ----------
    dec = []
    for strat in STRATEGIES + ['Random']:
        for sc_name, sc in SCENARIOS.items():
            sub = sens[(sens.scenario == sc_name) & (sens.strategy == strat) &
                       (sens.selection == 'overall')][['capacity', 'net_value', 'precision', 'roas']]
            # 把"不投"作为显式选项：容量 0、净收益 0，避免在全部为负时硬挑一个最不亏的容量
            sub = pd.concat([pd.DataFrame([dict(capacity=0.0, net_value=0.0, precision=float('nan'),
                                                roas=float('nan'))]), sub], ignore_index=True)
            best = sub.loc[sub.net_value.idxmax()]
            dec.append(dict(strategy=strat, scenario=sc_name, mode=sc['mode'],
                            best_test_window_capacity=best.capacity,
                            best_label_scenario_net=best.net_value,
                            best_precision=best.precision, roas=best.roas,
                            decision='仅假设下为正，待验证' if best.net_value > 0 else '按假设不投',
                            hypothetical_positive=bool(best.net_value > 0)))
    pd.DataFrame(dec).to_csv(RES / '07_decision_summary.csv', index=False, encoding='utf-8-sig')

    (RES / '08_assumptions.json').write_text(json.dumps(
        dict(test_orders=n, test_days=ndays, base_rate=base_rate,
             average_orders_per_day=n / ndays, fractions=FRACTIONS, daily_caps=DAILY_CAPS,
             main_scenario=MAIN_SCENARIO, scenarios=SCENARIOS,
             note='金额单位 BRL。C、L、p、delta 均是假设；历史标签条件下的情景值，非真实已实现收益。自动通知若仅影响差评，不可用延迟标签推算收益。'),
        ensure_ascii=False, indent=2), encoding='utf-8')

    print(comp[comp.selection == 'overall'].round(4).to_string(index=False))
    print('\n--- 各假设场景在测试窗的最大标签条件情景净值（含不干预）---')
    print(pd.DataFrame(dec).round(2).to_string(index=False))


if __name__ == '__main__':
    main()
