/* 任务四：最小必要清洗（MySQL 5.7/8.0）
使用：在 Navicat 选择 Olist 数据库，打开本文件，整段执行。
前提：任务三关键键检查通过；订单、评价时间字段为 DATETIME。
只创建/更新以下 clean_ 视图，不修改原表。视图随原表实时变化。

清洗规则表
对象                 规则
订单                 全部保留，取消/未完成订单不删除；增加时间异常及分析资格标记。
商品类别             空字符串统一为 NULL；另提供未知类别展示字段及缺失标记。
商品重量、尺寸       不填补，保留 NULL；增加缺失标记。
评价                 原表保留；订单级只用1—5的整数评分，选最新回答时间；
                     回答时间缺失时用创建时间；两者均缺失排最后。
                     最新时间并列时取较低评分（保守口径，非确认哪条为真实最新）。
支付                 使用已修复原表；9条零支付保留，不填补缺少支付的订单。
重复记录             已通过的关键键不再全表去重；评价聚合为一单一分。
                     未发现并确认的完全重复，不执行删除；本脚本删除原记录数为0。
地理数据             暂不使用，不关联原始地理表；计算距离前再聚合检查。

注意：订单级评价视图只提供评分，不选取评价文本；有效评价以评分范围为准，
尚未定义“下单时已知评价”，未来预测建模不得直接使用未来评价。
*/

-- 1. 订单：保留全部字段及全部订单。
CREATE OR REPLACE VIEW clean_orders AS
SELECT o.*,
    CASE WHEN order_approved_at < order_purchase_timestamp
           OR order_delivered_carrier_date < order_purchase_timestamp
           OR order_delivered_customer_date < order_purchase_timestamp
           OR order_delivered_customer_date < order_delivered_carrier_date
           OR DATE(order_estimated_delivery_date) < DATE(order_purchase_timestamp)
         THEN 1 ELSE 0 END AS flag_time_anomaly,
    CASE WHEN order_status = 'delivered'
                   AND order_delivered_customer_date IS NULL
         THEN 1 ELSE 0 END AS flag_missing_delivery,
    CASE WHEN order_status = 'canceled'
                   AND order_delivered_customer_date IS NOT NULL
         THEN 1 ELSE 0 END AS flag_canceled_with_delivery,
    CASE WHEN order_status = 'delivered'
                   AND order_purchase_timestamp IS NOT NULL
                   AND order_delivered_customer_date >= order_purchase_timestamp
         THEN 1 ELSE 0 END AS usable_delivery_duration,
    CASE WHEN order_status = 'delivered'
                   AND order_purchase_timestamp IS NOT NULL
                   AND order_delivered_customer_date >= order_purchase_timestamp
                   AND order_estimated_delivery_date IS NOT NULL
                   AND DATE(order_estimated_delivery_date) >= DATE(order_purchase_timestamp)
         THEN 1 ELSE 0 END AS usable_delay_label,
    CASE WHEN order_status = 'delivered'
                   AND order_delivered_carrier_date >= order_purchase_timestamp
                   AND order_delivered_customer_date >= order_delivered_carrier_date
         THEN 1 ELSE 0 END AS usable_shipping_duration
FROM olist_orders_dataset o;

-- 各资格仅用于对应分析：配送总时长用 usable_delivery_duration=1；
-- 延迟标签用 usable_delay_label=1；运输时长用 usable_shipping_duration=1。
-- 延迟按日期比较，实际签收日期晚于预计日期才算延迟。
-- flag_time_anomaly 用于追踪，不将所有异常订单统一剔除。

-- 2. 商品：保留原表全部字段，仅规范类别并增加辅助字段。
CREATE OR REPLACE VIEW clean_products AS
SELECT
    product_id,
    NULLIF(TRIM(product_category_name), '') AS product_category_name,
    COALESCE(NULLIF(TRIM(product_category_name), ''), '未知类别') AS category_display,
    CASE WHEN NULLIF(TRIM(product_category_name), '') IS NULL
         THEN 1 ELSE 0 END AS flag_missing_category,
    product_name_lenght, product_description_lenght, product_photos_qty,
    product_weight_g, product_length_cm, product_height_cm, product_width_cm,
    CASE WHEN product_weight_g IS NULL THEN 1 ELSE 0 END AS flag_missing_weight,
    CASE WHEN product_length_cm IS NULL OR product_height_cm IS NULL
                   OR product_width_cm IS NULL
         THEN 1 ELSE 0 END AS flag_missing_dimension
FROM olist_products_dataset;

-- 3. 评价：先得出每单最新有效评分时间，再汇总该时间下的评分。
CREATE OR REPLACE VIEW clean_review_latest_time AS
SELECT order_id,
       MAX(COALESCE(review_answer_timestamp, review_creation_date,
                    CAST('1000-01-01 00:00:00' AS DATETIME))) AS latest_review_time
FROM olist_order_reviews_dataset
WHERE review_score BETWEEN 1 AND 5
  AND review_score = FLOOR(review_score)
  AND NULLIF(TRIM(order_id), '') IS NOT NULL
GROUP BY order_id;

CREATE OR REPLACE VIEW clean_order_reviews AS
SELECT r.order_id,
       MIN(r.review_score) AS review_score,
       NULLIF(t.latest_review_time, CAST('1000-01-01 00:00:00' AS DATETIME))
           AS selected_review_time,
       COUNT(*) AS latest_time_candidate_rows
FROM olist_order_reviews_dataset r
JOIN clean_review_latest_time t
  ON r.order_id = t.order_id
 AND COALESCE(r.review_answer_timestamp, r.review_creation_date,
              CAST('1000-01-01 00:00:00' AS DATETIME)) = t.latest_review_time
WHERE r.review_score BETWEEN 1 AND 5
  AND r.review_score = FLOOR(r.review_score)
GROUP BY r.order_id, t.latest_review_time;

-- 4. 处理前后行数对比：评价行数减少是分析粒度转换，不是删除原数据。
SELECT '订单' AS object_name,
       (SELECT COUNT(*) FROM olist_orders_dataset) AS before_rows,
       (SELECT COUNT(*) FROM clean_orders) AS after_rows,
       '保留全部订单' AS explanation
UNION ALL
SELECT '商品',
       (SELECT COUNT(*) FROM olist_products_dataset),
       (SELECT COUNT(*) FROM clean_products), '保留全部商品'
UNION ALL
SELECT '评价',
       (SELECT COUNT(*) FROM olist_order_reviews_dataset),
       (SELECT COUNT(*) FROM clean_order_reviews), '原始评价行转为每单一条有效评分'
UNION ALL
SELECT '支付',
       (SELECT COUNT(*) FROM olist_order_payments_dataset),
       (SELECT COUNT(*) FROM olist_order_payments_dataset), '沿用已修复原表，未删除';

-- 5. 清洗结果摘要（不同标记可重叠，不能直接相加）。
SELECT '时间顺序异常订单' AS check_item, SUM(flag_time_anomaly) AS row_count FROM clean_orders
UNION ALL
SELECT '已送达缺签收时间', SUM(flag_missing_delivery) FROM clean_orders
UNION ALL
SELECT '取消但有签收时间', SUM(flag_canceled_with_delivery) FROM clean_orders
UNION ALL
SELECT '可计算配送总时长', SUM(usable_delivery_duration) FROM clean_orders
UNION ALL
SELECT '可计算延迟标签', SUM(usable_delay_label) FROM clean_orders
UNION ALL
SELECT '缺商品类别', SUM(flag_missing_category) FROM clean_products
UNION ALL
SELECT '缺商品重量', SUM(flag_missing_weight) FROM clean_products
UNION ALL
SELECT '缺任一尺寸', SUM(flag_missing_dimension) FROM clean_products
UNION ALL
SELECT '最新时间有多条候选的评价订单', COUNT(*) FROM clean_order_reviews
WHERE latest_time_candidate_rows > 1
UNION ALL
SELECT '订单级评价重复键（应为0）', COUNT(*) FROM
(SELECT order_id FROM clean_order_reviews GROUP BY order_id HAVING COUNT(*) > 1) d;

/* 完成标准
1. 脚本无报错；订单、商品前后行数一致，支付保持103886行（原表未再变更时）。
2. 订单级评价重复键为0；原评价表仍然保留。
3. 将最后两个结果集分别存为“行数对比”“清洗摘要”，即可完成本阶段验收。
本文件在生成环境未连接你的本地数据库，未实际执行；数量以本次运行结果为准。
后续关联：订单连 clean_products 需经过订单明细，评价可按 order_id 关联。
明细与支付必须各自先聚合到订单，再关联，避免金额重复放大。
*/
