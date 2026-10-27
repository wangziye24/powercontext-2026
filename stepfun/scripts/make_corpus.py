"""生成评测语料与标注查询集（可复现：脚本固定输出同样的文件）。"""
import json, pathlib

root = pathlib.Path(__file__).resolve().parents[1] / "corpus"
root.mkdir(parents=True, exist_ok=True)

docs = {
"seekdb-hybrid.md": """# seekdb 的混合检索

seekdb 是一个 MySQL 兼容的数据库，同时提供向量索引、全文索引与稀疏向量索引。
在 JS/TS SDK 中可以通过 Schema 同时声明 fulltextIndex 与 vectorIndex，集合因此同时具备
关键词匹配与语义相似两种召回能力。

## 全文索引与分词器

中文场景使用 ik 分词器，ik_mode 取 smart 表示智能分词，会自动合并更长的词组。
分词器决定了"向量检索之外"的关键词召回质量，中文短词、专有名词、型号编号尤其依赖它。
英文与数字则由 space 分词器按空白切分。

## 混合检索怎么执行

混合检索由数据库在一次查询里完成：全文本条件与向量近邻条件同时下推给引擎，
引擎各自召回候选后按融合算法排序，返回统一的 id、document、metadata 与 distance。
客户端不应把两路结果分别取回再自行拼合，那样既丢失数据库侧的融合排序，也无法复用索引。

## 增量写入与索引刷新

写入向量与原文后需要刷新索引，刷新之后新写入的内容才能被检索到。
批量写入时按批提交比逐条写入快得多，索引刷新一次即可。

## 性能要点

向量检索的延迟主要取决于候选规模与索引参数；批量写入的瓶颈在于索引构建。
写入与刷新分离后，可以先积累数据再集中刷新，把索引构建的代价摊薄。
""",

"seekdb-schema.md": """# seekdb 集合与表结构

集合是 seekdb 中组织数据的基本单位，它同时承载原文、元数据与向量。
创建集合时可以声明 schema，决定有哪些索引可用。

## 索引类型

FulltextIndexConfig 声明全文索引，参数包括分词器与分词模式。
VectorIndexConfig 声明稠密向量索引，参数包括维度、距离度量与 HNSW 构建参数。
SparseVectorIndexConfig 声明稀疏向量索引，常配合 BM25 之类的稀疏向量函数。

## 维度与距离

维度在创建集合时确定，之后写入的向量必须与之匹配，否则会被拒绝。
距离度量可选余弦或内积，中文语义检索常用余弦。

## 集合版本

集合有版本概念，SDK 内部以带前缀的内部表名保存元数据，
对外通过 getCollection 与 createCollection 访问，避免直接暴露内部结构。

## 元数据与过滤

写入时可以附带任意元数据，检索时通过 where 条件过滤。
过滤与向量近邻可以组合，用于在语义召回的基础上收窄候选。
""",

"seekdb-operations.md": """# 本地部署 seekdb

seekdb 提供两种本地形态：嵌入式与本地服务进程。嵌入式直接以库文件形式存在于目录中，
服务进程则监听端口，客户端用 MySQL 协议连接。

## 本地服务进程

服务进程需要一个基础目录，目录里通常包含 etc、log、run、store 四个子目录。
首次启动会初始化数据目录，耗时明显长于后续启动。

## 目录与路径

进程会把自身复制或硬链接到基础目录下，因此可执行文件路径与基础目录路径都需要可写。
在 Windows 上，如果路径包含非 ASCII 字符，复制操作可能失败，需要先转到纯 ASCII 目录。

## 连接参数

连接需要主机、端口、用户与口令，本地实例通常使用 root 用户与空口令。
端口冲突是常见的启动失败原因，随机端口是一种规避方式。

## 生命周期

启动、复用、关闭三个动作要分开处理：重复打开同一目录会复用已有实例，
关闭时先断开 SQL 连接再释放进程句柄，进程退出可能滞后于句柄释放。
""",

"edge-inference.md": """# 浏览器端侧推理

在浏览器里运行模型可以让数据不出设备：文档内容在本地被切块、编码成向量，
只有向量与必要的元数据会被送到数据库。用户上传的私有文档因此不会被第三方服务看到。

## 运行时与后端

Transformers.js 提供了 WebGPU 与 WASM 两种推理后端。WebGPU 走 GPU 管线，
吞吐明显更高；WASM 是兜底路径，兼容性最好但更慢。选择后端时应当显式配置，
并在界面上如实提示当前使用的后端，避免用户误判性能。

## 量化

量化把权重从 FP32 压到 INT8 或 FP16，模型体积随之显著下降，代价是精度损失。
在检索场景里，量化对召回排序的影响通常小于语料切块策略与查询改写带来的影响。
比较量化前后时，应当在同一个评测集上计算 Recall@K 与 nDCG@K，而不是只看几条样例。

## 批处理

逐条推理的固定开销会被放大，批量推理能显著提高吞吐。批大小受显存与内存限制，
需要按设备实际情况调整，并在报告中注明。

## 模型来源

端侧模型通常来自公开模型仓库，运行时从仓库拉取并缓存在浏览器。
为了保证离线可用与可复现，更稳妥的做法是把模型文件随应用一起分发。
""",

"edge-pipeline.md": """# 端到端流水线

一个端侧检索应用通常包含四段：切块、编码、写库、查询。每一段都有独立的耗时与优化空间。

## 切块

切块粒度直接决定召回质量与存储成本。块太大会稀释语义，块太小会丢上下文。
按段落合并到固定长度是一个简单可用的起点。

## 编码

编码阶段消耗最多时间。批量提交、复用模型实例、避免重复加载都能显著降低耗时。

## 写库

写入包含网络传输、事务提交与索引构建。索引刷新是显式动作，
把它放在批次边界可以避免每个小块都触发一次昂贵的索引构建。

## 查询

查询包含查询编码与数据库检索两段。查询编码通常在毫秒级，
检索延迟取决于索引状态与候选规模。两段耗时要分开测量才能定位瓶颈。
""",

"powercontext-memory.md": """# PowerContext 的任务记忆

PowerContext 用 Scope 隔离不同任务的记忆，把会话进展、已完成事项与待办持久化下来。
记忆的写入是增量的，检索时按预算挑选最相关的证据，避免把整个历史塞进上下文。

## 存储与检索

PowerContext 的存储后端可以是内置的 SQLite，也可以是 seekdb。使用 seekdb 时，
工程资料、规格条目、代码符号与测试可以被统一检索，检索结果直接用于构造开发上下文。

## 预算约束

上下文不是越长越好。给定预算后，应当按相关度挑选证据，宁可少而准。
这要求检索结果带有可比较的分数，并且评分口径在一次任务内保持一致。

## 交接与复用

跨会话交接时，下一次运行需要知道上一次做到哪一步、哪些结论已被验证。
把这些状态结构化保存，比依赖自然语言摘要更可靠。
""",

"powercontext-retrieval.md": """# 混合检索的评测方法

检索质量不能只看几条样例，需要在固定评测集上计算指标，并保证不同方案之间条件一致。

## Recall@K

Recall@K 衡量相关文档被召回的比例：前 K 条结果覆盖了多少标注为相关的文档。
它的弱点是对排名不敏感，排在第一和排在第五效果相同。

## nDCG@K

归一化折损累计增益把位置纳入考量：越靠前的命中贡献越大。
理想情况下的最大增益作为归一化分母，指标落在 0 到 1 之间。

## 评测集设计

查询应当覆盖不同意图，包括术语命中、语义近义和跨文档的问题。
每个查询的标注相关文档要人工确认，避免用模型生成标签造成自证。

## 量化前后的对比

量化对比必须在同一评测集、同一索引配置、同一查询集上进行，
只改变模型精度这一个变量，否则结论不可比。
""",

"oceanbase-arch.md": """# OceanBase 与 seekdb 的关系

seekdb 是面向本地与轻量场景的数据库形态，OceanBase 则是分布式、多租户形态，
两者在 SQL 层面兼容，因此同一套应用代码可以通过切换连接参数在两者上运行。

## 多租户与资源隔离

OceanBase 中不同租户共享集群资源但相互隔离，访问时需要额外携带租户名。
这会影响连接串的构造与权限校验。

## 向量能力

向量索引支持 HNSW 等图索引结构，距离度量可选余弦或内积。
维度在建立集合时确定，之后写入的向量必须与之一致，否则会被拒绝。

## 索引与刷新

写入之后刷新索引才能被检索到，刷新是显式动作。
在批量导入场景下，把刷新放到批次边界可以减少重复的索引构建开销。
""",

"oceanbase-search.md": """# 数据库侧的全文与向量索引

传统全文检索擅长精确词匹配，向量检索擅长语义相近但用词不同的场景，
两者的召回集合并不重合，混合检索的价值正在于把两路结果在数据库侧融合。

## 倒排与图索引

倒排索引把词项映射到文档集合，适合精确匹配与短语查询。
图索引把相近的向量连成邻接图，适合近邻搜索。

## 融合排序

融合排序需要兼顾两路分数，常见做法是加权求和或倒数排名融合。
无论哪种方式，前提是两路召回都由数据库完成。

## 索引刷新与可见性

写入与刷新之间存在窗口期，窗口期内新数据不可见。
批量导入应当把刷新放在批次边界，并让应用在写入完成后显式等待刷新结束。
""",
}

for name, text in docs.items():
    (root / name).write_text(text, encoding="utf-8")

# 标注查询集：每条给出 query 与相关文档（用于 Recall@K / nDCG@K）
queries = [
    {"q": "混合检索为什么要由数据库完成融合排序", "relevant": ["seekdb-hybrid.md"]},
    {"q": "中文全文索引用哪个分词器", "relevant": ["seekdb-hybrid.md"]},
    {"q": "写入向量后为什么还要刷新索引", "relevant": ["seekdb-hybrid.md", "oceanbase-arch.md"]},
    {"q": "增量写入怎么减少索引构建开销", "relevant": ["seekdb-hybrid.md", "oceanbase-arch.md"]},
    {"q": "集合可以同时建哪几种索引", "relevant": ["seekdb-schema.md"]},
    {"q": "向量维度在什么时候确定", "relevant": ["seekdb-schema.md", "oceanbase-arch.md"]},
    {"q": "距离度量有哪些选项", "relevant": ["seekdb-schema.md", "oceanbase-arch.md"]},
    {"q": "本地部署 seekdb 有哪两种形态", "relevant": ["seekdb-operations.md"]},
    {"q": "为什么路径里不能有中文", "relevant": ["seekdb-operations.md"]},
    {"q": "端口冲突要怎么避免", "relevant": ["seekdb-operations.md"]},
    {"q": "浏览器里跑模型有什么好处", "relevant": ["edge-inference.md"]},
    {"q": "WebGPU 不可用时怎么办", "relevant": ["edge-inference.md"]},
    {"q": "INT8 量化的代价是什么", "relevant": ["edge-inference.md"]},
    {"q": "怎么评估量化前后的检索质量", "relevant": ["edge-inference.md", "powercontext-retrieval.md"]},
    {"q": "批量推理对吞吐有什么影响", "relevant": ["edge-inference.md"]},
    {"q": "端侧模型从哪里获取更稳妥", "relevant": ["edge-inference.md"]},
    {"q": "切块粒度为什么重要", "relevant": ["edge-pipeline.md"]},
    {"q": "索引刷新应该放在什么时机", "relevant": ["edge-pipeline.md", "oceanbase-search.md"]},
    {"q": "查询耗时要怎么拆开测", "relevant": ["edge-pipeline.md"]},
    {"q": "PowerContext 如何隔离不同任务的记忆", "relevant": ["powercontext-memory.md"]},
    {"q": "上下文的预算约束是什么意思", "relevant": ["powercontext-memory.md"]},
    {"q": "跨会话交接需要保存哪些状态", "relevant": ["powercontext-memory.md"]},
    {"q": "Recall@K 和 nDCG@K 有什么区别", "relevant": ["powercontext-retrieval.md"]},
    {"q": "评测集应该怎么设计", "relevant": ["powercontext-retrieval.md"]},
    {"q": "量化对比要保证什么一致", "relevant": ["powercontext-retrieval.md"]},
    {"q": "seekdb 和 OceanBase 是什么关系", "relevant": ["oceanbase-arch.md"]},
    {"q": "多租户下访问需要带什么", "relevant": ["oceanbase-arch.md"]},
    {"q": "倒排索引和图索引分别适合什么", "relevant": ["oceanbase-search.md"]},
    {"q": "融合排序有哪些做法", "relevant": ["oceanbase-search.md", "seekdb-hybrid.md"]},
    {"q": "写入和刷新之间的窗口期是什么", "relevant": ["oceanbase-search.md"]},
]

(root / "queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"语料 {len(docs)} 篇，查询 {len(queries)} 条 -> {root}")
