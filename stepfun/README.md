# 端侧迷你知识库 · seekdb 向量 + 全文混合检索

> OceanBase 2026 阶跃星辰（StepFun）赛题作品：**在浏览器本地完成文本向量化，用 seekdb 存储并做混合检索**。
> 文档不出设备——切块、embedding 全部在浏览器里算；向量、原文与元数据写入 seekdb；
> 查询时同样在本地生成查询向量，由 **seekdb 完成全文 + 向量的混合检索与排序**。

## 1. 赛题要求 ↔ 实现 ↔ 证据

| 赛题要求 | 本作品的实现 | 可核对的证据 |
| --- | --- | --- |
| 可运行的 Web 应用 | 浏览器直接运行 ESM（零构建）：`npm run setup && npm start` → http://127.0.0.1:5178 | `web/` 目录；`bench/smoke.mjs` 端到端自测 |
| 导入文本 → 本地向量化 → 写入 seekdb → 混合检索 → 展示原文与排序 | 前端切块后批量 embedding，随请求把向量传给后端写入集合；查询时本地生成查询向量，调 `collection.hybridSearch()` | `bench/smoke.mjs` 输出、界面检索结果区 |
| 端侧推理（禁云端推理、禁服务端预计算回放） | Transformers.js 在浏览器里跑 ONNX；后端注册"端侧占位 embedding 函数"，一旦服务端试图代算向量就抛错 | `server/src/seekdb-store.mjs` 的 `ClientSideEmbeddingFunction` |
| 模型须公开开源并注明来源与版本 | `Xenova/bge-small-zh-v1.5`（BAAI，Apache-2.0），revision `main`，512 维；模型目录可切换（`?model=`），换维度时服务端自动重建集合 | `web/public/models/.../manifest.json`（含 sha256）、`TECH-NOTE.md` §0 |
| 用阶跃星辰生态（题面不强制用阶跃模型） | 题面明确"自选可公开获取的开源 embedding 模型，不限定使用阶跃星辰模型"；且官方 `stepfun-ai` 开源仓库里没有带 ONNX 权重的 embedding 模型（云端 API 又被题面禁止），故选可端侧运行的开源权重 | `TECH-NOTE.md` §0 的核查结论 |
| INT8 / FP16 量化并报告体积、压缩比、质量变化 | 同权重三种精度（FP32 / FP16 / INT8-q8）本地可切换 | `bench/results/bench.md`、`bench/results/bench.json` |
| 优先 WebGPU，不支持时自动降级 WASM 并明确提示 | 启动即探测 `navigator.gpu.requestAdapter()`，不可用则 WASM；界面顶部常驻显示当前后端 | 界面右上角徽标；`bench/results/bench.md` 中 WebGPU 与 WASM 两组数据 |
| 用 seekdb 存取向量 + 原文 + 元数据 | 集合 `knowledge_base`：`ik` 中文全文索引 + HNSW 向量索引（cosine，512 维） | `GET /api/schema` 返回真实 DDL |
| 混合检索必须由 seekdb 完成，不得客户端拼合 | 一次 `hybridSearch({query:{whereDocument}, knn:{queryEmbeddings}})` 由数据库融合排序 | `server/src/seekdb-store.mjs::hybridSearch` |
| 增量写入与索引刷新 | 写入后显式 `refresh_index()`；重复导入同一 id 走 upsert 语义，不报错 | `server/src/seekdb-store.mjs::addDocuments` |
| 集合与会话状态 | 集合状态在 seekdb；会话状态（模型/精度/后端/集合/片段数/上次查询）在浏览器 `localStorage`，界面状态栏实时展示 | 界面顶部状态栏、`web/src/main.js` |
| 性能优化并给出**优化前后**实测 | 基线（逐条推理 + 逐条写入、每条都刷新索引）vs 优化（批量推理 + 批量写入、批次末尾刷新一次）：写入+刷新 **17289 ms → 783 ms（22.1×）**，质量完全一致 | `bench/results/bench.md` 的"优化前后对比" |
| 固定测试条件与计时范围 | 设备、GPU 适配器、语料规模、批大小、seekdb 索引参数、各阶段计时口径全部随结果落盘 | `bench/results/bench.json` 的 `conditions` |
| 一页技术说明 | `TECH-NOTE.md` | — |

## 2. 架构

```
浏览器 (Edge / Chrome)                          Node 服务 (server/)
┌────────────────────────────────┐            ┌──────────────────────────────┐
│ Transformers.js (ONNX Runtime)  │            │ 本地 seekdb 进程（MySQL 协议）│
│  ├ WebGPU 优先，失败降级 WASM   │  向量+原文  │  ├ 全文索引 ik (smart)         │
│  └ 文档切块 → 批量 embedding    │ ─────────► │  └ 向量索引 HNSW (cosine,512) │
│                                │  ←───────── │ hybridSearch 融合排序        │
│ 查询 → 本地 query embedding     │  命中+原文  │ （服务端不做任何推理）        │
└────────────────────────────────┘            └──────────────────────────────┘
```

要点：

- **向量只在一处产生**——浏览器。服务端只接收向量、写库、检索。
- **混合检索只有一处发生**——seekdb。服务端不分别取全文结果和向量结果再拼合。
- **模型与运行时都本地化**：`web/public/models/`（ONNX 权重）与 `web/public/ort/`（onnxruntime-web 的 wasm），页面用 import map 直接加载，不依赖任何 CDN。

## 3. 目录结构

```
stepfun/
├── server/
│   ├── src/server.mjs           # HTTP API + 静态托管前端
│   ├── src/seekdb-store.mjs     # 集合结构、写入/刷新/混合检索
│   └── src/local-seekdb.mjs      # 本地 seekdb 进程生命周期（端口、目录、降级）
├── web/
│   ├── index.html               # 界面（含 import map）
│   ├── src/main.js              # 导入→向量化→写库→检索 的交互与埋点
│   ├── src/embedder.js          # 端侧 embedding：后端探测、量化、批处理
│   └── public/{models,ort,vendor}/  # 本地模型、wasm 运行时、第三方 ESM
├── bench/
│   ├── smoke.mjs                # 端到端冒烟
│   └── run-bench.mjs            # 性能与检索质量基准（Playwright + 系统 Edge）
├── corpus/                      # 评测语料 + 标注查询集
├── scripts/                     # 装依赖、拷 vendor、下载模型、生成语料
├── data/                        # 本地 seekdb 数据目录（运行时生成）
├── README.md
└── TECH-NOTE.md                 # 一页技术说明
```

## 4. 快速开始

前置：Node.js ≥ 22（本地实测 24.15）、npm、Python 3（仅生成语料时需要）。

```bash
# 1) 安装依赖并把浏览器端第三方产物拷到 web/public/vendor
npm run setup

# 2) 预下载端侧模型到 web/public/models（离线可用；默认 huggingface.co，不通时自动回落 hf-mirror.com）
node scripts/download-model.mjs

# 3) 启动：拉起本地 seekdb + 前端（http://127.0.0.1:5178）
npm start

# 4) 端到端冒烟（驱动系统 Edge 跑一遍导入→检索）
node bench/smoke.mjs --dtype q8 --backend wasm

# 5) 性能与质量基准（两种精度 × 两种后端）
node bench/run-bench.mjs
```

浏览器打开 http://127.0.0.1:5178 后：选择端侧精度 → 选择若干 `.txt/.md` 文件导入 → 输入查询即可看到混合检索结果。

URL 参数 `?dtype=fp32|fp16|q8` 与 `?backend=webgpu|wasm` 可直接指定精度与后端，便于复现基准。

## 5. API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与 seekdb 连接信息 |
| POST | `/api/documents` | 写入文档（body 含 `ids`/`documents`/`metadatas`/`embeddings`，向量来自端侧），写完自动刷新索引 |
| POST | `/api/search` | 混合检索（body 含 `keyword`、`queryEmbedding`、`nResults`），排序由 seekdb 完成 |
| POST | `/api/reset` | 重建集合（清空知识库） |
| GET | `/api/schema` | 返回真实表名与 DDL（供技术说明引用） |

## 6. 环境注意事项（踩过的坑）

1. **路径必须纯 ASCII**：seekdb 启动时会把自身 copy/hardlink 到数据目录，若可执行文件或数据目录路径含中文会失败（`err=5` / `errno=2`）。`server/src/local-seekdb.mjs` 会把原生运行时复制到临时目录并自动改用 ASCII 数据目录。
2. **Windows 不会自动建子目录**：需预建 `etc/ log/ run/ store/`，同样由上面的模块处理。
3. **端口**：服务自行挑选空闲端口，避免与机器上其它实例冲突。
4. **npm 安装 `seekdb` 需要 `--legacy-peer-deps`**：它的 peer 依赖 `@seekdb/default-embed` 声明了 `workspace:*` 协议，npm 无法解析；本作品不使用该包（向量由端侧传入）。
5. **huggingface.co 直连不通时走 hf-mirror.com**；更稳妥的做法是执行 `scripts/download-model.mjs` 把模型落到本地，运行时完全离线。

## 7. 已知限制

- 语料规模较小（`corpus/` 为演示与评测用），真实场景需要更细的切块与更大的集合。
- 混合检索目前使用 seekdb 默认融合权重，未做权重调参对比。
- WASM 后端在长文档上的吞吐明显低于 WebGPU，详见 `TECH-NOTE.md` 的实测数据。
