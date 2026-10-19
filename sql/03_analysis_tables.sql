/* 任务六：分析数据表（MySQL 5.7 / 8.0）
前提：已执行04_cleaning.sql；在Navicat中选中Olist数据库。
执行：首次整段运行。仅新建4张分析表及2张临时汇总表，不改原始表/clean视图。
如果同名分析表已经存在，CREATE TABLE会报错，请停止，不要直接跳过继续运行。
本文件未连接本地MySQL执行；末尾3个结果集用于验收。
分析表为运行时快照；原始数据更新后需重新构建，不会自动刷新。

口径：金额BRL；时间按原数据。订单、客户统计含全部状态；
另保留已送达商品金额及其有效订单数，计算客单价时二者相除。
明细/支付先分别汇总再关联，金额缺失不补0。
邮编统一为5位字符串（原始数据为数值或不足5位字符串时补前导0）。
地理代表坐标使用有效坐标的算术均值；不是中位数，也非精确地址。
有效坐标使用巴西主体区域宽松矩形：纬度[-34,6]、经度[-74,-34]；
仅作粗筛，不代表经过国界校验；范围外坐标不用于均值，原始行仍计数。
完全相同坐标记录先去重，避免重复导入/重复采样对均值重复加权。
*/

-- 1. 邮编维表：包括没有有效坐标的邮编，代表坐标允许NULL。
CREATE TABLE dim_zip_geo AS
SELECT z.zip_code_prefix,
       z.raw_coordinate_rows,
       COALESCE(g.valid_coordinate_pairs, 0) AS valid_coordinate_pairs,
       g.representative_lat, g.representative_lng
FROM (
    SELECT LPAD(TRIM(CAST(geolocation_zip_code_prefix AS CHAR)),5,'0') AS zip_code_prefix,
           COUNT(*) AS raw_coordinate_rows
    FROM olist_geolocation_dataset
    WHERE TRIM(CAST(geolocation_zip_code_prefix AS CHAR)) REGEXP '^[0-9]{1,5}$'
    GROUP BY LPAD(TRIM(CAST(geolocation_zip_code_prefix AS CHAR)),5,'0')
) z
LEFT JOIN (
    SELECT v.zip_code_prefix, COUNT(*) AS valid_coordinate_pairs,
           AVG(v.lat) AS representative_lat, AVG(v.lng) AS representative_lng
    FROM (
        SELECT DISTINCT
            LPAD(TRIM(CAST(geolocation_zip_code_prefix AS CHAR)),5,'0') AS zip_code_prefix,
            geolocation_lat AS lat, geolocation_lng AS lng
        FROM olist_geolocation_dataset
        WHERE TRIM(CAST(geolocation_zip_code_prefix AS CHAR)) REGEXP '^[0-9]{1,5}$'
          AND geolocation_lat BETWEEN -34 AND 6
          AND geolocation_lng BETWEEN -74 AND -34
    ) v
    GROUP BY v.zip_code_prefix
) g ON z.zip_code_prefix = g.zip_code_prefix;
ALTER TABLE dim_zip_geo ADD PRIMARY KEY (zip_code_prefix);

-- 2. 商品项事实表：每个商品项一行，类别保留葡语原值及展示字段。
CREATE TABLE fact_order_item AS
SELECT i.order_id, i.order_item_id, i.product_id, i.seller_id,
       i.shipping_limit_date, i.price, i.freight_value,
       p.product_category_name,
       COALESCE(p.category_display, '未知类别') AS category_display,
       p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm,
       COALESCE(p.flag_missing_category,1) AS flag_missing_category,
       COALESCE(p.flag_missing_weight,1) AS flag_missing_weight,
       COALESCE(p.flag_missing_dimension,1) AS flag_missing_dimension,
       NULLIF(TRIM(s.seller_city),'') AS seller_city,
       NULLIF(TRIM(s.seller_state),'') AS seller_state,
       CASE WHEN TRIM(CAST(s.seller_zip_code_prefix AS CHAR)) REGEXP '^[0-9]{1,5}$'
            THEN LPAD(TRIM(CAST(s.seller_zip_code_prefix AS CHAR)),5,'0')
       END AS seller_zip_code_prefix,
       CASE WHEN p.product_id IS NULL THEN 1 ELSE 0 END AS flag_product_unmatched,
       CASE WHEN s.seller_id IS NULL THEN 1 ELSE 0 END AS flag_seller_unmatched,
       CASE WHEN i.price IS NULL OR i.freight_value IS NULL THEN 1 ELSE 0 END AS flag_item_amount_missing
FROM olist_order_items_dataset i
LEFT JOIN clean_products p ON i.product_id = p.product_id
LEFT JOIN olist_sellers_dataset s ON i.seller_id = s.seller_id;
ALTER TABLE fact_order_item ADD PRIMARY KEY (order_id, order_item_id);

-- 临时汇总只在当前连接中存在，不增加永久业务表。
CREATE TEMPORARY TABLE tmp06_items AS
SELECT order_id, COUNT(*) AS item_count,
       COUNT(DISTINCT product_id) AS distinct_product_count,
       COUNT(DISTINCT seller_id) AS seller_count,
       CASE WHEN COUNT(price)=COUNT(*) THEN SUM(price) END AS item_amount,
       CASE WHEN COUNT(freight_value)=COUNT(*) THEN SUM(freight_value) END AS freight_amount,
       MAX(flag_item_amount_missing) AS flag_item_amount_missing,
       MAX(flag_product_unmatched) AS flag_product_unmatched,
       MAX(flag_seller_unmatched) AS flag_seller_unmatched,
       MAX(flag_missing_category) AS flag_missing_category,
       MAX(flag_missing_weight) AS flag_missing_weight,
       MAX(flag_missing_dimension) AS flag_missing_dimension
FROM fact_order_item GROUP BY order_id;
ALTER TABLE tmp06_items ADD PRIMARY KEY (order_id);

CREATE TEMPORARY TABLE tmp06_payments AS
SELECT order_id, COUNT(*) AS payment_record_count,
       CASE WHEN COUNT(payment_value)=COUNT(*) THEN SUM(payment_value) END AS payment_amount,
       CASE WHEN COUNT(payment_value)<COUNT(*) THEN 1 ELSE 0 END AS flag_payment_amount_missing,
       MAX(CASE WHEN payment_value=0 THEN 1 ELSE 0 END) AS flag_zero_payment_record
FROM olist_order_payments_dataset GROUP BY order_id;
ALTER TABLE tmp06_payments ADD PRIMARY KEY (order_id);

-- 3. 订单宽表：o.*包括全部原始订单时间、状态及任务四质量/资格标记。
CREATE TABLE mart_order AS
SELECT o.*,
       NULLIF(TRIM(c.customer_unique_id),'') AS customer_unique_id,
       NULLIF(TRIM(c.customer_city),'') AS customer_city,
       NULLIF(TRIM(c.customer_state),'') AS customer_state,
       CASE WHEN TRIM(CAST(c.customer_zip_code_prefix AS CHAR)) REGEXP '^[0-9]{1,5}$'
            THEN LPAD(TRIM(CAST(c.customer_zip_code_prefix AS CHAR)),5,'0')
       END AS customer_zip_code_prefix,
       g.representative_lat AS customer_lat,
       g.representative_lng AS customer_lng,
       COALESCE(i.item_count,0) AS item_count,
       COALESCE(i.distinct_product_count,0) AS distinct_product_count,
       COALESCE(i.seller_count,0) AS seller_count,
       i.item_amount, i.freight_amount,
       i.item_amount+i.freight_amount AS amount_with_freight,
       COALESCE(p.payment_record_count,0) AS payment_record_count,
       p.payment_amount,
       p.payment_amount-i.item_amount-i.freight_amount AS payment_amount_difference,
       r.review_score, r.selected_review_time,
       CASE WHEN o.usable_delivery_duration=1
            THEN TIMESTAMPDIFF(SECOND,o.order_purchase_timestamp,o.order_delivered_customer_date)/86400.0
       END AS delivery_days,
       CASE WHEN o.usable_shipping_duration=1
            THEN TIMESTAMPDIFF(SECOND,o.order_delivered_carrier_date,o.order_delivered_customer_date)/86400.0
       END AS shipping_days,
       CASE WHEN o.usable_delay_label=1
            THEN CASE WHEN DATE(o.order_delivered_customer_date)>DATE(o.order_estimated_delivery_date)
                      THEN 1 ELSE 0 END
       END AS is_delayed,
       CASE WHEN c.customer_id IS NULL THEN 1 ELSE 0 END AS flag_customer_unmatched,
       CASE WHEN NULLIF(TRIM(c.customer_unique_id),'') IS NULL THEN 1 ELSE 0 END AS flag_customer_unique_id_missing,
       CASE WHEN i.order_id IS NULL THEN 1 ELSE 0 END AS flag_no_items,
       CASE WHEN p.order_id IS NULL THEN 1 ELSE 0 END AS flag_no_payment,
       CASE WHEN r.order_id IS NULL THEN 1 ELSE 0 END AS flag_no_valid_review,
       CASE WHEN g.zip_code_prefix IS NULL THEN 1 ELSE 0 END AS flag_geo_unmatched,
       CASE WHEN g.representative_lat IS NULL OR g.representative_lng IS NULL
            THEN 1 ELSE 0 END AS flag_geo_unavailable,
       COALESCE(i.flag_item_amount_missing,1) AS flag_item_amount_missing,
       COALESCE(p.flag_payment_amount_missing,1) AS flag_payment_amount_missing,
       COALESCE(p.flag_zero_payment_record,0) AS flag_zero_payment_record,
       COALESCE(i.flag_product_unmatched,0) AS flag_product_unmatched,
       COALESCE(i.flag_seller_unmatched,0) AS flag_seller_unmatched,
       i.flag_missing_category, i.flag_missing_weight, i.flag_missing_dimension
FROM clean_orders o
LEFT JOIN olist_customers_dataset c ON o.customer_id=c.customer_id
LEFT JOIN tmp06_items i ON o.order_id=i.order_id
LEFT JOIN tmp06_payments p ON o.order_id=p.order_id
LEFT JOIN clean_order_reviews r ON o.order_id=r.order_id
LEFT JOIN dim_zip_geo g
  ON g.zip_code_prefix = CASE
       WHEN TRIM(CAST(c.customer_zip_code_prefix AS CHAR)) REGEXP '^[0-9]{1,5}$'
       THEN LPAD(TRIM(CAST(c.customer_zip_code_prefix AS CHAR)),5,'0') END;
ALTER TABLE mart_order ADD PRIMARY KEY (order_id);
CREATE INDEX idx_mart_order_customer ON mart_order (customer_unique_id);
CREATE INDEX idx_mart_order_purchase ON mart_order (order_purchase_timestamp);

-- 4. 客户历史汇总：每个可识别且有订单的真实客户一行。
-- 所有状态的首末次下单时间，与已送达首末次下单时间分开。
-- 金额为非NULL订单金额之和；同时保留完整金额订单数，避免把缺失误当0。
-- 无已送达订单时已送达金额为NULL，不能解释为缺失支付或经营金额为零。
CREATE TABLE mart_customer AS
SELECT customer_unique_id,
       MIN(order_purchase_timestamp) AS first_purchase_time,
       MAX(order_purchase_timestamp) AS last_purchase_time,
       COUNT(*) AS order_count,
       SUM(CASE WHEN order_status='delivered' THEN 1 ELSE 0 END) AS delivered_order_count,
       MIN(CASE WHEN order_status='delivered' THEN order_purchase_timestamp END) AS first_delivered_purchase_time,
       MAX(CASE WHEN order_status='delivered' THEN order_purchase_timestamp END) AS last_delivered_purchase_time,
       SUM(item_amount) AS item_amount_all_status,
       COUNT(item_amount) AS orders_with_complete_item_amount,
       SUM(CASE WHEN order_status='delivered' THEN item_amount END) AS delivered_item_amount,
       SUM(CASE WHEN order_status='delivered' AND item_amount IS NOT NULL THEN 1 ELSE 0 END)
           AS delivered_orders_with_complete_item_amount,
       SUM(freight_amount) AS freight_amount_all_status,
       SUM(payment_amount) AS payment_amount_all_status,
       COUNT(payment_amount) AS orders_with_complete_payment_amount,
       SUM(flag_no_items) AS orders_without_items,
       SUM(flag_no_payment) AS orders_without_payment
FROM mart_order
WHERE customer_unique_id IS NOT NULL
GROUP BY customer_unique_id;
ALTER TABLE mart_customer ADD PRIMARY KEY (customer_unique_id);

-- 验收结果1：行数及唯一键；duplicate_key_groups应全部为0。
SELECT 'dim_zip_geo' AS table_name, COUNT(*) AS row_count,
       (SELECT COUNT(*) FROM (SELECT zip_code_prefix FROM dim_zip_geo GROUP BY zip_code_prefix HAVING COUNT(*)>1) d) AS duplicate_key_groups
FROM dim_zip_geo
UNION ALL
SELECT 'fact_order_item', COUNT(*),
       (SELECT COUNT(*) FROM (SELECT order_id,order_item_id FROM fact_order_item GROUP BY order_id,order_item_id HAVING COUNT(*)>1) d)
FROM fact_order_item
UNION ALL
SELECT 'mart_order', COUNT(*),
       (SELECT COUNT(*) FROM (SELECT order_id FROM mart_order GROUP BY order_id HAVING COUNT(*)>1) d)
FROM mart_order
UNION ALL
SELECT 'mart_customer', COUNT(*),
       (SELECT COUNT(*) FROM (SELECT customer_unique_id FROM mart_customer GROUP BY customer_unique_id HAVING COUNT(*)>1) d)
FROM mart_customer;

-- 验收结果2：结构核对，difference应全部为0。
SELECT '明细行数' AS check_item,
       (SELECT COUNT(*) FROM fact_order_item)-(SELECT COUNT(*) FROM olist_order_items_dataset) AS difference
UNION ALL
SELECT '订单行数', (SELECT COUNT(*) FROM mart_order)-(SELECT COUNT(*) FROM clean_orders)
UNION ALL
SELECT '客户汇总订单数',
       (SELECT SUM(order_count) FROM mart_customer)-(SELECT COUNT(*) FROM mart_order WHERE customer_unique_id IS NOT NULL)
UNION ALL
SELECT '客户数量',
       (SELECT COUNT(*) FROM mart_customer)-(SELECT COUNT(DISTINCT customer_unique_id) FROM mart_order);

-- 验收结果3：必要质量/金额核对；金额差应为0（允许浮点误差<0.01）。
SELECT '订单商品金额与明细一致' AS check_item,
       (SELECT SUM(item_amount) FROM mart_order)-
       (SELECT SUM(price) FROM fact_order_item WHERE order_id IN (SELECT order_id FROM mart_order WHERE item_amount IS NOT NULL)) AS result_value
UNION ALL
SELECT '订单运费与明细一致',
       (SELECT SUM(freight_amount) FROM mart_order)-
       (SELECT SUM(freight_value) FROM fact_order_item WHERE order_id IN (SELECT order_id FROM mart_order WHERE freight_amount IS NOT NULL))
UNION ALL
SELECT '订单支付与支付表一致',
       (SELECT SUM(payment_amount) FROM mart_order)-
       (SELECT SUM(payment_value) FROM olist_order_payments_dataset WHERE order_id IN (SELECT order_id FROM mart_order WHERE payment_amount IS NOT NULL))
UNION ALL
SELECT '订单没有明细', SUM(flag_no_items) FROM mart_order
UNION ALL
SELECT '订单没有支付', SUM(flag_no_payment) FROM mart_order
UNION ALL
SELECT '订单客户关联失败', SUM(flag_customer_unmatched) FROM mart_order
UNION ALL
SELECT '订单客户标识缺失', SUM(flag_customer_unique_id_missing) FROM mart_order
UNION ALL
SELECT '订单地理坐标不可用', SUM(flag_geo_unavailable) FROM mart_order
UNION ALL
SELECT '有效延迟标签订单数', COUNT(is_delayed) FROM mart_order;

/* 将以上3个结果分别保存到Excel的“表行数”“结构核对”“质量核对”。
预计订单99441行；无明细775笔；无支付1笔；有效延迟标签96470笔。
地理坐标不可用允许非0，保留缺失标记，不影响其他业务分析。
客户表首末次时间、金额是全历史描述汇总；不能直接当作历史预测特征使用。
本阶段不打RFM标签、不构建机器学习特征。
*/
