# orders-filter —— 验证用业务工程

一个最小但完整的订单筛选服务，用于演示 SDD 上下文模块的工作流。

- **规格**：[`specs/filtering.md`](specs/filtering.md)（编号条目 SPEC-001 ~ SPEC-007）
- **代码**：`src/orders/`（`models.py` 数据模型、`errors.py` 错误类型、`filter.py` 筛选实现）
- **测试**：`tests/`（`test_filter_regression.py` 原有行为回归集、`test_filter_tier.py` 本次任务的��标测试）

## 本次开发任务

> 在订单筛选中**增加客户等级过滤**（`customer_tier`），同时保持 SPEC-001 ~ SPEC-003、SPEC-005 ~ SPEC-007 描述的原有行为完全不变。

## 独立运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

- `test_filter_regression.py` 在修改前后都应全部通过（回归保证）。
- `test_filter_tier.py` 在修改**前**因 `filter_orders` 尚未支持 `tier` 参数而失败，在修改**后**全部通过（目标功能）。
