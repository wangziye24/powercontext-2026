# 订单筛选规格（orders-filter）

本文件是 `orders` 包筛选行为的唯一事实来源。每个条目有稳定编号，条目内容变化时编号不变。

## SPEC-001 状态过滤

- `filter_orders` 接受可选关键字参数 `status`。
- `status` 只能取 `paid`、`pending`、`cancelled` 之一，大小写敏感。
- 传入其它值时必须抛出 `InvalidStatusError`。
- 提供 `status` 时，只返回 `status` 等于该值的订单。

## SPEC-002 金额区间过滤

- `filter_orders` 接受可选关键字参数 `min_amount` 与 `max_amount`。
- 区间是**闭区间**：`min_amount <= amount <= max_amount`。
- 当 `min_amount` 与 `max_amount` 同时提供且 `min_amount > max_amount` 时，必须抛出 `InvalidRangeError`。
- 只提供其中一个端点时，另一端不设限制。

## SPEC-003 关键词搜索

- `filter_orders` 接受可选关键字参数 `keyword`。
- 关键词匹配是**大小写不敏感**的子串匹配，匹配范围是订单的 `name` 或 `customer_email`。
- `keyword` 为空字符串时不过滤（视为未提供）。

## SPEC-004 客户等级过滤

- `filter_orders` 接受可选关键字参数 `tier`，用于按客户等级筛选订单。
- 允许的等级集合为：`VIP`、`GOLD`、`SILVER`。
- 等级比较**大小写不敏感**：`tier="vip"` 与 `tier="VIP"` 等价。
- 传入不在允许集合中的等级时，必须抛出 `InvalidTierError`。
- `tier` 为 `None` 时不按等级过滤（视为未提供）。
- 订单的 `customer_tier` 为 `None` 表示该订单没有等级信息，只有 `tier="NONE"` 才能匹配到这类订单。

## SPEC-005 条件组合

- 多个过滤条件同时提供时，条件之间是**与（AND）**关系：订单必须同时满足全部条件才会被返回。
- 返回结果的顺序与输入顺序一致（稳定过滤，不重排）。

## SPEC-006 空条件

- 未提供任何过滤条件时，`filter_orders` 返回全部订单，顺序与输入一致。

## SPEC-007 缺少金额的订单

- `amount` 为 `None` 的订单，在任何金额区间过滤（提供 `min_amount` 或 `max_amount`）下都必须被排除。
- 未提供金额区间时，`amount` 为 `None` 的订单正常保留。
