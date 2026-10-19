# 项目说明：口径、字段与复现

本文件是项目的唯一说明文档，合并了原来的流程、指标口径、字段字典、看板说明和文件用途。
成果入口见根目录 [`README.md`](../README.md)，分析结论见 `reports/`。

金额单位统一为 **巴西雷亚尔（BRL）**，不换算、不扣成本，不代表利润。比例字段原始值取 0–1 小数，报告和图表里转成百分数。

---

## 1. 六个模块与交付物

| 模块 | 负责计算的代码 | 唯一权威产物 |
|---|---|---|
| 1 数据质量与集成 | `sql/01_data_quality` → `02_cleaning_views` → `03_analysis_tables` → `04_reconciliation` | MySQL `mart_order`（订单级宽表，生成方式见 2.1） |
| 2 经营分析 | `sql/05_business_analysis.sql`（12 条查询，数据库客户端执行） | `results/module2/08_business_analysis.xlsx` |
| 3 客户价值与体验 | `src/03_customer_analysis.py` | `results/module3/`（8 个文件） |
| 4 延迟风险建模 | `src/04_train_delay_model.py` | `results/module4/`（12 个文件）+ `models/` |
| 5 商业看板 | Power BI（`dashboards/olist_dashboard.pbix`） | 三页看板与截图 + `reports/05_商业看板.md` |
| 6 运营决策 | `src/06_build_scores` → `06_strategy_eval` → `06_value_targeting` → `06_ab_test_design` → `06_risk_grading` → `06_build_excel` | `results/module6/` + `reports/06_运营决策与实验设计.md` |

模块一、二没有 Python 代码：数据集成和经营指标全部由 SQL 定义，查询在数据库客户端执行后把结果导出成 Excel。

`src/` 的命名规则：文件名前缀的两位数字是**模块编号**，不是执行顺序。所以 `src/` 从 `03_` 开始，
`06_` 有六个文件是因为模块六是一条必须按顺序执行的流水线。`analysis_contract.py` 没有编号，
它是模块三 import 的筛选规则库。

---

## 2. 数据流与执行顺序

### 2.1 订单宽表的生成

原始数据是 Kaggle 的 *Brazilian E-Commerce Public Dataset by Olist*，9 张 CSV 共约 130 MB。先手工导入 MySQL 8 的 9 张原始表，再由 4 个 SQL 脚本生成宽表：

```text
sql/01 质量检查 → sql/02 clean_* 视图 → sql/03 生成 mart_order 等 4 张表 → sql/04 对账
```

`mart_order` 是订单粒度宽表，99,441 行，是模块三、四、六的唯一输入。导出方式：在数据库客户端执行 `SELECT * FROM mart_order` 并导出为 CSV，Navicat 和 MySQL Workbench 均可，需保证字段顺序与表定义一致、编码 UTF-8。

宽表约 50 MB，不随项目分发。重新生成后需与模块四记录的哈希一致（见第 6 节）。

### 2.2 后续流程

```text
宽表 mart_order（CSV）
  ├─ sql/05 经营查询（数据库客户端执行）→ 模块二
  ├─ src/03 → results/module3 + figures/m3_*
  ├─ src/04 → results/module4 + models/
  │                    ↓
  ├─ Power BI 看板 ← 模块三标签 + 模块四冻结 XGBoost 分数
  └─ src/06_* ← 模块四冻结产物 + 宽表历史标签
```

### 2.3 执行命令

```bash
pip install -r requirements.txt

python src/03_customer_analysis.py --input <宽表CSV路径> --output .
python src/04_train_delay_model.py --input <宽表CSV路径> --output .

python src/06_build_scores.py          # 必须最先跑，其余五个都读它的输出
python src/06_strategy_eval.py
python src/06_value_targeting.py
python src/06_ab_test_design.py
python src/06_risk_grading.py
python src/06_build_excel.py
```

`03`、`04` 通过 `--input` 接收宽表路径；`06_build_scores.py` 把这路径写死在脚本第 30 行（`ROOT / 'data' / 'mart_order.csv'`），运行前需把宽表放回 `data/` 或改这一行。

依赖只有三条：`03` 依赖 `analysis_contract.py`；`06_build_scores` 依赖模块四的特征函数、
`models/` 和 `results/module4/`；其余五个 `06_*.py` 都读 `results/module6/02_test_scores.csv`。

### 容易混淆的三处分工

- **SQL `mart_customer` 与 Python RFM：不同粒度。** 前者是全量客户的基础汇总，只用于订单/金额对账；
  后者是截止 2018-09-01 的已送达客户分层。对外说"六分层"只用模块三结果。
- **模块四与模块六的特征：单一实现。** `06_build_scores.py` 用 `importlib` 调用模块四的 `features()`，
  并与冻结指标逐项核对，模块六不产生第二个训练模型。
- **CSV 与 Excel：数据与阅读视图。** 模块六的 CSV/JSON 是机读来源，`module6_summary.xlsx` 只是汇总副本，
  不作独立计算依据。

---

## 3. 经营指标口径（模块二）

总订单含全部状态；商品金额、运费、客单价只算 `delivered`。商品金额**不含运费**，也不等同支付金额，
支付差异不强行调平。商品客单价 = 有效商品金额之和 ÷ 金额有效的已交付订单数；完整金额缺失时不补零，
Q01/Q04 单列缺失数量。交付完成率是快照状态比例，不是最终成功率。

**延迟率**用模块一 `is_delayed` 非空样本，限定已交付订单；按自然日判断超期，承诺日当天送达不算延迟。
配送天数和评价各按自己的非空样本计算，**分母不同**；评价使用模块一选定的订单级评价，未重新连接原评价记录。

**月度经营**按购买月份归属。Q04/Q06 保留购买时间缺失组以便对账；Q05 排除缺失时间，
只在相邻日历月份间算环比，前月为零或不存在时返回 NULL。首尾月份不纳入自动峰值比较，
最新批次的交付状态可能尚未成熟。

**类别与卖家金额**只纳入订单级商品金额完整的已交付订单，确保与 Q01 对账；类别运费比例另用商品、
运费均完整的配对订单。未知类别/地区/卖家保留。类别与卖家订单数是组内去重值，**跨组不能相加**。

**卖家履约**先去重"卖家-订单"，同一订单多商品不重复加权；多卖家订单可同时归入多组，
所以不能把卖家延迟订单数相加当平台值，也不能直接归责。地区使用客户所在州。

| 查询 | 回答什么问题 |
|---|---|
| Q01 | 规模多大；核心指标、缺失计数、时间范围 |
| Q02 | 订单状态结构与占比 |
| Q03 | 履约和评价：延迟率、配送天数、评分及有效分母 |
| Q04 | 每月订单量、金额、运费、客单价 |
| Q05 | 相邻月份订单、金额、客单价环比 |
| Q06 | 购买批次的交付完成率与延迟率 |
| Q07 | 类别金额占比、销量、组内订单数 |
| Q08 | 类别运费负担（运费/商品金额，配对样本） |
| Q09 | 客户州金额、占比、订单量、客单价 |
| Q10 | 客户州延迟率与有效样本数 |
| Q11 | 卖家贡献排名、占比、累计占比 |
| Q12 | 卖家的关联订单延迟率（去重订单） |

图表和简报的地区/类别排名使用**至少 100 笔相关有效样本**；这是展示门槛，不做显著性检验。
SQL 保留全量结果供复核。

**对账**：订单唯一；状态总订单量；月度总订单量与已交付订单量；月度/类别/地区/卖家商品金额；
月度运费；地区已交付订单量；月度延迟有效样本。金额容差 0.01 BRL。NULL 金额视为对账失败，不用 0 替代。

---

## 4. 订单宽表字段

### 4.1 原始表

| 表 | 主键 | 关键字段 |
|---|---|---|
| `olist_orders_dataset` | `order_id` | `order_status`、5 个时间戳 |
| `olist_customers_dataset` | `customer_id` | `customer_unique_id`、`customer_state`、邮编 |
| `olist_order_items_dataset` | `order_id` + `order_item_id` | `product_id`、`seller_id`、`price`、`freight_value` |
| `olist_order_payments_dataset` | `order_id` + `payment_sequential` | `payment_type`、`payment_value` |
| `olist_order_reviews_dataset` | `review_id`（不唯一） | `order_id`、`review_score`、两个时间戳 |
| `olist_products_dataset` | `product_id` | `product_category_name`（葡语）、重量尺寸 |
| `olist_sellers_dataset` | `seller_id` | `seller_state`、邮编 |
| `olist_geolocation_dataset` | 无 | 邮编、经纬度（一邮多行，需聚合） |
| `product_category_name_translation` | `product_category_name` | 葡→英映射，71 条 |

三处关系：`customer_id` 是**订单级**标识，同一自然人多次下单会得到不同的 `customer_id`；
`order_items` 和 `payments` 与订单是一对多；`reviews` 存在一条评价对应多行、一个订单多条评价。

### 4.2 分析表

| 表 | 粒度 | 主键 | 说明 |
|---|---|---|---|
| `dim_zip_geo` | 邮编前缀 | `zip_code_prefix` | 有效坐标的算术均值，非中位数 |
| `fact_order_item` | 订单商品明细 | `order_id` + `order_item_id` | 已补齐客户、商品、卖家信息 |
| `mart_order` | **订单** | `order_id` | 订单级宽表，后续模块的唯一输入 |
| `mart_customer` | 客户 | `customer_unique_id` | 客户级汇总，只用于对账 |

### 4.3 `mart_order` 主要字段

**订单主体**：`order_id`、`customer_id`（订单级，不可识别自然人）、`order_status`、
`order_purchase_timestamp`（下单）、`order_approved_at`（支付审核）、`order_delivered_carrier_date`（交承运商）、
`order_delivered_customer_date`（签收）、`order_estimated_delivery_date`（**下单时承诺的**送达日期）。

**质量标记**（均为 0/1，1 表示命中该问题）：`flag_time_anomaly` 时间顺序异常、`flag_missing_delivery` 已送达但缺签收时间、
`flag_canceled_with_delivery` 取消却有签收时间、`flag_customer_unmatched` 找不到客户、`flag_customer_unique_id_missing`、
`flag_no_items`、`flag_no_payment`、`flag_zero_payment_record`、`flag_no_valid_review`、`flag_geo_unmatched`、
`flag_geo_unavailable`（落在粗筛矩形外）、`flag_item_amount_missing`、`flag_payment_amount_missing`、
`flag_product_unmatched`、`flag_seller_unmatched`、`flag_missing_category`、`flag_missing_weight`、`flag_missing_dimension`。

**可用性标记**决定下游能否使用该行，属于口径而非数据质量：
`usable_delivery_duration`（配送时长可算）、`usable_delay_label`（延迟标签可信，承诺与签收日期均有效）、
`usable_shipping_duration`（承运时长可算）。

**客户**：`customer_unique_id`（**自然人标识，客户分析的唯一正确键**）、`customer_city`、`customer_state`（巴西 27 个联邦单位）、
`customer_zip_code_prefix`（5 位字符串）、`customer_lat` / `customer_lng`。

**金额与数量**：`item_count`、`distinct_product_count`、`seller_count`、`item_amount`（**不含运费**）、
`freight_amount`、`amount_with_freight`、`payment_record_count`、`payment_amount`、
`payment_amount_difference`（支付减含运费金额，用于对账）。缺失保持 NULL，**不补 0**。

**评价**：`review_score` 1–5 分。一个订单多条评价时取 `review_answer_timestamp` 最新的一条；
回答时间缺失用创建时间；仍并列时取**较低分**（保守口径，非确认哪条为真实最新）。`selected_review_time` 记录被选中那条的时间。

**履约**：`delivery_days`（下单到签收的自然日数，秒差/86400 未取整）、`shipping_days`、
`is_delayed`（1 表示实际签收晚于承诺日期，**仅在 `usable_delay_label = 1` 时有意义**）。

### 4.4 三个容易用错的地方

1. **用 `customer_id` 数客户。** 它是订单级的，会得出"复购率接近 0"的错误结论，必须用 `customer_unique_id`。
2. **认为 `is_delayed` 对所有行都有效。** 只有 `usable_delay_label = 1` 的行才有标签，
   直接对全表算均值会低估延迟率，因为未送达订单被当成 0。
3. **把 `review_score` 当成订单唯一评价。** 原始表里有 789 个 `review_id` 各出现 3 次、547 个订单有多条评价，
   宽表已做选择，但规则带主观成分。

---

## 5. 看板口径（模块五）

1. 日期一律是**购买日期**，不是送达日期；默认覆盖全部 99,441 笔订单。
2. 商品金额和运费只累计已送达订单；客单价 = 已送达商品金额 ÷ 已送达订单。BRL，不能解释为人民币或利润。
3. 已送达率 = 已送达订单 ÷ 全部订单；延迟率 = 有效延迟标签中的延迟订单 ÷ 有效延迟订单。
   **不能把标签缺失补为按时**。
4. 评价只用已送达且有效的 1–5 星记录；低评分为 1–2 星。均分按订单算，客户不等权。
5. 用户页的复购率在**当前日期和地区筛选窗口内**重新计数（客户 ≥ 2 笔已送达订单），全期约 3.00%；
   它不是模块三那个固定 90 天观察期的 1.29% 指标。
6. RFM 标签是 2018-09-01 快照，即使筛到 2017 年也不重建当时的客户状态，**不能用于无泄漏的历史营销回测**。
   州是每笔订单所在州，跨州客户会进入多个地区，不同地区客户数不可相加当全体客户数。
7. 用户画像按当前筛选订单算，所以金额和客户数可能不同于全期 RFM 表；RFM 标签本身不变。
   Cohort 是全量固定快照，不随筛选变化。
8. 风险页只包含模块四冻结 XGBoost 的 **18,603 笔**测试评分，其余订单不补分，更早月份显示无数据。
9. 风险 Top-K 在当前筛选内重排，K 向上取整；并列分数按 `order_id` 升序。它和"每天固定 20 笔"的
   运营容量不同，后者见 `results/module4/07_topk.csv`。
10. **风险分数不等同于真实概率。** 标签只用于评估，不能输入预测；识别到延迟不等于干预成功，
    也不等于实现真实收益。
11. 缺少订单类别和逐卖家 ID，未提供商品类别与卖家联动，也未用全期类别汇总冒充可筛选数据。
12. Power BI 源文件的导入与视觉对象搭建需在本地 Desktop 完成；Power Query 和 DAX 未经 Desktop 执行，
    不能宣称已通过实机验收。

---

## 6. 复现注意

**输入校验。** 宽表是模块三、四、六的唯一输入。`results/module4/05_run_config.json` 记录了它的 SHA-256：

```text
e8e971141d972c647f6403801c14dd4d76275664c1bdfc329248a7831197203c
```

`06_build_scores.py` 的第一道校验就是比对这个值，不一致直接终止。宽表需由 `sql/01`–`04` 重新生成后导出，不要手工编辑。

**重跑模块四会改变选型。** 冻结产物在 Python 3.12.14 下产生。同一份宽表、同样的
`scikit-learn==1.8.0` 和 `xgboost==3.4.1`，换 Python 3.13.14 重训，验证集 AP 变成：

| 候选 | 冻结记录（3.12） | 重跑结果（3.13） |
|---|---:|---:|
| XGBoost_depth2 | 0.323490 | 0.313977 |
| XGBoost_depth4 | 0.322737 | 0.315518 |
| 选出 | **depth2** | **depth4** |

两档只差 0.0008 和 0.0015，属于噪声量级，但排序发生反转。所以：

- 复现报告里的数字时不要重跑模块四，直接用 `models/` 与 `results/module4/` 的冻结产物。
  在这个前提下，模块六 12 个结果文件可以逐位复现（实测最大数值差 0.00e+00）。
- 若重跑模块四，`06_build_scores.py` 会因硬编码 `XGBoost_depth2` 而报错，
  需同步修改它和 `06_risk_grading.py` 里的模型名列表。

**金额的性质。** 模块六所有金额都是未经验证参数下的**历史标签情景值**，不是观测到的干预收益。
项目没有上线干预，也没有做因果实验。
