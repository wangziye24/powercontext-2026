# 规有所依 —— 面向软件持续演进的 SDD 上下文管理系统

> MiniMax × OceanBase 大赛作品。针对一个开发任务，模块从 **seekdb** 中找出适用的规格条目、需要修改的代码符号与用于验证的测试，交给 **MiniMax** 完成代码修改，并把任务进展保存在 **PowerContext** 中，供下一次运行读取复用。

## 0. 一条命令复现 6 项必做验收（评审入口）

```bash
bash scripts/setup_wsl.sh        # 一次性：建虚拟环境、装依赖、自检 seekdb
bash scripts/reproduce.sh        # 复现「改前失败 → MiniMax 改码 → 改后全过 → 状态复用」，逐项打印 PASS/FAIL
```

`scripts/reproduce.sh`：

* **不需要 MINIMAX_API_KEY**：改码步骤用 `sddctx apply --replay` 回放开发过程中记录下来的 MiniMax 产出
  （原始请求与响应在 `artifacts/minimax/`）；若环境里存在密钥，则走真实调用。
* **不依赖 git 历史**：修改前快照随仓库交付在 `snapshots/before/`，修改后快照就是 `sample-project/`。
* 数据目录默认 `$HOME/.sddctx-reproduce`（内嵌 seekdb 需要 `fallocate`，**不能放在 `/mnt/c`、`/mnt/d` 这类 WSL 挂载盘上**）。

## 1. 三项技术的分工

| 技术 | 承担的作用 | 落地位置 |
| --- | --- | --- |
| **seekdb** | 存储工程资料（规格条目、代码符号、测试）与检索索引，检索结果直接用于构造开发上下文 | `src/sddctx/schema.py`、`src/sddctx/db.py`、`src/sddctx/ingest.py`、`src/sddctx/retrieve.py` |
| **MiniMax** | 依据检索出的上下文完成本次任务的真实代码修改，并留下完整请求/响应记录 | `src/sddctx/minimax_client.py`、`src/sddctx/patching.py` |
| **PowerContext** | 保存任务进展（Scope、已完成/待办、验证结果），模块重启后读取并继续 | `src/sddctx/taskstate.py` |

## 2. 赛题必做项 ↔ 实现 ↔ 证据

| 必做项（题面 4.1） | 本作品的实现 | 证据 |
| --- | --- | --- |
| 能够运行 | `scripts/setup_wsl.sh` + `scripts/reproduce.sh`；资料、测试、快照全部随仓库交付，不依赖开发机上的残留状态 | `reproduce.sh` 的 PASS/FAIL 汇总；`artifacts/logs/` |
| MiniMax 实际参与 | 改码由 `MiniMaxClient`（`MiniMax-M3.1-Flash`，OpenAI 兼容 HTTP API）产出 unified diff，模块解析并应用 | `artifacts/minimax/minimax-call-20261026T071003.json`（请求/响应/token 用量）、`artifacts/minimax/minimax-prompt-*.txt`、`artifacts/diff/minimax-*.patch` |
| 上下文正确 | 按任务检索 7 条规格、待改代码符号、目标测试与回归测试；规格更新后重新导入即使用新正文 | `artifacts/context/context.json`、`artifacts/spec-change/`（改前/改后正文与哈希对比） |
| 数据库实际参与 | 规格/代码/测试写入内嵌 seekdb，上下文召回全部在 SQL 中完成 | `artifacts/db/ingest-*.sql.txt`、`artifacts/db/context-*.sql.txt` |
| 状态能够复用 | 任务阶段、已完成、待办、验证结果写入 PowerContext（内嵌 seekdb）；重启 Server 后读回 | `artifacts/state/verify-*.json`、`sddctx state show` |
| 代码修改有效 | 同一组目标测试改前因 `TypeError: unexpected keyword argument 'tier'` 失败，改后 8 passed；回归 13 passed 全程通过 | `artifacts/logs/pytest-before-*.log`、`artifacts/logs/pytest-after-*.log`、`artifacts/diff/` |

## 3. 目录结构

```
minimax/
├── src/sddctx/        # 上下文模块（CLI 入口 sddctx）
├── sample-project/    # 验证用业务工程（修改后快照：规格 + 代码 + pytest 用例）
├── snapshots/before/  # 同一个业务工程的修改前快照（目标功能尚未实现）
├── scripts/           # 环境准备、一键复现、改前/改后对比、补丁等价性、规格变更演示、重置
├── docs/              # 运行说明、验证样例、技术说明、MiniMax 使用记录
└── artifacts/         # 运行证据：SQL 记录、MiniMax 请求响应、测试日志、代码差异、状态快照
```

> 两个快照的区别只有 `src/orders/filter.py`：`snapshots/before` 没有 `tier` 参数与等级过滤分支，
> 因此 `tests/test_filter_tier.py` 的 8 个目标用例在改码前必然失败；`sample-project` 是模型改完后的最终代码。

## 4. 命令行入口

```bash
sddctx ingest  --root snapshots/before            # 导入工程资料到 seekdb
sddctx context --target-spec SPEC-004             # 输出任务上下文（规格/代码/测试来源）
sddctx apply   --target-spec SPEC-004 --replay    # 回放已记录的 MiniMax 产出（无密钥可用）
sddctx apply   --target-spec SPEC-004             # 真实调用 MiniMax 改码（需要 MINIMAX_API_KEY）
sddctx verify  --phase before --target-spec SPEC-004   # 改前基线：目标测试必须失败
sddctx verify  --phase after  --target-spec SPEC-004   # 改后验证：目标测试与回归测试全过
sddctx state show                                 # 查看 PowerContext 中保存的任务状态
```

详细说明见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)，验证样例见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)，
模块结构与技术说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，MiniMax 使用记录见
[`docs/MINIMAX-USAGE.md`](docs/MINIMAX-USAGE.md)。
