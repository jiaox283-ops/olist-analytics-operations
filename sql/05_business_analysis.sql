-- 模块二：MySQL 8.0+。只读查询，不修改模块一数据。
-- 在Navicat中选择olist数据库后运行；Python按Q编号分别执行。
-- 金额BRL；销售以delivered为准；比例0—1；缺失金额不补零。
-- Q01 经营总览
SELECT COUNT(*) total_orders, SUM(order_status='delivered') delivered_orders,
SUM(CASE WHEN order_status='delivered' THEN item_amount END) item_amount,
SUM(CASE WHEN order_status='delivered' THEN freight_amount END) freight_amount,
AVG(CASE WHEN order_status='delivered' THEN item_amount END) item_aov,
AVG(order_status='delivered') delivered_rate,
SUM(order_status='delivered' AND item_amount IS NULL) missing_item_orders,
SUM(order_status='delivered' AND freight_amount IS NULL) missing_freight_orders,
MIN(order_purchase_timestamp) first_purchase, MAX(order_purchase_timestamp) last_purchase,
COUNT(*)-COUNT(DISTINCT order_id) duplicate_orders,
SUM(order_purchase_timestamp IS NULL) missing_purchase_orders
FROM mart_order;
-- Q02 订单状态
SELECT COALESCE(order_status,'未知状态') order_status, COUNT(*) orders,
COUNT(*)/(SELECT COUNT(*) FROM mart_order) share FROM mart_order GROUP BY order_status ORDER BY orders DESC;
-- Q03 履约评价
SELECT COUNT(*) delivered_orders, COUNT(is_delayed) delay_valid_orders, SUM(is_delayed) delayed_orders,
AVG(is_delayed) delay_rate, COUNT(delivery_days) duration_valid_orders, AVG(delivery_days) delivery_days,
COUNT(review_score) review_valid_orders, AVG(review_score) review_score FROM mart_order WHERE order_status='delivered';
-- Q04 月度经营；NULL月份单独保留
SELECT DATE_FORMAT(order_purchase_timestamp,'%Y-%m') month, COUNT(*) total_orders,
SUM(order_status='delivered') delivered_orders,
SUM(CASE WHEN order_status='delivered' THEN item_amount END) item_amount,
SUM(CASE WHEN order_status='delivered' THEN freight_amount END) freight_amount,
AVG(CASE WHEN order_status='delivered' THEN item_amount END) item_aov,
SUM(order_status='delivered' AND item_amount IS NULL) missing_item_orders
FROM mart_order GROUP BY month ORDER BY month;
-- Q05 月环比；只在日历相邻月份间比较，缺月不越月计算
WITH m AS (SELECT DATE_FORMAT(order_purchase_timestamp,'%Y-%m-01') month,
COUNT(*) delivered_orders, SUM(item_amount) item_amount, AVG(item_amount) item_aov
FROM mart_order WHERE order_status='delivered' AND order_purchase_timestamp IS NOT NULL GROUP BY month)
SELECT c.month,c.delivered_orders,c.item_amount,c.item_aov,
c.delivered_orders/NULLIF(p.delivered_orders,0)-1 orders_mom,
c.item_amount/NULLIF(p.item_amount,0)-1 amount_mom,
c.item_aov/NULLIF(p.item_aov,0)-1 aov_mom
FROM m c LEFT JOIN m p ON p.month=DATE_FORMAT(DATE_SUB(c.month,INTERVAL 1 MONTH),'%Y-%m-01') ORDER BY c.month;
-- Q06 按购买月份的履约；最新月份可能未成熟
SELECT DATE_FORMAT(order_purchase_timestamp,'%Y-%m') month,COUNT(*) total_orders,
AVG(order_status='delivered') delivered_rate,
COUNT(CASE WHEN order_status='delivered' THEN is_delayed END) delay_valid_orders,
SUM(CASE WHEN order_status='delivered' THEN is_delayed END) delayed_orders,
AVG(CASE WHEN order_status='delivered' THEN is_delayed END) delay_rate
FROM mart_order GROUP BY month ORDER BY month;
-- Q07 商品类别贡献；金额只纳入订单商品金额完整的订单，保证跨粒度可对账
SELECT COALESCE(i.category_display,'未知类别') category,COUNT(DISTINCT i.order_id) orders,COUNT(*) units,
SUM(i.price) item_amount,
SUM(i.price)/(SELECT SUM(item_amount) FROM mart_order WHERE order_status='delivered') amount_share
FROM fact_order_item i JOIN mart_order o ON i.order_id=o.order_id
WHERE o.order_status='delivered' AND o.item_amount IS NOT NULL GROUP BY category ORDER BY item_amount DESC;
-- Q08 类别运费负担；使用商品与运费同时完整的配对样本
SELECT COALESCE(i.category_display,'未知类别') category,COUNT(DISTINCT i.order_id) orders,
SUM(i.price) item_amount,SUM(i.freight_value) freight_amount,
SUM(i.freight_value)/NULLIF(SUM(i.price),0) freight_item_ratio
FROM fact_order_item i JOIN mart_order o ON i.order_id=o.order_id
WHERE o.order_status='delivered' AND o.item_amount IS NOT NULL AND o.freight_amount IS NOT NULL
GROUP BY category ORDER BY freight_item_ratio DESC;
-- Q09 客户州经营贡献
SELECT COALESCE(customer_state,'未知地区') state,COUNT(*) orders,SUM(item_amount) item_amount,
SUM(item_amount)/(SELECT SUM(item_amount) FROM mart_order WHERE order_status='delivered') amount_share,
AVG(item_amount) item_aov,COUNT(item_amount) amount_valid_orders
FROM mart_order WHERE order_status='delivered' GROUP BY state ORDER BY item_amount DESC;
-- Q10 客户州履约；保留全部州，展示时筛选有效延迟样本>=100
SELECT COALESCE(customer_state,'未知地区') state,COUNT(*) orders,COUNT(is_delayed) delay_valid_orders,
SUM(is_delayed) delayed_orders,AVG(is_delayed) delay_rate,COUNT(delivery_days) duration_valid_orders,AVG(delivery_days) delivery_days
FROM mart_order WHERE order_status='delivered' GROUP BY state ORDER BY delay_rate DESC;
-- Q11 卖家贡献；相同金额按seller排序，保证累计占比稳定
WITH s AS (SELECT COALESCE(i.seller_id,'未知卖家') seller_id,COUNT(DISTINCT i.order_id) orders,SUM(i.price) item_amount
FROM fact_order_item i JOIN mart_order o ON i.order_id=o.order_id WHERE o.order_status='delivered' AND o.item_amount IS NOT NULL GROUP BY seller_id)
SELECT seller_id,orders,item_amount,item_amount/NULLIF(SUM(item_amount) OVER(),0) amount_share,
SUM(item_amount) OVER(ORDER BY item_amount DESC,seller_id ROWS UNBOUNDED PRECEDING)/NULLIF(SUM(item_amount) OVER(),0) cumulative_share
FROM s ORDER BY item_amount DESC,seller_id;
-- Q12 卖家关联订单履约；先去重卖家-订单，避免多商品重复加权
SELECT s.seller_id,COUNT(*) orders,COUNT(o.is_delayed) delay_valid_orders,SUM(o.is_delayed) delayed_orders,
AVG(o.is_delayed) delay_rate FROM
(SELECT DISTINCT COALESCE(seller_id,'未知卖家') seller_id,order_id FROM fact_order_item) s
JOIN mart_order o ON s.order_id=o.order_id WHERE o.order_status='delivered'
GROUP BY s.seller_id ORDER BY delay_rate DESC;
