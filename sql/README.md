# SQL 脚本

| 文件 | 类型 | 作用 |
|---|---|---|
| `01_data_quality.sql` | 只读 | 主键/联合键唯一性、缺失率、外键完整性、金额负值与零值、时间顺序异常、订单状态分布 |
| `02_cleaning_views.sql` | 建视图 | 建 `clean_orders` / `clean_products` / `clean_order_reviews` / `clean_review_latest_time` 四个视图，不改原表 |
| `03_analysis_tables.sql` | 建表 | 建 `dim_zip_geo`、`fact_order_item`、`mart_order`、`mart_customer` 四张分析表 | 
| `04_reconciliation.sql` | 只读 | 金额与订单数对账、键唯一性复核、NULL 与 0 的区分核对 |
| `05_business_analysis.sql` | 只读 | 12 条经营分析查询（Q01–Q12），在数据库客户端依次执行 |

`05_business_analysis.sql` 是经营指标的唯一计算定义，结果导出为 `results/module2/08_business_analysis.xlsx`。`03_analysis_tables.sql` 中的 `mart_customer` 主要用于客户数与金额对账；模块三的 RFM 是另一个有截止日与筛选条件的分析结果，不能混用。完整交接见 [`../docs/README.md`](../docs/README.md)。



## 口径约定

- 金额单位巴西雷亚尔（BRL），不换算、不扣成本，不代表利润。
- 比例字段取 0–1 小数，不是百分数。
- 金额缺失不补 0：明细与支付先分别汇总再关联，NULL 保持 NULL，对账时把 NULL 与 0 分开比较。
- 邮编统一为 5 位字符串（不足补前导 0），避免数值化后丢失前导零。
- 地理代表坐标取有效坐标的算术均值（不是中位数），有效范围用巴西主体区域宽松矩形（纬度 −34～6、经度 −74～−34）做粗筛，不代表经过国界校验。
- 时间字段为 `DATETIME`，不做时区转换。
