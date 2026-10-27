# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

> MiniMax × OceanBase 大赛作品。针对一个开发任务，模块从 **seekdb** 中找出适用的规格条目、需要修改的代码符号与用于验证的测试，交给 **MiniMax** 完成代码修改，并把任务进展保存在 **PowerContext** 中，供下一次运行读取复用。

## 1. 三项技术的分工

| 技术 | 承担的作用 | 落地位置 |
| --- | --- | --- |
| **seekdb** | 存储工程资料（规格条目、代码符号、测试）与检索索引，检索结果直接用于构造开发上下文 | `src/sddctx/schema.py`、`src/sddctx/db.py`、`src/sddctx/ingest.py`、`src/sddctx/retrieve.py` |
| **MiniMax** | 依据检索出的上下文完成本次任务的真实代码修改，并留下完整请求/响应记录 | `src/sddctx/minimax_client.py`、`src/sddctx/patching.py` |
| **PowerContext** | 保存任务进展（Scope、已完成/待办、验证结果），模块重启后读取并继续 | `src/sddctx/taskstate.py` |

## 2. 快速开始

```bash
# 1) 准备环境（Linux / WSL Ubuntu，Python 3.12）
bash scripts/setup_wsl.sh

# 2) 初始化数据：把规格、代码、测试导入 seekdb
bash scripts/init_data.sh

# 3) 端到端验证：检索上下文 → MiniMax 改代码 → 跑测试 → 保存状态
bash scripts/run_verification.sh

# 4) 重置数据
bash scripts/reset_data.sh
```

详细说明见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)，验证样例见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)，
模块结构与技术说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，MiniMax 使用记录见
[`docs/MINIMAX-USAGE.md`](docs/MINIMAX-USAGE.md)。

## 3. 目录结构

```
minimax/
├── src/sddctx/        # 上下文模块（CLI 入口 sddctx）
├── sample-project/    # 验证用业务工程（规格 + 代码 + pytest 回归集）
├── scripts/           # 环境准备、数据初始化、端到端验证、重置
├── docs/              # 运行说明、验证样例、技术说明、MiniMax 使用记录
└── artifacts/         # 运行产生的证据：SQL 记录、MiniMax 请求响应、测试日志、代码差异
```

## 4. 命令行入口

```bash
sddctx ingest --root sample-project        # 导入工程资料到 seekdb
sddctx context --task "<开发任务描述>"      # 输出任务上下文（规格/代码/测试来源）
sddctx apply --task "<开发任务描述>"        # 用 MiniMax 完成代码修改
sddctx state show                          # 查看 PowerContext 中保存的任务状态
sddctx verify --task "<开发任务描述>"       # 跑目标测试与回归测试并记录结果
```
