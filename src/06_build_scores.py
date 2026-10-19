"""模块六 第 1 步：复现模块四三个方案的测试集逐单分数。

目的：模块四只落盘了冻结 XGBoost 的逐单分数（06_test_risk_orders.csv）。
模块六的成本-收益分析需要「同一批订单、同一划分」上三个方案的可比分数。
本脚本直接调用模块四的特征函数，并按相同时间切分：

  - 规则（Rule）：用模块四已落盘的 02_rule_lookup.csv 查表，纯 pandas，可完全复现
  - Logistic：加载模块四保存的 Logistic_C0.1.joblib，重新打分
  - XGBoost：直接读取 06_test_risk_orders.csv（含真实标签，模块四已验收）

并做三项一致性校验，校验不过直接报错，不继续往下算。
"""
from __future__ import annotations

import json
import importlib.util
import hashlib
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)

ROOT = Path(__file__).resolve().parents[1]
MART = ROOT / 'data' / 'mart_order.csv'
MODULE4 = ROOT / 'results' / 'module4'
MODELS = ROOT / 'models'
OUT = ROOT / 'results' / 'module6'

def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Use the single module-four feature definition to avoid drift."""
    path = ROOT / 'src' / '04_train_delay_model.py'
    spec = importlib.util.spec_from_file_location('delay_model_module4', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.features(frame)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reference_hash = json.loads((MODULE4 / '05_run_config.json').read_text(encoding='utf-8'))['input_sha256']
    input_hash = hashlib.sha256(MART.read_bytes()).hexdigest()
    assert input_hash == reference_hash, '宽表哈希与模块四训练输入不同，终止'
    d = pd.read_csv(MART, dtype={'order_id': str, 'customer_unique_id': str})
    assert d.order_id.is_unique, 'order_id 不唯一'

    t = pd.to_datetime(d.order_purchase_timestamp)
    delivery = pd.to_datetime(d.order_delivered_customer_date)
    base = (d.order_status.eq('delivered') & d.usable_delay_label.eq(1)
            & d.is_delayed.isin([0, 1]) & t.ge('2017-01-01') & t.lt('2018-09-01'))
    masks = {
        'train': base & t.lt('2018-02-01') & delivery.lt('2018-04-01'),
        'validation': base & t.ge('2018-02-01') & t.lt('2018-04-01') & delivery.lt('2018-06-01'),
        'test': base & t.ge('2018-06-01'),
    }

    X = {k: build_features(d.loc[m]) for k, m in masks.items()}
    y = {k: d.loc[m, 'is_delayed'].astype(int).to_numpy() for k, m in masks.items()}
    print('切分样本量:', {k: len(v) for k, v in y.items()})

    # ---------- 规则：训练集承诺天数 → 平滑延迟率 ----------
    lookup_raw = pd.read_csv(MODULE4 / '02_rule_lookup.csv')
    lookup = lookup_raw.set_index('days')['smoothed_rate']
    prior = float(y['train'].mean())
    # 自检：用训练集重算，应与落盘查找表一致（允许浮点容差）
    tr = pd.DataFrame({'days': X['train'].promise_days.to_numpy(), 'y': y['train']})
    agg = tr.groupby('days').y.agg(['sum', 'count'])
    recomputed = (agg['sum'] + 100 * prior) / (agg['count'] + 100)
    common = lookup.index.intersection(recomputed.index)
    max_gap = float(np.max(np.abs(lookup.loc[common] - recomputed.loc[common]))) if len(common) else float('nan')
    print(f'规则查找表复现最大偏差 = {max_gap:.3e}（{len(common)} 个天数档位）')
    assert max_gap < 1e-9, '规则查找表无法复现，终止'

    score = {}
    score['Rule'] = X['test'].promise_days.map(lookup).fillna(prior).to_numpy()

    # ---------- Logistic：加载模块四冻结模型 ----------
    logit = joblib.load(MODELS / 'Logistic_C0.1.joblib')
    score['Logistic_C0.1'] = logit.predict_proba(X['test'])[:, 1]

    # ---------- XGBoost：读取模块四已落盘分数 ----------
    xgb_tbl = pd.read_csv(MODULE4 / '06_test_risk_orders.csv',
                          dtype={'order_id': str})
    test_ids = d.loc[masks['test'], 'order_id'].to_numpy()
    assert len(xgb_tbl) == len(test_ids), 'XGBoost 分数行数与测试集不符'
    xgb_map = xgb_tbl.set_index('order_id')['risk_score']
    assert set(xgb_map.index) == set(test_ids), 'XGBoost 分数与测试集 order_id 不完全一致'
    score['XGBoost_depth2'] = xgb_map.loc[test_ids].to_numpy()

    # ---------- 一致性校验：与模块四 04_metrics.csv 对齐 ----------
    ref = pd.read_csv(MODULE4 / '04_metrics.csv').set_index(['model', 'split'])
    checks = []
    for name in score:
        p = np.asarray(score[name], dtype=float)
        got = dict(roc_auc=float(roc_auc_score(y['test'], p)),
                   ap=float(average_precision_score(y['test'], p)),
                   brier=float(brier_score_loss(y['test'], p)))
        want = dict(roc_auc=float(ref.loc[(name, 'test'), 'roc_auc']),
                    ap=float(ref.loc[(name, 'test'), 'pr_auc_ap']),
                    brier=float(ref.loc[(name, 'test'), 'brier']))
        gap = {k: abs(got[k] - want[k]) for k in got}
        checks.append(dict(model=name, reproduced=got, module4=want,
                           abs_gap=gap, pass_=max(gap.values()) < 1e-6))
        print(f'{name}: AUC {got["roc_auc"]:.4f} / AP {got["ap"]:.4f} / Brier {got["brier"]:.5f}'
              f' | 与模块四最大偏差 {max(gap.values()):.2e}')

    assert all(c['pass_'] for c in checks), '分数复现未通过一致性校验'

    # ---------- 落盘 ----------
    out = d.loc[masks['test'], ['order_id', 'customer_unique_id', 'order_purchase_timestamp',
                                'customer_state', 'is_delayed', 'item_amount',
                                'freight_amount', 'order_estimated_delivery_date',
                                'review_score', 'flag_no_valid_review', 'delivery_days']].reset_index(drop=True)
    out['promise_days'] = X['test'].reset_index(drop=True)['promise_days']
    for name, p in score.items():
        out[f'score__{name}'] = p
    out.to_csv(OUT / '02_test_scores.csv', index=False, encoding='utf-8-sig')

    (OUT / '01_score_reproduction_check.json').write_text(
        json.dumps(dict(input_sha256=input_hash, n_test=int(len(y['test'])), delay_rate=float(y['test'].mean()),
                        rule_lookup_max_gap=max_gap, checks=checks),
                   ensure_ascii=False, indent=2), encoding='utf-8')
    print('已写出 results/module6/02_test_scores.csv 与 01_score_reproduction_check.json')


if __name__ == '__main__':
    main()
