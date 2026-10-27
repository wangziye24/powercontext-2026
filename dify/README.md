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

## 快速开始（3 条命令）

```bash
# 1) seekdb（设定库 + 玩家账本 + PowerContext 记忆表同库）
cd dify/deploy && docker compose -f seekdb.yml up -d seekdb

# 2) 侧车服务（给 Dify 提供 seekdb 访问）
cd ../backend && python3 -m app.main          # 需要先 cp .env.example .env

# 3) Dify 1.18
cd ../deploy/dify && cp .env.template .env && docker compose up -d
```

PowerContext 启动与数据初始化见 [运行说明 §4](docs/01-运行说明.md#4-部署步骤)。

---

## 目录

```
dify/
├── dsl/aye-ledger-chatflow.yml   # ★ Dify DSL（提交物）
├── backend/                      # 侧车（标准库 + vendor PyMySQL，零 pip 依赖）
├── data/                         # schema.sql + 35 条设定
├── deploy/                       # Dify 1.18 compose / seekdb compose
├── scripts/                      # DSL 生成器、灌数据、自检
└── docs/                         # 设计 / 运行说明 / 验证样例 / 证据 / 开发记录
```

## 许可与来源说明

- `backend/vendor/pymysql/`：PyMySQL（MIT），随仓库分发以实现"零 pip 依赖"启动。
- `dsl/aye-ledger-chatflow.yml` 由 `scripts/build_dsl.py` 生成，可在本地一键重建。
- 设定（35 条）、角色与剧情均为本作品原创；PowerContext、Dify、seekdb 均为各自官方开源项目。
