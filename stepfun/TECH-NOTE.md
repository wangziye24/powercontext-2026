<!-- 与同目录 TECH-NOTE.md 内容完全相同（仅为兼容不同的大小写/连字符命名习惯） -->

# 一页技术说明 · 端侧迷你知识库（seekdb 向量 + 全文混合检索）

> OceanBase 2026 阶跃星辰赛题作品。全部 embedding 在浏览器本地计算，向量与原文写入本地 seekdb，
> 混合检索由 seekdb 完成。运行环境：Windows 11 / Intel Arc Xe-LPG / Microsoft Edge（Chromium）/ Node.js 24.15。

## 0. 关于"是否必须使用阶跃星辰模型"

赛题原文写明：**「模型要求：自选可公开获取的开源 embedding 模型，不限定使用阶跃星辰模型」**。本作品据此选择
`Xenova/bge-small-zh-v1.5`（BAAI，Apache-2.0，公开可获取，官方提供 ONNX 权重）。

除题面允许之外，还有一条硬约束指向同一个结论：**题面禁止云端推理**（"禁止调用云端推理 API"、
"实际计算必须发生在用户设备"）。我们核查了阶跃星辰在 Hugging Face 上的官方开源仓库 `stepfun-ai`：
其公开模型集中在大语言、多模态、语音与图像方向（safetensors / GGUF 等），**没有任何
feature-extraction / embedding 类、带 ONNX 权重的模型**；阶跃的 embedding 能力只以云端 API 形式提供。
因此在"必须端侧运行"的前提下，不存在可直接在浏览器中运行的阶跃 embedding 模型。

工程上我们把这件事做成了可扩展的：模型目录见 `web/src/embedder.js::MODEL_CATALOG`，可用 `?model=<id>` 切换
（切换后若向量维度变化，服务端会自动按新维度重建集合）。若阶跃星辰后续发布带 ONNX 权重的 embedding 模型，
加入目录即可，其余流程无需改动。

## 1. 模型与量化

| 项 | 内容 |
| --- | --- |
| 模型 | `Xenova/bge-small-zh-v1.5`（BAAI 开源中文 embedding 模型，Apache-2.0），revision `main`，512 维 |
| 获取 | `scripts/download-model.mjs` 从公开模型仓库下载（直连不可用时自动回落 `hf-mirror.com`），落到 `web/public/models/`，运行时完全离线 |
| 运行时 | Transformers.js 3.8.1 + onnxruntime-web（wasm 运行时同样本地化于 `web/public/ort/`，不依赖 CDN） |
| 量化方案 | 同一份权重的三种精度：FP32（`onnx/model.onnx`）、FP16（`onnx/model_fp16.onnx`）、INT8（`onnx/model_quantized.onnx`） |
| 量化处理方式 | INT8 采用 **ONNX 动态量化**：权重在模型加载期按张量量化到 int8（`quantize_dynamic`），无需额外校准数据；FP16 为半精度权重直存。两者都是官方仓库提供的量化权重，本项目直接取用，不自行训练或校准 |
| 池化 | CLS + L2 归一化（bge 官方推荐） |
| 模型获取与加载 | `scripts/download-model.mjs` 下载到 `web/public/models/`（下载耗时与字节数记入 `manifest.json`）；**运行时从本地读取，无下载等待**，加载 0.5–0.8 s |

**体积与压缩比**（同一口径：本地实际文件总字节数，含分词器与配置；清单与 sha256 见 `web/public/models/Xenova/bge-small-zh-v1.5/manifest.json`）

| 精度 | 模型总体积 | 相对 FP32 |
| --- | --- | --- |
| FP32 | **90.88 MiB** | 1.00× |
| FP16 | **45.72 MiB** | **1.99× 更小** |
| INT8(q8) | **23.32 MiB** | **3.90× 更小** |

**检索质量变化**（同一评测集、同一索引配置、同一 30 条标注查询，只改模型精度；K=5，Recall 命中同一文档只计一次）

| 精度 / 后端 | Recall@5 | nDCG@5 | 相对 FP32 的质量变化 |
| --- | --- | --- | --- |
| FP32 / WebGPU | 1.000 | **0.977** | 基线 |
| FP16 / WebGPU | 1.000 | **0.977** | **无可见下降**（Δ=0.000） |
| INT8 / WebGPU | 1.000 | 0.949 | ΔnDCG = **−0.028** |
| INT8 / WASM | 1.000 | 0.974 | ΔnDCG = **−0.003** |

结论：**FP16 是本机的最优解**（体积减半、质量不变、速度最快）；INT8 虽体积再降 3.9×，但在本机 WebGPU 路径上精度损失更大且反而更慢（见 §4）。

## 2. 端侧运行

| 项 | 内容 |
| --- | --- |
| 后端选择 | 启动即 `navigator.gpu.requestAdapter()`；拿到适配器用 **WebGPU**，否则**自动降级 WASM**，并把真实适配器信息显示在界面右上角徽标旁 |
| 显式配置 | 推理会话以 `device: 'webgpu' \| 'wasm'` 显式创建；URL 参数 `?backend=webgpu|wasm|auto` 可指定（默认 `auto`） |
| 降级策略 | 请求 WebGPU 但本机没有适配器时，**不会谎报**：探测失败即改用 WASM，界面显示实际后端与原因 |
| 实测适配器 | 本机 WebGPU 适配器：**`intel xe-lpg`**（Intel Arc 核显），headless 与有头模式一致 |
| 降级验证 | 同一套用例用 `?backend=wasm` 强制走 WASM 路径，流程与结果一致（Recall@5 均为 1.000），界面徽标显示 `WASM` |

## 3. seekdb 使用

**集合/表结构**（真实 DDL，`GET /api/schema` 可取）

```sql
CREATE TABLE `c$v2$<collection_id>` (
  `_id`      varbinary(512) NOT NULL,
  `document` string DEFAULT NULL,      -- 原文
  `embedding` VECTOR(512) DEFAULT NULL,-- 端侧算好的向量
  `metadata` json DEFAULT NULL,        -- 来源文件等元数据
  PRIMARY KEY (`_id`),
  FULLTEXT KEY `idx_fts` (`document`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart") BLOCK_SIZE 16384,
  VECTOR KEY `idx_vec` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG,
                                           M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC) BLOCK_SIZE 16384
);
```

**混合检索**：一次 `collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings: [vec] }, nResults })`
由数据库同时下推全文与向量条件并融合排序。**客户端不分别取两路结果再拼合**。

**增量写入与索引刷新**：写入后显式 `collection.refresh_index()`，新内容随即可检索；重复导入同一文档 id 走 **upsert** 语义不报错；
"清空知识库"用 `DROP TABLE` + 删除 SDK 元数据 + 重建集合（SDK 自带的 `delete()` 是软删除，行仍在表内，因此不能直接用）。

**集合与会话状态**：集合状态在 seekdb 侧（表 + `sdk_collections` 元数据）；会话状态在浏览器侧（`localStorage` 记录当前模型、精度、推理后端、集合名、片段数与上次查询），页面顶部状态栏实时展示"模型 · 精度 · 后端 · 集合 · 片段数 · 批大小 · 写入方式"。切换模型若向量维度不同，服务端会按新维度重建集合，避免旧向量残留导致检索结果与当前知识库不一致。

**模型可切换**：模型目录见 `web/src/embedder.js::MODEL_CATALOG`（默认 BAAI bge-small-zh-v1.5，另有 all-MiniLM-L6-v2 与 paraphrase-multilingual-MiniLM-L12-v2 两个多语言备选），界面下拉或 `?model=<id>` 均可切换；换维度时服务端自动重建集合。

**向量只来自浏览器**：服务端注册的 embedding 函数是占位实现，一旦服务端试图代算向量即抛错（`ClientSideEmbeddingFunction.generate`），
从机制上杜绝"服务端预计算向量"。

## 4. 性能（固定条件、分阶段计时、中位数）

**固定测试条件**

| 项 | 值 |
| --- | --- |
| 设备 | Windows 11 (10.0.26200) · Intel Core Ultra 7 155H · 31.5 GiB RAM |
| GPU 适配器 | `intel xe-lpg`（Intel Arc 核显，WebGPU 可用） |
| 浏览器 | Microsoft Edge（系统自带，Playwright 驱动） |
| Node.js | v24.15.0 |
| 语料 | 9 篇文档 / 27 个片段 / 30 条标注查询（`scripts/make_corpus.py` 固定生成） |
| seekdb | 本地 server 进程（seekdb 1.4.0，MySQL 协议）；集合 `knowledge_base`，512 维；`FULLTEXT KEY idx_fts (document) WITH PARSER ik, ik_mode=smart`；`VECTOR KEY idx_vec (embedding) WITH DISTANCE=COSINE, TYPE=HNSW, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64` |
| 模型 | `Xenova/bge-small-zh-v1.5` @ `main`；批大小 8（基线组为 1） |
| 计时范围 | 模型加载＝从本地加载 ONNX 权重到会话可用；文档向量化＝全量片段的端侧批量 embedding；写入+刷新＝一次 POST 写入与随后的索引刷新；查询向量化＝单条查询的端侧 embedding；seekdb 检索＝服务端 `hybridSearch` 的响应耗时；端到端＝查询向量化＋检索（不含结果渲染）。冷启动与预热后分开统计，热数据取 3 次重复的**中位数**。 |

| 配置 | 精度 | 后端 | 批大小 | 写入方式 | 模型体积 | Recall@5 | nDCG@5 | 吞吐 | 文档向量化 | 写入+刷新 | 查询向量化 | seekdb 检索 | 端到端(热) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 优化配置 | FP32 | WebGPU | 8 | 批量 | 90.88 MiB | 1.000 | 0.977 | 37.3 chunk/s | 724 ms | 783 ms | 15 ms | 7 ms | **21 ms** |
| 优化配置 | FP32 | WASM | 8 | 批量 | 90.88 MiB | 1.000 | 0.977 | 7.4 chunk/s | 3642 ms | 668 ms | 18 ms | 6 ms | 24 ms |
| 优化配置 | FP16 | WebGPU | 8 | 批量 | 45.72 MiB | 1.000 | **0.977** | **43.4 chunk/s** | 623 ms | 554 ms | 15 ms | 6 ms | **21 ms** |
| 优化配置 | FP16 | WASM | 8 | 批量 | 45.72 MiB | 1.000 | 0.977 | 7.6 chunk/s | 3554 ms | 768 ms | 32 ms | 6 ms | 39 ms |
| 优化配置 | INT8 | WebGPU | 8 | 批量 | 23.32 MiB | 1.000 | 0.949 | 9.1 chunk/s | 2980 ms | 858 ms | 151 ms | 7 ms | 159 ms |
| 优化配置 | INT8 | WASM | 8 | 批量 | 23.32 MiB | 1.000 | 0.974 | 12.3 chunk/s | 2191 ms | 767 ms | 14 ms | 6 ms | **20 ms** |
| 基线配置 | FP32 | WebGPU | **1** | **逐条** | 90.88 MiB | 1.000 | 0.977 | 28.9 chunk/s | 933 ms | **17289 ms** | 14 ms | 5 ms | 19 ms |

**优化前后对比**（除"批大小 / 写入方式"外，其余条件完全一致：同设备、同浏览器、同语料、同查询集、同 seekdb 配置、同模型同精度）

| 指标 | 基线：逐条推理 + 逐条写入（每条都刷新索引） | 优化后：批量推理 + 批量写入（批次末尾刷新一次） | 变化 |
| --- | --- | --- | --- |
| 文档向量化（27 片段） | 933 ms | 724 ms | **快 1.29×**（吞吐 28.9 → 37.3 chunk/s） |
| 写入 + 索引刷新 | 17289 ms | 783 ms | **快 22.1×** |
| Recall@5 / nDCG@5 | 1.000 / 0.977 | 1.000 / 0.977 | 质量完全一致 |

观察与优化点：

1. **写入与刷新策略是最大收益点**：逐条写入时每条都触发一次索引刷新，27 个片段要重建 27 次索引；改为一次批量写入、批次末尾刷新一次后，从 17.3 s 降到 0.78 s。
2. **WebGPU 优先是划算的**：同精度 FP32 下 WebGPU 向量化吞吐是 WASM 的 **5.0×**（37.3 vs 7.4 chunk/s）；端到端查询延迟两者都在 25 ms 内（查询只有一条文本，编码占比小）。
3. **FP16 优于 FP32**：体积减半、吞吐更高（43.4 vs 37.3 chunk/s）、质量完全一致。
4. **INT8 在本机 WebGPU 上是负优化**：动态量化图未针对 Xe-LPG 优化，吞吐掉到 9.1 chunk/s，单条查询编码 151 ms；换 WASM 反而更快（12.3 chunk/s、14 ms）且 nDCG 更高（0.974）。
5. **模型与运行时本地化**：模型文件在准备阶段一次性下载到 `web/public/models/`（下载耗时与字节数记入 `manifest.json`），运行时从本地读取，**没有下载等待**，加载稳定在 0.5–0.8 s；onnxruntime-web 的 wasm 运行时同样本地化于 `web/public/ort/`。
6. **seekdb 检索稳定在 5–7 ms**（含全文 + 向量两路召回与融合排序），是端到端延迟里最小的一段。

## 5. 复现

```bash
npm run setup                     # 装依赖 + 拷贝浏览器端第三方产物
node scripts/download-model.mjs   # 预下载模型（离线可用）
npm start                        # 拉起本地 seekdb + Web 应用 (127.0.0.1:5178)
node bench/smoke.mjs             # 端到端冒烟
node bench/run-bench.mjs         # 复现本说明的全部数据（结果落在 bench/results/）
```

评测集与语料可由 `python scripts/make_corpus.py` 重新生成（固定输出，语料与标注查询一致）。
