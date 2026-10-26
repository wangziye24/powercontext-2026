# MiniMax 开发记录（技术使用记录）

本文件记录本次任务的代码修改是如何由 MiniMax 完成的，以及提交的代码差异与模型产出的对应关系。原始请求与响应保存在 `artifacts/minimax/`，可逐条复核。

## 1. 接入方式

| 项目 | 内容 |
| --- | --- |
| 接入方式 | HTTP API（OpenAI 兼容协议），由 `sddctx/minimax_client.py` 调用 |
| 端点 | `https://api.minimaxi.com/v1/chat/completions` |
| 模型 | `MiniMax-M3.1-Flash`（`reasoning_split=true`，思考内容与正文分离） |
| 凭据 | 环境变量 `MINIMAX_API_KEY`，不写入仓库 |
| 记录文件 | `artifacts/minimax/minimax-call-20261026T071003.json`（请求、响应、token 用量）<br>`artifacts/minimax/minimax-prompt-20261026T071003.txt`（本次发送的完整提示） |

## 2. 本次调用

| 项目 | 数值 |
| --- | --- |
| 调用时间 | 2026-10-26T15:08:20+08:00 |
| 耗时 | 102.9 秒 |
| 输入 token | 4283（其中缓存命中 187） |
| 输出 token | 9150（其中思考 token 8560，正文 590） |
| 提示规模 | system 215 字符 + user 13777 字符 |
| 回答规模 | 2352 字符（一个 unified diff） |
| 结束原因 | `stop` |

提示内容的构成（可在 `minimax-prompt-*.txt` 中逐段核对）：

1. **开发任务**：增加客户等级过滤，同时保持 SPEC-001 ~ SPEC-003、SPEC-005 ~ SPEC-007 的原有行为；
2. **适用的规格条目**：7 条，SPEC-004 标注为「本次要实现」，其余标注为「必须保持的原有行为」，每条带来源路径；
3. **需要修改的代码符号**：`src/orders/filter.py::filter_orders`（含行号区间与关联规格编号）；
4. **相关文件的当前完整内容**：`src/orders/filter.py`、`src/orders/errors.py`、`src/orders/models.py`、`src/orders/__init__.py`；
5. **该包已有的错误类型**：`OrderFilterError`、`InvalidStatusError`、`InvalidRangeError`（供模型沿用命名与继承关系）；
6. **用于验证的测试**：8 个目标功能测试与 13 个回归测试的完整代码（标注 `target` / `regression`）；
7. **输出要求**：unified diff 格式、路径前缀、禁止修改测试、上下文行必须逐字一致等 9 条约束。

## 3. 模型产出与代码差异的对应

模型返回的补丁保存在 `artifacts/diff/minimax-20261026T071003.patch`，模块应用后实际产生的差异保存在 `artifacts/diff/after-20261026T071003.patch`（即工作区相对提交基线的 `git diff`）。

| 模型产出的改动 | 位置 | 对应规格 |
| --- | --- | --- |
| 新增 `VALID_TIERS = ("VIP", "GOLD", "SILVER")` | `src/orders/filter.py` | SPEC-004 |
| 新增 `tier` 关键字参数 | `src/orders/filter.py::filter_orders` | SPEC-004 |
| 新增等级校验，非法等级抛出 `InvalidTierError` | `src/orders/filter.py::filter_orders` | SPEC-004 |
| 新增等级过滤分支（大小写不敏感；`NONE` 匹配无等级订单） | `src/orders/filter.py::filter_orders` | SPEC-004 |
| 更新 docstring 的规格引用，补上 SPEC-004 | `src/orders/filter.py::filter_orders` | 文档一致性 |
| 导入 `InvalidTierError` | `src/orders/filter.py` | SPEC-004 |

未被模型改动、也未被本作品改动的部分：

- `tests/` 下的全部测试文件（目标测试与回归测试都没有被修改或删除断言）；
- SPEC-001 / 002 / 003 / 005 / 006 / 007 描述的既有行为；
- `src/orders/errors.py` 中既有的错误类型。

提交中的代码差异与模型产出的补丁一致，可以直接比对：

```bash
git diff <修改前提交> <修改后提交> -- minimax/sample-project
diff <(git diff <修改前提交> <修改后提交> -- minimax/sample-project) \
     minimax/artifacts/diff/after-20261026T071003.patch
```

## 4. 迭代过程

本次任务共进行了三次 MiniMax 调用，前两次暴露的问题都促成了模块的改进，最终一次调用与提交的代码完全对应。

| 轮次 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| 第 1 次（15:01） | 回答为空，`finish_reason=length` | `max_tokens=8192` 全部被模型的强制思考消耗，模型尚未输出正文 | 调用预算提高到 32768，并开启 `reasoning_split` 把思考与正文分离 |
| 第 2 次（15:03） | 补丁解析成功但无法应用 | 模型按"无 license 头的文件"计算 hunk 行号；且提示里只给了函数签名，模型无法看到文件全文 | 上下文改为提供文件完整内容；补丁应用改为以上下文匹配定位（`git apply --recount`），并对生成的 Python 做语法校验、失败即回滚 |
| 第 3 次（15:08） | 应用成功，21 个测试全部通过 | —— | 作为最终记录提交 |

这三次调用都属于本次任务的开发过程，前两次的失败原因与处理方式记录在此，便于核对上下文模块与补丁流程的改进轨迹。

## 5. 上下文模块中的 MiniMax 参与点

| 环节 | 代码位置 | MiniMax 的作用 |
| --- | --- | --- |
| 构造提示 | `src/sddctx/context.py::render_prompt` | 把检索到的规格、代码、测试、工程约定组织成提示 |
| 发起调用 | `src/sddctx/minimax_client.py::MiniMaxClient.chat` | 真实 HTTP 请求，记录模型、参数、token 用量 |
| 解析与应用 | `src/sddctx/patching.py::extract_diff` / `apply_unified_diff` | 提取 unified diff、校验后应用到工程 |
| 留档 | `src/sddctx/minimax_client.py::save_call` | 保存请求、响应与用量，作为开发记录 |
