# 运行说明

本文档说明如何在干净环境上复现本作品：准备依赖、初始化数据、启动模块、执行验证样例，以及每个命令的输入与输出。

## 1. 环境要求

| 项目 | 版本 | 说明 |
| --- | --- | --- |
| 操作系统 | Linux 或 WSL Ubuntu（22.04 / 24.04 实测） | 内嵌 seekDB 不支持 Windows；**数据目录也不能放在 WSL 的 `/mnt/c`、`/mnt/d` 上**（见 §7.1） |
| Python | 3.12（3.11 ~ 3.14 均可） | 由 `uv` 自动准备 |
| uv | 最新版 | 用于创建虚拟环境与安装依赖 |
| MiniMax API | `MiniMax-M3.1-Flash`（`https://api.minimaxi.com/v1`） | 可选：没有 `MINIMAX_API_KEY` 时用 `sddctx apply --replay` 回放已记录的模型产出 |
| 磁盘 | 约 200 MB | seekDB 数据目录与虚拟环境 |

依赖由 `minimax/pyproject.toml` 固定，钉版清单另见 `minimax/requirements.txt`：

```
powercontext[cli,server,seekdb]==1.4.0
sqlalchemy>=2.0
httpx>=0.27
pyyaml>=6.0
pytest>=8.0
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

## 3. 一键流程（推荐）

```bash
cd minimax
bash scripts/setup_wsl.sh     # 3.1 建虚拟环境、装依赖、seekdb 自检（$HOME 下的数据目录）
bash scripts/reproduce.sh     # 3.2 逐项复现 6 项必做验收并打印 PASS/FAIL
```

`reproduce.sh` 做的事（全部在临时目录里跑，不会弄脏仓库）：

1. 用全新的 `$SDDCTX_HOME`（默认 `$HOME/.sddctx-reproduce`）启动 PowerContext Server；
2. 把 `snapshots/before/` 复制成临时工作工程；
3. `sddctx ingest` → 资料写入 seekdb；
4. `sddctx context --target-spec SPEC-004` → 校验 7 条规格 / 待改函数 / 目标与回归测试都在上下文里；
5. `sddctx verify --phase before` → 目标测试必须因 `TypeError` 失败（不是环境错误）；
6. `sddctx apply`：有 `MINIMAX_API_KEY` 走真实调用，没有则 `--replay` 回放已记录的模型产出；
7. `sddctx verify --phase after` → 21 passed；
8. 与仓库里的 `sample-project/` 逐字节比较 → 必须一致（模型产出 = 提交差异）；
9. 重启 Server 后 `sddctx state show` → 状态仍可读回。

### 3.3 手工逐步（等价，便于理解每一步）

```bash
cd minimax
bash scripts/start_server.sh                  # 启动 PowerContext Server（状态后端：内嵌 seekDB）
WORK=$(mktemp -d) && cp -r snapshots/before "$WORK/project"

sddctx ingest  --root "$WORK/project"
sddctx context --root "$WORK/project" --target-spec SPEC-004
sddctx verify  --root "$WORK/project" --phase before --target-spec SPEC-004
sddctx apply   --root "$WORK/project" --target-spec SPEC-004 --replay    # 无密钥；有密钥去掉 --replay
sddctx verify  --root "$WORK/project" --phase after  --target-spec SPEC-004
sddctx state show

bash scripts/stop_server.sh && bash scripts/start_server.sh && sddctx state show
```

> 想直接观察"改前失败 / 改后通过"的对照，可运行 `bash scripts/verify_before_after.sh`
> （默认对比 `snapshots/before` 与 `sample-project`，不需要 git 历史）。

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

`scripts/verify_before_after.sh` 直接对比随仓库交付的两个快照（`snapshots/before` 与 `sample-project`），
分别执行上述命令并打印退出码，**不依赖 git 历史**；`scripts/verify_patch_equivalence.sh` 则把
`artifacts/diff/minimax-*.patch` 应用到修改前快照，再与 `sample-project` 逐字节比较，预期输出 `IDENTICAL`。

## 7. 已知限制

1. **内嵌 seekDB 需要 Linux 原生文件系统**：不支持 Windows；在 WSL 里也**不能**把数据目录放在
   `/mnt/c`、`/mnt/d` 这类 Windows 挂载盘上——seekdb 建库要 `fallocate`，DrvFs/v9fs 会返回
   `Operation not supported`，表现为 `SeekdbError: seekdb_open failed: code=-1`（底层 `errcode=-4009`）。
   默认的 `$HOME/.sddctx` 在 ext4 上，按本文档执行不会遇到；只有显式把 `SDDCTX_HOME` 指到 `/mnt/*` 才会。
2. **无 MiniMax 密钥时的回放**：`sddctx apply --replay` 用的是开发过程中记录的模型产出，
   不会发起新请求，并在 `artifacts/minimax/minimax-replay-*.json` 里标注 `"mode": "replay"`。
   需要"真实调用"的复核请设置 `MINIMAX_API_KEY` 并去掉 `--replay`。
3. **上下文检索是词法级**的：任务描述与规格使用同一套术语时命中最好；跨语言表述（例如英文任务描述匹配中文规格）只能靠规格编号命中。
4. **MiniMax M3.1-Flash 强制自适应思考**，思考会消耗输出预算；`sddctx apply --max-tokens` 默认 32768，若任务上下文很大可继续调高。
5. **补丁应用是严格校验**：上下文行必须与文件内容逐字一致，否则补丁被拒绝（`PatchError`），需要重新生成补丁。
6. **单任务单 Scope**：验证样例使用 PowerContext 的默认 Scope；多个任务并行时建议为每个任务设置独立的 Scope 绑定。
7. **任务 key 用内容哈希**：`_task_key()` 取任务文本 SHA-1 的前 12 位，跨进程稳定；
   早期版本用 Python 内置 `hash()`（每进程加盐）会导致 `apply` 写入的状态在 `verify`/`state show` 里读不到，已修正。
