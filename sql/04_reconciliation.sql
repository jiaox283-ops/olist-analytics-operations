/* 任务七：补充对账与验收（MySQL 5.7/8.0，只读）
前提：任务六已完成。Navicat中整段运行，输出4个结果集。
任务六已通过：四表键唯一、订单/明细无丢行、商品/运费/支付关联前后总额一致、
客户数量及订单数一致。原验收结果保留，本脚本仅补充未覆盖项目。
分类：检查通过 / 存在但可解释 / 尚未解决。
金额核对容差0.01 BRL；不通过项目不自动改数据，不用余额倒推修正金额。
金额字段存在NULL时，分别比较NULL状态和数值，不能用0掩盖缺失。
*/

-- 结果1：客户金额及已送达订单数对账（按同一客户、同一订单范围）。
-- 每项problem_count应为0。金额比较不将NULL自动当0。
SELECT '客户全部状态商品金额' AS check_item, COUNT(*) AS problem_count,
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END AS check_status
FROM mart_customer c
JOIN (
 SELECT customer_unique_id,SUM(item_amount) AS amount
 FROM mart_order WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
) o ON c.customer_unique_id=o.customer_unique_id
WHERE (c.item_amount_all_status IS NULL) <> (o.amount IS NULL)
   OR ABS(c.item_amount_all_status-o.amount)>0.01
UNION ALL
SELECT '客户已送达商品金额', COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END
FROM mart_customer c
JOIN (
 SELECT customer_unique_id,SUM(CASE WHEN order_status='delivered' THEN item_amount END) AS amount
 FROM mart_order WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
) o ON c.customer_unique_id=o.customer_unique_id
WHERE (c.delivered_item_amount IS NULL) <> (o.amount IS NULL)
   OR ABS(c.delivered_item_amount-o.amount)>0.01
UNION ALL
SELECT '客户全部状态运费', COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END
FROM mart_customer c
JOIN (
 SELECT customer_unique_id,SUM(freight_amount) AS amount
 FROM mart_order WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
) o ON c.customer_unique_id=o.customer_unique_id
WHERE (c.freight_amount_all_status IS NULL) <> (o.amount IS NULL)
   OR ABS(c.freight_amount_all_status-o.amount)>0.01
UNION ALL
SELECT '客户全部状态支付金额', COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END
FROM mart_customer c
JOIN (
 SELECT customer_unique_id,SUM(payment_amount) AS amount
 FROM mart_order WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
) o ON c.customer_unique_id=o.customer_unique_id
WHERE (c.payment_amount_all_status IS NULL) <> (o.amount IS NULL)
   OR ABS(c.payment_amount_all_status-o.amount)>0.01
UNION ALL
SELECT '客户已送达订单数', COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END
FROM mart_customer c
JOIN (
 SELECT customer_unique_id,SUM(CASE WHEN order_status='delivered' THEN 1 ELSE 0 END) AS n
 FROM mart_order WHERE customer_unique_id IS NOT NULL GROUP BY customer_unique_id
) o ON c.customer_unique_id=o.customer_unique_id
WHERE c.delivered_order_count<>o.n;

-- 结果2：关联失败数量、已知原因或待核查方向。项目可重叠，不能相加。
SELECT '订单找不到客户' AS check_item,SUM(flag_customer_unmatched) AS problem_count,
       CASE WHEN SUM(flag_customer_unmatched)=0 THEN '检查通过' ELSE '尚未解决' END AS check_status,
       '非0需核查customer_id，不能猜测客户身份' AS explanation
FROM mart_order
UNION ALL
SELECT '明细找不到商品',SUM(flag_product_unmatched),
       CASE WHEN SUM(flag_product_unmatched)=0 THEN '检查通过' ELSE '尚未解决' END,
       '非0需核查product_id；保留明细与金额' FROM fact_order_item
UNION ALL
SELECT '明细找不到卖家',SUM(flag_seller_unmatched),
       CASE WHEN SUM(flag_seller_unmatched)=0 THEN '检查通过' ELSE '尚未解决' END,
       '非0需核查seller_id；保留明细与金额' FROM fact_order_item
UNION ALL
SELECT '明细找不到订单',COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END,
       '非0表示孤立明细，需核查原表订单键'
FROM fact_order_item i WHERE NOT EXISTS(SELECT 1 FROM mart_order o WHERE o.order_id=i.order_id)
UNION ALL
SELECT '支付找不到订单',COUNT(*),
       CASE WHEN COUNT(*)=0 THEN '检查通过' ELSE '尚未解决' END,
       '非0表示孤立支付，需核查原表订单键'
FROM olist_order_payments_dataset p WHERE NOT EXISTS(SELECT 1 FROM mart_order o WHERE o.order_id=p.order_id)
UNION ALL
SELECT '订单缺少明细',SUM(flag_no_items),
       CASE WHEN SUM(flag_no_items)=0 THEN '检查通过' ELSE '尚未解决' END,
       '缺失已记录；状态分布见结果3，尚不能断言均由取消导致' FROM mart_order
UNION ALL
SELECT '订单缺少支付',SUM(flag_no_payment),
       CASE WHEN SUM(flag_no_payment)=0 THEN '检查通过' ELSE '尚未解决' END,
       '原始支付表缺少匹配记录；具体业务原因待核查，不补0' FROM mart_order
UNION ALL
SELECT '订单没有有效评价',SUM(flag_no_valid_review),
       CASE WHEN SUM(flag_no_valid_review)=0 THEN '检查通过' ELSE '存在但可解释' END,
       '评分可缺失；也可能有原评价但无有效评分，不据此删除订单' FROM mart_order
UNION ALL
SELECT '订单地理坐标不可用',SUM(flag_geo_unavailable),
       CASE WHEN SUM(flag_geo_unavailable)=0 THEN '检查通过' ELSE '存在但可解释' END,
       '邮编未匹配或无粗筛后有效坐标；见结果3，距离分析排除' FROM mart_order;

-- 结果3：按状态解释缺失及金额业务差异；eligible为金额两侧完整且有记录的订单。
-- amount_gap_orders只统计逐订单ROUND到分后的差额超过0.01，不改写任何金额。
SELECT order_status,COUNT(*) AS order_count,
       SUM(flag_no_items) AS no_items_orders,
       SUM(flag_no_payment) AS no_payment_orders,
       SUM(flag_no_valid_review) AS no_valid_review_orders,
       SUM(flag_geo_unmatched) AS zip_unmatched_orders,
       SUM(CASE WHEN flag_geo_unmatched=0 AND flag_geo_unavailable=1 THEN 1 ELSE 0 END)
           AS matched_zip_without_valid_coordinates,
       SUM(CASE WHEN amount_with_freight IS NOT NULL AND payment_amount IS NOT NULL
                THEN 1 ELSE 0 END) AS amount_comparable_orders,
       SUM(CASE WHEN amount_with_freight IS NOT NULL AND payment_amount IS NOT NULL
                     AND ABS(ROUND(payment_amount-amount_with_freight,2))>0.01
                THEN 1 ELSE 0 END) AS amount_gap_orders,
       SUM(CASE WHEN amount_with_freight IS NULL OR payment_amount IS NULL
                THEN 1 ELSE 0 END) AS amount_not_comparable_orders,
       MAX(CASE WHEN amount_with_freight IS NOT NULL AND payment_amount IS NOT NULL
                THEN ABS(ROUND(payment_amount-amount_with_freight,2)) END) AS max_abs_gap_brl
FROM mart_order GROUP BY order_status ORDER BY order_count DESC;

-- 结果4：业务差异样例（最多20笔，不代表全部差异记录）。
-- 支付减商品及运费的差额不是关联对账失败；具体来源尚未证实，先列“尚未解决”。
SELECT order_id,order_status,item_count,payment_record_count,
       item_amount,freight_amount,amount_with_freight,payment_amount,
       ROUND(payment_amount-amount_with_freight,2) AS business_amount_gap_brl,
       '尚未解决' AS check_status,
       '两侧记录完整但金额不等；需核查明细及支付，不能推断为优惠、退款或错误' AS explanation
FROM mart_order
WHERE amount_with_freight IS NOT NULL AND payment_amount IS NOT NULL
  AND ABS(ROUND(payment_amount-amount_with_freight,2))>0.01
ORDER BY ABS(ROUND(payment_amount-amount_with_freight,2)) DESC,order_id
LIMIT 20;

/* 保存4个结果集：客户对账、关联记录、状态与金额差异、差异样例。
第4个结果为空也应注明“0条”，不要误认为漏运行。
通过条件：任务六已有检查通过，且结果1 problem_count均为0；
结果2核心关联均为0；已知缺失与业务差异有数量、处理方式及待查原因记录。
存在已记录的未解业务问题时，可继续不依赖这些记录的分析，不能宣称所有问题已解释。
本脚本不修改表，不要求重跑任务六建表。
*/
