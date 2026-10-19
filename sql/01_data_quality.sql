-- =====================================================================================
-- 1.检查主键和联合键
-- =====================================================================================

-- 1.1 单字段候选主键
SELECT
    'orders.order_id' AS checked_key,
    COUNT(*) AS total_rows,
    COUNT(DISTINCT order_id) AS distinct_nonblank_keys,
    SUM(
        CASE WHEN order_id IS NULL OR TRIM(order_id) = ''
             THEN 1 ELSE 0 END
       ) AS missing_key_rows
FROM olist_orders_dataset

UNION ALL

SELECT
    'customers.customer_id',
    COUNT(*),
    COUNT(DISTINCT customer_id),
    SUM(
        CASE WHEN customer_id IS NULL OR TRIM(customer_id) = ''
             THEN 1 ELSE 0 END
    )
FROM olist_customers_dataset

UNION ALL

SELECT
    'products.product_id',
    COUNT(*),
    COUNT(DISTINCT product_id),
    SUM(
        CASE WHEN product_id IS NULL OR TRIM(product_id) = ''
             THEN 1 ELSE 0 END
    )
FROM olist_products_dataset

UNION ALL

SELECT
    'sellers.seller_id',
    COUNT(*),
    COUNT(DISTINCT seller_id),
    SUM(
        CASE WHEN seller_id IS NULL OR TRIM(seller_id) = ''
             THEN 1 ELSE 0 END
    )
FROM olist_sellers_dataset

UNION ALL

SELECT
    'translation.product_category_name',
    COUNT(*),
    COUNT(DISTINCT product_category_name),
    SUM(
        CASE
            WHEN product_category_name IS NULL
              OR TRIM(product_category_name) = ''
            THEN 1 ELSE 0
        END
    )
FROM product_category_name_translation;



-- 1.2 订单明细联合键
-- 联合键是否缺失
SELECT COUNT(*) AS missing_key_rows
FROM olist_order_items_dataset
WHERE order_id IS NULL
   OR TRIM(order_id) = ''
   OR order_item_id IS NULL;
   
-- 联合键是否重复
SELECT
    order_id,
    order_item_id,
    COUNT(*) AS row_count
FROM olist_order_items_dataset
GROUP BY order_id, order_item_id
HAVING COUNT(*) > 1
ORDER BY row_count DESC;

-- 1.3 支付记录联合键
SELECT COUNT(*) AS missing_key_rows
FROM olist_order_payments_dataset
WHERE order_id IS NULL
   OR TRIM(order_id) = ''
   OR payment_sequential IS NULL;
   
SELECT
    order_id,
    payment_sequential,
    COUNT(*) AS row_count
FROM olist_order_payments_dataset
GROUP BY order_id, payment_sequential
HAVING COUNT(*) > 1
ORDER BY row_count DESC;

-- 1.4 评价表单独检查
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT review_id) AS distinct_review_ids,
    COUNT(DISTINCT order_id) AS distinct_order_ids,
    SUM(
        CASE WHEN review_id IS NULL OR TRIM(review_id) = ''
             THEN 1 ELSE 0 END
    ) AS missing_review_id_rows,
    SUM(
        CASE WHEN order_id IS NULL OR TRIM(order_id) = ''
             THEN 1 ELSE 0 END
    ) AS missing_order_id_rows
FROM olist_order_reviews_dataset;

-- 重复评价标识
SELECT review_id, COUNT(*) AS row_count
FROM olist_order_reviews_dataset
GROUP BY review_id
HAVING COUNT(*) > 1
ORDER BY row_count DESC;

-- 一笔订单对应多条评价
SELECT order_id, COUNT(*) AS review_count
FROM olist_order_reviews_dataset
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY review_count DESC;


-- ===========================================================================
-- 2．统计所有字段的缺失数量及比例
-- ===========================================================================

SET SESSION group_concat_max_len = 1000000;

SELECT GROUP_CONCAT(
    CONCAT(
        'SELECT ',
        QUOTE(TABLE_NAME), ' AS table_name, ',
        QUOTE(COLUMN_NAME), ' AS column_name, ',
        'COUNT(*) AS total_rows, ',

        'COALESCE(SUM(CASE WHEN `',
        COLUMN_NAME,
        '` IS NULL THEN 1 ELSE 0 END), 0) AS null_rows, ',

        'COALESCE(SUM(CASE WHEN `',
        COLUMN_NAME,
        '` IS NOT NULL AND TRIM(CAST(`',
        COLUMN_NAME,
        '` AS CHAR)) = '''' THEN 1 ELSE 0 END), 0) ',
        'AS blank_rows, ',

        'COALESCE(SUM(CASE WHEN `',
        COLUMN_NAME,
        '` IS NULL OR TRIM(CAST(`',
        COLUMN_NAME,
        '` AS CHAR)) = '''' THEN 1 ELSE 0 END), 0) ',
        'AS missing_rows, ',

        'ROUND(100.0 * ',
        'COALESCE(SUM(CASE WHEN `',
        COLUMN_NAME,
        '` IS NULL OR TRIM(CAST(`',
        COLUMN_NAME,
        '` AS CHAR)) = '''' THEN 1 ELSE 0 END), 0) ',
        '/ NULLIF(COUNT(*), 0), 2) AS missing_pct ',

        'FROM `', TABLE_NAME, '`'
    )
    ORDER BY TABLE_NAME, ORDINAL_POSITION
    SEPARATOR ' UNION ALL '
) INTO @missing_sql
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
      'olist_customers_dataset',
      'olist_geolocation_dataset',
      'olist_order_items_dataset',
      'olist_order_payments_dataset',
      'olist_order_reviews_dataset',
      'olist_orders_dataset',
      'olist_products_dataset',
      'olist_sellers_dataset',
      'product_category_name_translation'
  );

PREPARE missing_stmt FROM @missing_sql;
EXECUTE missing_stmt;
DEALLOCATE PREPARE missing_stmt;


-- ========================================
-- 核心关联是否完整
-- ========================================

SELECT
    '订单找不到客户' AS check_item,
    COUNT(*) AS problem_rows
FROM olist_orders_dataset o
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_customers_dataset c
    WHERE c.customer_id = o.customer_id
)

UNION ALL

SELECT '明细找不到订单', COUNT(*)
FROM olist_order_items_dataset i
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_orders_dataset o
    WHERE o.order_id = i.order_id
)

UNION ALL

SELECT '明细找不到商品', COUNT(*)
FROM olist_order_items_dataset i
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_products_dataset p
    WHERE p.product_id = i.product_id
)

UNION ALL

SELECT '明细找不到卖家', COUNT(*)
FROM olist_order_items_dataset i
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_sellers_dataset s
    WHERE s.seller_id = i.seller_id
)

UNION ALL

SELECT '支付找不到订单', COUNT(*)
FROM olist_order_payments_dataset p
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_orders_dataset o
    WHERE o.order_id = p.order_id
)

UNION ALL

SELECT '订单没有明细', COUNT(*)
FROM olist_orders_dataset o
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_order_items_dataset i
    WHERE i.order_id = o.order_id
)

UNION ALL

SELECT '订单没有支付记录', COUNT(*)
FROM olist_orders_dataset o
WHERE NOT EXISTS (
    SELECT 1
    FROM olist_order_payments_dataset p
    WHERE p.order_id = o.order_id
);

-- 金额检查
SELECT
    '商品价格 price' AS check_item,
    COUNT(*) AS total_rows,
    SUM(CASE WHEN price < 0 THEN 1 ELSE 0 END) AS negative_rows,
    SUM(CASE WHEN price = 0 THEN 1 ELSE 0 END) AS zero_rows,
    MIN(price) AS min_value,
    MAX(price) AS max_value
FROM olist_order_items_dataset

UNION ALL

SELECT
    '运费 freight_value',
    COUNT(*),
    SUM(CASE WHEN freight_value < 0 THEN 1 ELSE 0 END),
    SUM(CASE WHEN freight_value = 0 THEN 1 ELSE 0 END),
    MIN(freight_value),
    MAX(freight_value)
FROM olist_order_items_dataset

UNION ALL

SELECT
    '支付金额 payment_value',
    COUNT(*),
    SUM(CASE WHEN payment_value < 0 THEN 1 ELSE 0 END),
    SUM(CASE WHEN payment_value = 0 THEN 1 ELSE 0 END),
    MIN(payment_value),
    MAX(payment_value)
FROM olist_order_payments_dataset;

-- 时间顺序检查
SELECT
    COUNT(*) AS total_orders,

    SUM(
        CASE
            WHEN order_approved_at < order_purchase_timestamp
            THEN 1 ELSE 0
        END
    ) AS approval_before_purchase,

    SUM(
        CASE
            WHEN order_delivered_carrier_date
                 < order_purchase_timestamp
            THEN 1 ELSE 0
        END
    ) AS carrier_before_purchase,

    SUM(
        CASE
            WHEN order_delivered_customer_date
                 < order_purchase_timestamp
            THEN 1 ELSE 0
        END
    ) AS delivery_before_purchase,

    SUM(
        CASE
            WHEN order_delivered_customer_date
                 < order_delivered_carrier_date
            THEN 1 ELSE 0
        END
    ) AS delivery_before_carrier,

    SUM(
        CASE
            WHEN DATE(order_estimated_delivery_date)
                 < DATE(order_purchase_timestamp)
            THEN 1 ELSE 0
        END
    ) AS estimated_date_before_purchase

FROM olist_orders_dataset;


-- 订单状态与时间完整性
SELECT
    order_status,
    COUNT(*) AS order_count,

    SUM(
        CASE
            WHEN order_approved_at IS NULL
            THEN 1 ELSE 0
        END
    ) AS missing_approval_time,

    SUM(
        CASE
            WHEN order_delivered_carrier_date IS NULL
            THEN 1 ELSE 0
        END
    ) AS missing_carrier_time,

    SUM(
        CASE
            WHEN order_delivered_customer_date IS NULL
            THEN 1 ELSE 0
        END
    ) AS missing_delivery_time,

    SUM(
        CASE
            WHEN order_delivered_customer_date IS NOT NULL
            THEN 1 ELSE 0
        END
    ) AS has_delivery_time

FROM olist_orders_dataset
GROUP BY order_status
ORDER BY order_count DESC;
