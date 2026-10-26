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
| 量化方案 | 同一份权重的三种精度：FP32（`onnx/model.onnx`）、FP16（`onnx/model_fp16.onnx`）、INT8（`onnx/model_quantized.onnx`，动态量化） |
| 池化 | CLS + L2 归一化（bge 官方推荐） |

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

**向量只来自浏览器**：服务端注册的 embedding 函数是占位实现，一旦服务端试图代算向量即抛错（`ClientSideEmbeddingFunction.generate`），
从机制上杜绝"服务端预计算向量"。

## 4. 性能（分阶段计时，中位数；首次与预热分开）

语料：9 篇文档 / 27 个片段 / 30 条标注查询；浏览器：系统 Edge。

| 精度 | 后端 | 模型加载 | 文档向量化 | 写入+刷新 | 向量化吞吐 | 查询向量化 | seekdb 检索 | 端到端(热) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FP32 | WebGPU | 769 ms | 686 ms | 789 ms | **39.3 chunk/s** | 14.4 ms | 5.0 ms | 20 ms |
| FP32 | WASM | 685 ms | 2706 ms | 477 ms | 10.0 chunk/s | 14.3 ms | 5.0 ms | 19 ms |
| FP16 | WebGPU | 604 ms | 607 ms | 786 ms | **44.5 chunk/s** | 15.2 ms | 5.0 ms | 20 ms |
| INT8 | WebGPU | 539 ms | 2501 ms | 584 ms | 10.8 chunk/s | 166.4 ms | 5.0 ms | 171 ms |
| INT8 | WASM | 469 ms | 1985 ms | 682 ms | 13.6 chunk/s | 14.3 ms | 5.0 ms | 19 ms |

观察与优化点：

1. **WebGPU 优先是划算的**：同精度 FP32 下 WebGPU 向量化吞吐是 WASM 的 **3.9×**（39.3 vs 10.0 chunk/s），端到端查询延迟两者都在 20 ms 内（查询只有一条文本，编码占比小）。
2. **FP16 优于 FP32**：体积减半、吞吐更高（44.5 vs 39.3）、质量不变。
3. **INT8 在本机 WebGPU 上是负优化**：动态量化图未针对 Xe-LPG 优化，吞吐掉到 10.8 chunk/s，单条查询编码 166 ms；换 WASM 反而更快（13.6 chunk/s、14.3 ms）。
4. **优化措施**：模型与 wasm 运行时本地化（消除首次下载等待，加载稳定在 0.5–0.8 s）；批量 embedding（批大小 8）+ 每批让出主线程；写入批量提交并只在批次边界刷新索引；切块按段落合并到约 200 字。
5. **seekdb 检索稳定在 5 ms**（含混合检索的全文+向量两路召回与融合排序），是端到端延迟里最小的一段。

## 5. 复现

```bash
npm run setup                     # 装依赖 + 拷贝浏览器端第三方产物
node scripts/download-model.mjs   # 预下载模型（离线可用）
npm start                        # 拉起本地 seekdb + Web 应用 (127.0.0.1:5178)
node bench/smoke.mjs             # 端到端冒烟
node bench/run-bench.mjs         # 复现本说明的全部数据（结果落在 bench/results/）
```

评测集与语料可由 `python scripts/make_corpus.py` 重新生成（固定输出，语料与标注查询一致）。
