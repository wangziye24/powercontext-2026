# 端侧知识库基准结果（生成于 2026-10-26T15:20:13.562Z）

浏览器：Microsoft Edge (系统自带, Playwright 驱动)；K=5；每条查询重复 3 次取中位数（首次为冷启动）。
设备：win32 10.0.26200 · Intel(R) Core(TM) Ultra 7 155H · 31.5 GiB RAM；GPU 适配器：intel xe-lpg；Node v24.15.0
语料：9 篇文档 / 27 个片段 / 30 条标注查询（语料与标注查询由 scripts/make_corpus.py 固定生成）。
seekdb 配置：FULLTEXT KEY idx_fts (document) WITH PARSER ik, ik_mode=smart；VECTOR KEY idx_vec (embedding) WITH DISTANCE=COSINE, TYPE=HNSW, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64；部署方式：本地 server 进程（@oceanbase/seekdb-node 拉起 seekdb 1.4.0，MySQL 协议）

计时范围：模型加载=从浏览器加载本地 ONNX 权重到会话可用；文档向量化=端侧批量 embedding 全量片段；写入+刷新=一次 POST 写入与随后的索引刷新；查询向量化=单条查询的端侧 embedding；seekdb 检索=服务端 hybridSearch 的响应耗时；端到端=查询向量化+检索（不含结果渲染）。首次（冷）与后续（热）分开统计，热数据取重复查询的中位数。

本机提供 WebGPU 适配器，表中的 webgpu 行即 WebGPU 实测；wasm 行通过 `?backend=wasm` 显式走降级路径。

| 配置 | 精度 | 实际后端 | 批大小 | 写入方式 | 模型体积(MiB) | Recall@5 | nDCG@5 | 吞吐(chunk/s) | 文档向量化(ms) | 写入+刷新(ms) | 查询向量化(ms) | seekdb 检索(ms) | 端到端(ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 优化配置 | fp32 | webgpu | 8 | batch | 90.88 | 1.000 | 0.977 | 37.3 | 724 | 783 | 15 | 7 | 21 |
| 优化配置 | fp32 | wasm | 8 | batch | 90.88 | 1.000 | 0.977 | 7.4 | 3642 | 668 | 18 | 6 | 24 |
| 优化配置 | fp16 | webgpu | 8 | batch | 45.72 | 1.000 | 0.977 | 43.4 | 623 | 554 | 15 | 6 | 21 |
| 优化配置 | fp16 | wasm | 8 | batch | 45.72 | 1.000 | 0.977 | 7.6 | 3554 | 768 | 32 | 6 | 39 |
| 优化配置 | q8 | webgpu | 8 | batch | 23.32 | 1.000 | 0.949 | 9.1 | 2980 | 858 | 151 | 7 | 159 |
| 优化配置 | q8 | wasm | 8 | batch | 23.32 | 1.000 | 0.974 | 12.3 | 2191 | 767 | 14 | 6 | 20 |
| 基线配置（逐条推理+逐条写入） | fp32 | webgpu | 1 | each | 90.88 | 1.000 | 0.977 | 28.9 | 933 | 17289 | 14 | 5 | 19 |

压缩比：FP32→FP16 = 1.99×；FP32→INT8 = 3.90×。

## 优化前后对比（其余条件完全一致：同设备、同浏览器、同语料、同查询集、同 seekdb 配置）

| 指标 | 基线（逐条推理 + 逐条写入，每条都刷新索引） | 优化后（批量推理 + 批量写入，批次末尾刷新一次） | 变化 |
|---|---|---|---|
| 文档向量化（27 片段） | 933 ms | 724 ms | 快 1.29× |
| 向量化吞吐 | 28.9 chunk/s | 37.3 chunk/s | 快 1.29× |
| 写入 + 索引刷新 | 17289 ms | 783 ms | 快 22.1× |
| Recall@5 / nDCG@5 | 1.000 / 0.977 | 1.000 / 0.977 | 质量一致 |
