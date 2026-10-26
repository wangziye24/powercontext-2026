# 运行说明

本文档说明如何在干净环境上复现本作品：准备依赖、初始化数据、启动模块、执行验证样例，以及每个命令的输入与输出。

## 1. 环境要求

| 项目 | 版本 | 说明 |
| --- | --- | --- |
| 操作系统 | Linux 或 WSL Ubuntu（22.04 / 24.04 实测） | 内嵌 seekDB 不支持 Windows |
| Python | 3.12（3.11 ~ 3.13 均可） | 由 `uv` 自动准备 |
| uv | 最新版 | 用于创建虚拟环境与安装依赖 |
| MiniMax API | `MiniMax-M3.1-Flash`（`https://api.minimaxi.com/v1`） | 需要 `MINIMAX_API_KEY` |
| 磁盘 | 约 200 MB | seekDB 数据目录与虚拟环境 |

依赖由 `minimax/pyproject.toml` 固定，钉版清单另见 `minimax/requirements.txt`：

```
powercontext[cli,server,seekdb]==1.4.0
sqlalchemy>=2.0
httpx>=0.27
pyyaml>=6.0
```

若评审环境无法访问 PyPI 镜像，可用官方仓库的同版本源码替代安装：

```bash
git clone --depth 1 --branch 2026-competition https://github.com/oceanbase/powercontext.git /tmp/powercontext
pip install -e "/tmp/powercontext[cli,server,seekdb]"
pip install -e .   # 本作品
```

> 注意：`pylibseekdb` 的运行库本身来自 PyPI（manylinux wheel，约 54 MB），离线环境需要事先缓存该 wheel；
> 完全离线时请改用 OceanBase 实例，并把 `POWERCONTEXT_SERVER_DATABASE_KIND` 与上下文模块的存储后端一并切换。

## 2. 配置步骤

### 2.1 MiniMax 凭据

```bash
export MINIMAX_API_KEY=<你的 MiniMax API Key>
export MINIMAX_BASE_URL=https://api.minimaxi.com/v1   # 可选，默认值相同
export MINIMAX_MODEL=MiniMax-M3.1-Flash             # 可选，默认值相同
```

密钥只通过环境变量传入，仓库内不保存任何凭据。`config/powercontext.env.template` 里只有占位值。

### 2.2 工作目录

模块把工作数据放在 `$SDDCTX_HOME`（默认 `~/.sddctx`），把可提交的证据写进 `minimax/artifacts/`：

| 路径 | 内容 |
| --- | --- |
| `~/.sddctx/seekdb` | 工程资料索引（seekDB 数据目录） |
| `~/.sddctx/powercontext/seekdb` | PowerContext 任务状态（seekDB 数据目录） |
| `~/.sddctx/powercontext.env` | 由模板生成的 Server 配置 |
| `minimax/artifacts/` | SQL 记录、MiniMax 请求响应、测试日志、代码差异、状态快照 |

## 3. 一键流程

```bash
cd minimax

# 3.1 准备环境
bash scripts/setup_wsl.sh

# 3.2 启动 PowerContext Server（任务状态的持久化后端是内嵌 seekDB）
bash scripts/start_server.sh

# 3.3 初始化数据：把规格、代码、测试导入 seekDB
bash scripts/init_data.sh

# 3.4 查看本次任务选中的上下文
sddctx context --target-spec SPEC-004

# 3.5 修改前基线：目标测试必须失败，回归测试必须通过
sddctx verify --phase before --target-spec SPEC-004

# 3.6 用 MiniMax 完成代码修改（真实调用，保存请求与响应）
sddctx apply --target-spec SPEC-004

# 3.7 修改后验证：目标测试与回归测试都必须通过
sddctx verify --phase after --target-spec SPEC-004

# 3.8 状态复用：重启 Server 后仍能读出任务状态
bash scripts/stop_server.sh
bash scripts/start_server.sh
sddctx state show

# 3.9 重置数据
bash scripts/reset_data.sh
```

## 4. 调用入口

入口是命令行工具 `sddctx`（安装后位于虚拟环境 `bin/`）。

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `sddctx ingest --root <工程目录>` | 规格、代码、测试所在目录 | 导入统计（文档数、规格条目数、代码符号数、测试数）与 SQL 记录文件 |
| `sddctx context --task "<任务描述>" --target-spec <规格编号>` | 任务描述；本次要实现的规格编号（可重复） | 任务上下文 JSON：适用的规格条目、要改的代码符号与文件、目标测试与回归测试、工程约定 |
| `sddctx apply --task "<任务描述>" --target-spec <规格编号>` | 同上 | MiniMax 调用记录、生成的 unified diff、实际应用的文件变更 |
| `sddctx verify --phase <before\|after> --target-spec <规格编号>` | 同上 | 目标测试与全量测试的命令、退出码、日志路径，并写入任务状态 |
| `sddctx state show` | 无 | PowerContext 中保存的全部任务状态 |
| `sddctx state save --task ... --done ... --todo ...` | 任务描述 | 新增一条任务状态 |
| `sddctx reset` | 无 | 删除本地 seekDB 索引 |

`--task` 省略时使用内置的验证样例任务。

## 5. 上下文输出结构

`sddctx context` 的输出同时写到 `artifacts/context/context.json`，结构如下：

```jsonc
{
  "task": "在订单筛选中增加客户等级过滤……",
  "target_specs": ["SPEC-004"],              // 本次要实现的规格
  "mentioned_specs": ["SPEC-001", "……"],      // 任务文本里点名的规格（范围会展开）
  "specifications": [                         // 适用的规格条目
    {
      "spec_id": "SPEC-004",
      "title": "客户等级过滤",
      "body": "……",
      "source_path": "specs/filtering.md",     // 来源真实存在
      "source_hash": "……",
      "score": 8
    }
  ],
  "code": [                                   // 需要修改的代码文件与符号
    {
      "path": "src/orders/filter.py",
      "symbol": "filter_orders",
      "kind": "function",
      "line_start": 24,
      "line_end": 62,
      "covers_specs": ["SPEC-001", "……"],
      "source_hash": "……"
    }
  ],
  "tests": [                                  // 目标功能测试与必须保持的回归测试
    {
      "path": "tests/test_filter_tier.py",
      "name": "test_spec_004_filters_by_tier",
      "covers": ["SPEC-004"],
      "role": "target"
    }
  ],
  "conventions": [                            // 该包已有的错误类型
    { "symbol": "InvalidStatusError", "path": "src/orders/errors.py" }
  ],
  "related_files": [                          // 相关文件的当前完整内容
    { "path": "src/orders/filter.py", "selected": true }
  ]
}
```

## 6. 测试命令

验证样例使用的命令与退出码记录在 `artifacts/logs/pytest-*.log`：

```bash
cd minimax/sample-project
python -m pytest -q tests/test_filter_tier.py      # 目标功能测试
python -m pytest -q tests/test_filter_regression.py tests/test_filter_tier.py   # 全量
```

`scripts/verify_before_after.sh` 会用版本库中的两个提交分别还原“修改前”和“修改后”的快照并执行上述命令，用于在干净环境复核。

## 7. 已知限制

1. **内嵌 seekDB 不支持 Windows**：请在 Linux 或 WSL Ubuntu 上运行。Windows 上只能使用 PowerContext 的 SQLite 后端，此时“数据库实际参与”一条需要改用 OceanBase 实例。
2. **上下文检索是词法级**的：任务描述与规格使用同一套术语时命中最好；跨语言表述（例如英文任务描述匹配中文规格）只能靠规格编号命中。
3. **MiniMax M3.1-Flash 强制自适应思考**，思考会消耗输出预算；`sddctx apply --max-tokens` 默认 32768，若任务上下文很大可继续调高。
4. **补丁应用是严格校验**：上下文行必须与文件内容逐字一致，否则补丁被拒绝（`PatchError`），需要重新生成补丁。
5. **单任务单 Scope**：验证样例使用 PowerContext 的默认 Scope；多个任务并行时建议为每个任务设置独立的 Scope 绑定。
