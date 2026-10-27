# 验证样例

本样例用一个最小但完整的业务工程演示整套流程：**导入资料 → 检索上下文 → MiniMax 改代码 → 跑测试 → 状态复用**。

## 1. 任务描述

> 在订单筛选中**增加客户等级过滤**（`customer_tier`），
> 同时保持 SPEC-001 ~ SPEC-003、SPEC-005 ~ SPEC-007 描述的原有行为完全不变。

对应的命令参数：

```bash
sddctx context --target-spec SPEC-004
sddctx apply   --target-spec SPEC-004
sddctx verify  --phase before --target-spec SPEC-004
sddctx verify  --phase after  --target-spec SPEC-004
```

## 2. 有效规格

规格文件：`minimax/sample-project/specs/filtering.md`，条目编号稳定、内容可更新。

| 编号 | 标题 | 本次任务中的角色 |
| --- | --- | --- |
| SPEC-001 | 状态过滤 | 必须保持的原有行为 |
| SPEC-002 | 金额区间过滤 | 必须保持的原有行为 |
| SPEC-003 | 关键词搜索 | 必须保持的原有行为 |
| **SPEC-004** | **客户等级过滤** | **本次要实现** |
| SPEC-005 | 条件组合（AND） | 必须保持的原有行为 |
| SPEC-006 | 空条件返回全部 | 必须保持的原有行为 |
| SPEC-007 | 缺少金额的订单 | 必须保持的原有行为 |

## 3. 上下文中必须包含的来源

| 类别 | 必须包含的来源 | 为什么必须包含 |
| --- | --- | --- |
| 规格 | `sample-project/specs/filtering.md#SPEC-004` | 目标功能的唯一事实来源（允许的等级集合、大小写规则、`NONE` 语义、错误类型） |
| 规格 | `sample-project/specs/filtering.md#SPEC-001`、`#SPEC-002`、`#SPEC-003`、`#SPEC-005`、`#SPEC-006`、`#SPEC-007` | 界定"哪些原有行为必须保持" |
| 代码 | `sample-project/src/orders/filter.py::filter_orders` | 本次要修改的函数（新增 `tier` 参数与过滤逻辑） |
| 代码 | `sample-project/src/orders/errors.py` | 错误类型约定（`OrderFilterError` 基类与既有命名风格） |
| 测试 | `sample-project/tests/test_filter_tier.py` | 目标功能测试（8 个用例，覆盖 SPEC-004） |
| 测试 | `sample-project/tests/test_filter_regression.py` | 回归测试（13 个用例，覆盖 SPEC-001/002/003/005/006/007） |

以上来源都真实存在于仓库中，检索结果里的 `source_path` 与 `source_hash` 可逐条核对。

## 4. 初始数据

| 数据 | 位置 | 初始状态 |
| --- | --- | --- |
| 工程资料索引 | `~/.sddctx/seekdb`（内嵌 seekDB） | 由 `sddctx ingest` 从 `sample-project/` 建立：8 个文档、7 条规格、7 个代码符号、20 个测试函数 |
| 任务状态 | `~/.sddctx/powercontext/seekdb`（PowerContext 的内嵌 seekDB） | **空**。流程从零状态开始记录 |

建表与导入语句见 `artifacts/db/ingest-*.sql.txt`。

## 5. 操作步骤

```bash
cd minimax
bash scripts/setup_wsl.sh                 # 准备环境
bash scripts/start_server.sh              # 启动 PowerContext Server（状态后端：内嵌 seekDB）
export MINIMAX_API_KEY=<你的 key>         # MiniMax 凭据

sddctx ingest --root sample-project       # 5.1 导入资料
sddctx context --target-spec SPEC-004     # 5.2 检索上下文
sddctx verify --phase before --target-spec SPEC-004   # 5.3 修改前基线
sddctx apply  --target-spec SPEC-004      # 5.4 MiniMax 完成代码修改
sddctx verify --phase after  --target-spec SPEC-004   # 5.5 修改后验证
sddctx state show                         # 5.6 查看已保存的任务状态
```

`scripts/run_verification.sh` 把以上步骤串成一次执行。

## 6. 预期结果

### 6.1 上下文输出（`sddctx context`）

| 检查点 | 预期 |
| --- | --- |
| 适用的规格条目 | 7 条全部命中；`SPEC-004` 排在最前 |
| 本次要实现 / 必须保持的标记 | `target_specs = ["SPEC-004"]`，其余 6 条标为"必须保持的原有行为" |
| 要修改的代码 | `src/orders/filter.py::filter_orders`（附行号区间与关联规格） |
| 验证测试 | `tests/test_filter_tier.py` 标记 `role=target`；`tests/test_filter_regression.py` 标记 `role=regression` |
| 来源真实 | 每条都带 `source_path` 与 `source_hash` |

### 6.2 修改前（`sddctx verify --phase before`）

| 测试集 | 命令 | 预期退出码 | 原因 |
| --- | --- | --- | --- |
| 目标测试 | `python -m pytest -q tests/test_filter_tier.py` | **1** | `filter_orders` 尚未支持 `tier` 参数，8 个用例报 `TypeError`——属于功能未实现，不是环境错误 |
| 全量测试 | `python -m pytest -q tests/test_filter_regression.py tests/test_filter_tier.py` | **1** | 8 failed, 13 passed |

日志：`artifacts/logs/pytest-before-*.log`（含命令、cwd、退出码、代码版本、完整输出）。

### 6.3 代码修改（`sddctx apply`）

| 检查点 | 预期 |
| --- | --- |
| MiniMax 调用 | 真实请求，模型 `MiniMax-M3.1-Flash`，请求与响应留档于 `artifacts/minimax/` |
| 生成的补丁 | unified diff，落在 `artifacts/diff/minimax-*.patch` |
| 实际变更 | `src/orders/filter.py`：新增 `tier` 参数、`VALID_TIERS` 常量、等级校验与过滤分支，并更新 docstring 中的规格引用 |
| 未被修改 | `tests/` 下所有文件保持不变（回归测试集与目标测试集都不改） |

### 6.4 修改后（`sddctx verify --phase after`）

| 测试集 | 命令 | 预期退出码 | 结果 |
| --- | --- | --- | --- |
| 目标测试 | `python -m pytest -q tests/test_filter_tier.py` | **0** | 8 passed |
| 全量测试 | `python -m pytest -q tests/test_filter_regression.py tests/test_filter_tier.py` | **0** | 21 passed |

日志：`artifacts/logs/pytest-after-*.log`。

### 6.5 状态复用

| 步骤 | 预期 |
| --- | --- |
| `sddctx state show` | 返回该 Scope 下的任务状态：阶段、已完成、待办、目标测试与全量测试的退出码与日志路径 |
| 重启 Server 后再读 | `bash scripts/stop_server.sh && bash scripts/start_server.sh && sddctx state show` 仍能读到同样的状态（状态存于内嵌 seekDB，未随进程消失） |
| 状态参与后续处理 | 下一次 `sddctx apply` 会把上次状态拼进提示词，助手在已有进展上继续 |

### 6.6 规格更新后重新导入

修改 `sample-project/specs/filtering.md`（例如把 SPEC-004 的允许等级集合改为 `VIP / GOLD / SILVER / PLATINUM`），然后：

```bash
sddctx ingest --root sample-project
sddctx context --target-spec SPEC-004
```

`sddctx context` 输出的 SPEC-004 正文与 `source_hash` 必须随之改变，检索结果不得停留在旧内容上——`ingest` 的返回里 `changed_files` 会列出发生变化的规格条目。

## 7. 重置方法

```bash
bash scripts/reset_data.sh
```

该脚本会：删除本地 seekDB 索引 → 清空 PowerContext 任务状态 → 把 `sample-project` 恢复到版本库基线提交。随后重新执行 `bash scripts/init_data.sh` 即可回到初始状态。

## 8. 在干净环境复核提交中的两个快照

版本库中的两个提交分别对应"修改前"与"修改后"：

```bash
bash scripts/verify_before_after.sh <修改前提交> <修改后提交>
```

脚本会把两个提交里的 `sample-project` 快照解包到临时目录，分别运行目标测试与全量测试并打印退出码，预期是：修改前目标测试失败、修改后全部通过。
