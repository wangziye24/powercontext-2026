# 午夜便利店 · 阿夜的账本

> Dify × OceanBase 大赛作品（Just for Fun · 用 Dify 做一个好玩的智能应用）
> **Dify** 编排 · **seekdb** 设定库与玩家账本（混合检索）· **PowerContext** 跨会话记忆

凌晨 1:47 的街角便利店，收银员**阿夜**记得你所有的小动作——你换掉的那包薄荷糖、你说过的胡话、你欠的账。

- 📦 交付物：Dify Chatflow **DSL**（导入即用）+ 侧车服务源码 + 种子数据 + 运行说明 + 验证样例
- 🎮 入口：Dify WebApp（无需自研前端）
- 📚 文档：[设计](docs/00-项目设计.md) · [运行说明](docs/01-运行说明.md) · [验证样例](docs/02-验证样例.md) · [技术结合证据](docs/03-技术结合证据.md) · [开发记录](docs/04-开发过程记录.md)

---

## 玩法速览

| 钩子 | 玩家怎么玩 | 技术怎么参与 |
|---|---|---|
| **翻旧账** | 新会话里随便说句话，阿夜会主动提起你上次的恶作剧/口味 | PowerContext 跨会话记忆召回 → 直接进台词 |
| **整蛊任务** | "把薄荷糖标签换成『后悔药』" | seekdb `prank_events` 结构化过滤（好感度区间 + 触发条件） |
| **记忆盲盒** | "你还记得我上次干了什么吗？" → 她可能把**城市传闻**当成你的事；你一句纠正，她当场认错并写回记忆 | seekdb 传闻库 + PowerContext 记忆**交叉生成**，记忆可被修正 |
| **结局** | 好感度与整蛊数推进章节：夜来客 → 常客 → 熟客 → **合伙人**；翻车太多直接进黑名单 | seekdb `endings` 表按 `cond_min/cond_max/cond_pranks` 结构化过滤 |

每一轮结束，阿夜都会给你一张**今日账本**（好感度条、欠账、整蛊数、章节）。

---

## 一次对话里发生了什么（Dify 编排的 14 个节点）

```
开始 → 意图判定(LLM) → 解析意图(Code)
     → 设定检索(HTTP→seekdb 混合检索：向量+全文+结构化过滤)
     → 记忆召回(HTTP→PowerContext 语义检索)
     → 读取账本(HTTP→seekdb)
     → 汇总上下文(Code)
     → 阿夜台词(LLM：设定+记忆+账本 → JSON)
     → 状态机(Code：好感度/欠账/章节/结局判定)
     → 结局检索(HTTP→seekdb 结构化过滤) → 提取结局(Code)
     → 写入账本(HTTP→seekdb player_state + event_log)
     → 写入记忆(HTTP→PowerContext /memories)
     → 回答(台词 + 今日账本 Markdown)
```

完整节点说明见 [设计文档](docs/00-项目设计.md#21-dify-chatflow-节点清单dsl-逐节点设计)。

---

## 快速开始

```bash
# 一键起全栈（seekdb + 侧车 + PowerContext + Dify）+ 自检
bash dify/scripts/start_all.sh
```

PowerContext 需要用仓库源码启动（见 [运行说明 §4.2](docs/01-运行说明.md)），其余都在脚本里。
手工步骤见 [运行说明 §4](docs/01-运行说明.md#4-部署步骤)。

```bash
# 灌种子数据（35 条设定 + 预置记忆）
cd dify/backend && python3 -m app.seed
python3 dify/scripts/init_powercontext.py --memory

# 导入 DSL → 发布 → 跑一遍完整玩法演示
export DIFY_CONSOLE=http://127.0.0.1:5001 DIFY_EMAIL=you@example.com DIFY_PASSWORD='YourPass2026'
python3 dify/scripts/dify_ops.py import
python3 dify/scripts/dify_ops.py publish --app-id <app_id>
python3 dify/scripts/demo_run.py --app-id <app_id> --out dify/docs/evidence/demo-transcript.md
```

## 验证状态（本机实测）

| 项目 | 结果 |
|---|---|
| DSL 导入 Dify 1.18.0 | ✅ `status: completed`（无告警） |
| 14 个节点执行 | ✅ 14/14 `succeeded`（见 `docs/evidence/node-trace.txt`） |
| 混合检索 | ✅ `mode=hybrid_rrf`，"关东煮还有吗" → 命中「关东煮拼盘」（semantic 0.9765 / keyword 3.4284） |
| 跨会话记忆 | ✅ 新会话里阿夜主动提起上一轮的恶作剧 |
| 记忆校准 | ✅ 玩家纠正后当场认错并写回记忆 |
| 一键自检 | ✅ `smoke_test.sh` 6/6 通过 |

---

## 目录

```
dify/
├── 提交说明.md                    # ★ 对照平台表单逐项填写
├── dsl/aye-ledger-chatflow.yml   # ★ Dify DSL（Dify 官方导出，导入即用）
├── dsl/aye-ledger-chatflow.source.yml  # 脚本生成的等价版本
├── backend/                      # 侧车（标准库 + vendor PyMySQL，零 pip 依赖）
├── data/                         # schema.sql + 35 条设定
├── deploy/                       # Dify 1.18 compose / seekdb compose
├── scripts/
│   ├── build_dsl.py              # DSL 生成器
│   ├── validate_dsl.py           # DSL 自检（引用/语法/JSON 合法性）
│   ├── seed.py                   # 灌数据 + 预置记忆
│   ├── init_powercontext.py      # PowerContext 存储准备（建库/清表）
│   ├── dify_ops.py               # Dify 控制台 API（导入/导出/发布/对话/插件/凭证）
│   ├── demo_run.py               # 完整玩法演示（生成对话记录）
│   ├── dump_trace.sh             # 导出节点执行记录
│   ├── start_all.sh              # 一键起全栈
│   └── smoke_test.sh             # 一键自检
└── docs/                         # 设计 / 运行说明 / 验证样例 / 证据 / 开发记录
    └── evidence/                 # 真实对话记录 + 节点执行记录
```

## 许可与来源说明

- `backend/vendor/pymysql/`：PyMySQL（MIT），随仓库分发以实现"零 pip 依赖"启动。
- `dsl/aye-ledger-chatflow.yml` 由 `scripts/build_dsl.py` 生成，可在本地一键重建。
- 设定（35 条）、角色与剧情均为本作品原创；PowerContext、Dify、seekdb 均为各自官方开源项目。
