# Qoder 开发提示词包（逐条粘贴给 Qoder）

> 使用方式：每一步新建一个 Qoder 对话，粘贴该步提示词 + 上一轮的产出，让 Qoder 增量开发。
> 项目根目录：`qoder/`（在 powercontext 仓库的 `2026-competition-ecosystem` 分支上）
> 建议技术栈：Python 3.11 + FastAPI + 原生 HTML/JS 前端（不用前端框架，减少依赖）；SQL 用 PyMySQL。

---

## 步骤 0：项目骨架与配置

```
在当前仓库的 qoder/ 目录下创建一个 Python 项目 "coursenote"（学习资料混合检索助手）：

- 结构：
  qoder/
    README.md
    .env.example
    requirements.txt
    app/
      __init__.py
      main.py          # FastAPI 入口
      config.py        # 从 .env 读配置（MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_EMBED_MODEL=embo-01 / MINIMAX_CHAT_MODEL=MiniMax-M2.5 / SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASSWORD / DB_NAME=coursenote / EMBED_PROVIDER=minimax）
      db.py            # PyMySQL 连接池 + 建表 + 混合检索 SQL
      embed.py         # embedding 封装（minimax / local 两种 provider）
      chunker.py       # 文档解析与分块
      llm.py           # MiniMax chat 调用
      schemas.py       # pydantic 请求/响应模型
    web/
      index.html       # 单页前端：上传/检索/问答/文档列表
    scripts/
      seed_data.py     # 灌入示例文档与分块
      smoke_test.py    # 端到端自检脚本
  tests/
    test_chunker.py
    test_embed.py

要求：
1. requirements.txt 固定版本：fastapi==0.115.*, uvicorn==0.30.*, pymysql==1.1.*, pydantic==2.*, python-dotenv==1.*, httpx==0.27.*, pytest==8.*, pypdf==5.*, numpy==2.*
2. 所有配置从环境变量读取，仓库里只提交 .env.example，绝不写真实 Key
3. main.py 暴露健康检查 GET /api/health
4. 代码风格：类型注解 + docstring，模块级常量集中放在 config.py
```

---

## 步骤 1：MiniMax embedding 封装（重要：非 OpenAI 兼容格式）

```
实现 app/embed.py：

MiniMax embedding 接口不是 OpenAI 兼容格式，必须用 texts 字段：

POST {MINIMAX_BASE_URL}/embeddings
Headers: Authorization: Bearer {MINIMAX_API_KEY}, Content-Type: application/json
Body: {"model": "embo-01", "texts": ["文本1", "文本2"], "type": "db"}   # type: db 入库 / query 查询
Response: {"vectors": [[...1536维...], ...], "total_tokens": N, "base_resp": {...}}

要求：
- class Embedder，接口方法 embed_documents(texts: list[str]) -> list[list[float]] 与 embed_query(text: str) -> list[float]
- 文档用 type="db"，查询用 type="query"（带查询前缀可自行加）
- 校验返回值：vectors 为空或维度 != 1536 时抛出明确异常
- 失败重试 2 次（指数退避），超时 20s
- 提供 local provider：使用 fastembed 的 TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")，512 维，
  通过 EMBED_PROVIDER=local 切换；注意 local 与 minimax 维度不同，需要在 db.py 中按 provider 决定建表维度
- 写单测 test_embed.py：用 mock httpx 验证请求体里是 texts 而不是 input
```

---

## 步骤 2：文档解析与分块

```
实现 app/chunker.py：

- 支持 .pdf（用 pypdf 逐页取文本，保留每页作为 heading）、.md/.txt（按 markdown 标题切段）
- 分块策略：按 heading 分组，再按最大长度（默认 600 字符）切分，重叠 80 字符
- 每个 chunk 返回 dict: {seq, heading, content}
- 保留原始标题层级，heading 形如 "第3章 过拟合 / 3.2 常用缓解方法"
- 单测：给一段含标题的 markdown，断言分块数量、每块 <= 600 字符、相邻块有重叠
```

---

## 步骤 3：seekdb 存储与混合检索

```
实现 app/db.py（PyMySQL，MySQL 协议）：

建表（幂等，CREATE TABLE IF NOT EXISTS）：
  docs(doc_id BIGINT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(512), source VARCHAR(255),
       course VARCHAR(64) DEFAULT '未分类', tags VARCHAR(255) DEFAULT '', chunk_cnt INT DEFAULT 0,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
  chunks(chunk_id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_id BIGINT, seq INT, heading VARCHAR(255),
       content TEXT, embedding VECTOR(1536), course VARCHAR(64) DEFAULT '未分类')

索引：
- content 建 FULLTEXT 索引（中文场景同时用 ngram 分词器，若不支持则退化为普通全文索引并在注释说明）
- embedding 建 VECTOR 索引

混合检索函数 search(query_vec, query_text, course=None, top_k=10)：
一条 SQL 同时用到三种能力：
  SELECT c.chunk_id, c.doc_id, c.heading, c.content, d.title, d.source,
         VECTOR_DISTANCE(c.embedding, %s) AS dist
  FROM chunks c JOIN docs d ON d.doc_id = c.doc_id
  WHERE (%s IS NULL OR c.course = %s)
    AND (MATCH(c.content) AGAINST (%s IN NATURAL LANGUAGE MODE) OR 1 = 1)
  ORDER BY dist ASC LIMIT %s

要求：
- 混合打分：最终得分 = a * (1 - dist) + b * 归一化后的 kw_score（a、b 可配，默认 0.7/0.3）
- 写入用 executemany 批量插入 chunks
- 提供 count_by_course() 统计、delete_doc(doc_id)
- 连接失败要给出清晰错误（连接串来源、端口）
```

---

## 步骤 4：FastAPI 接口 + 问答

```
实现 app/main.py 的接口：

- POST /api/upload  (multipart: file, course, tags) → 解析→分块→embedding→写 seekdb→返回 doc_id 与 chunk 数
- POST /api/search  (json: query, course?, top_k?) → 混合检索 → 返回片段列表（含标题、来源、dist、kw_score、snippet）
- POST /api/ask     (json: query, course?, top_k?) → 先 search，再把 Top-K 片段拼成上下文，
    调用 MiniMax chat（MiniMax-M2.5，POST {BASE_URL}/chat/completions，OpenAI 风格 body:
    {"model": ..., "messages":[{"role":"system","content":"你是学习资料助手，只依据给定资料回答，并标注来源"},
    {"role":"user","content": "<资料片段>\n\n问题：..."}]}）→ 返回 {answer, sources:[{title, source, chunk_id}]}
- GET  /api/docs    → 文档列表
- DELETE /api/docs/{doc_id}
- GET  /api/stats   → 按课程统计

要求：
- 检索失败时返回 500 + 明确错误信息，不静默吞异常
- /api/ask 的 sources 必须来自真实检索结果（用于评审核对"技术结合有效"）
```

---

## 步骤 5：前端页面

```
实现 qoder/web/index.html（单文件，含少量 CSS/JS，无框架）：

四个区块：
1. 上传区：选择文件 + 课程 + 标签 → 上传后显示分块数
2. 检索区：输入问题 + 可选课程下拉 + top_k → 表格展示命中片段（标题/来源/相似度/关键词分）
3. 问答区：提问 → 展示答案 + 引用来源列表
4. 资料区：文档列表 + 删除按钮 + 按课程统计

要求：
- 中文界面，样式清爽
- 顶部展示"后端健康状态"（调用 /api/health）
- 检索结果里把相似度与命中关键词显式展示，便于评审看到"混合检索"确实生效
```

---

## 步骤 6：种子数据与冒烟脚本

```
实现 qoder/scripts/seed_data.py：
- 内置 3 篇示例资料（可直接写在脚本里）：
  1) 《机器学习复习笔记.md》：包含"过拟合""正则化""Dropout"等章节
  2) 《分布式系统讲义.md》：包含"一致性""CAP""向量时钟"等章节
  3) 《OceanBase 全文索引优化.pdf》或同名 .md：包含"FTS""倒排索引""构建性能"等章节
- 每篇带 course 标签（机器学习 / 分布式系统 / 数据库）
- 上传到 seekdb 后打印每个 chunk 的 embedding 维度（必须 1536）

实现 qoder/scripts/smoke_test.py：
- 依次调用 /api/health、/api/upload（用 seed 文件）、/api/search（查询"如何缓解过拟合"并指定 course=机器学习）、
  /api/ask（同一个问题）→ 打印检索命中数、Top-3 来源、答案摘要，并以 exit code 表示成败
- README 里写明：python scripts/smoke_test.py 的预期输出（命中数 >= 2 且来源包含《机器学习复习笔记.md》）
```

---

## 步骤 7：README 与运行说明

```
写 qoder/README.md，包含：
1. 项目简介与解决的痛点
2. 架构图（ASCII）
3. 依赖与安装（Python 3.11、pip install -r requirements.txt）
4. seekdb 准备：给出 docker run 命令与"用 seekdb 1.4.0.0 本地二进制"两种方式，
   并写明 MySQL 协议连接参数（host/port/user/password/默认端口）
5. 配置：复制 .env.example 为 .env 并填写 MINIMAX_API_KEY（说明去 MiniMax 开放平台申请；
   也说明 EMBED_PROVIDER=local 可离线运行）
6. 启动：uvicorn app.main:app --host 0.0.0.0 --port 8080
7. 验证样例：贴出 smoke_test 的预期输出与一次真实检索结果（含 SQL 与返回片段）
8. 目录结构说明
9. 常见问题：seekdb 连接失败 / embedding 维度不匹配 / 中文全文检索效果
所有命令与路径必须是真实可执行的，不要写占位命令。
```

---

## 步骤 8（可选加分）：PowerContext 记忆

```
在 app/memory.py 中接入 PowerContext（HTTP API，base_url 由 POWCONTEXT_BASE_URL 配置）：
- remember(user_id, kind, content)：写入"用户的课程偏好/检索历史/薄弱点"
- recall(user_id, kind, limit)：取回并在 /api/ask 的 system prompt 中作为附加上下文
- 在 /api/search 与 /api/ask 响应里返回 used_memory 字段，便于评审核对"记忆被复用"
- 开关：ENABLE_MEMORY=true/false，未配置 PowerContext 时自动降级为不使用记忆（并在响应里说明）
```

---

## 步骤 9：自查清单（让 Qoder 执行）

```
请自查并逐条修复：
1. python -m pytest tests -q 全部通过
2. python scripts/smoke_test.py 在本地 seekdb 跑通（检索命中 >= 2，来源正确）
3. 所有 Key 只从环境变量读取，仓库中没有任何真实密钥
4. README 中每条命令都真实可执行
5. 代码中没有遗留 TODO / pass 占位
```

## 给 Qoder 的总控提示词（可选，用于一次性生成）

```
请在 qoder/ 目录下实现"学习资料混合检索助手"，需求如下（严格按下面 8 个模块实现，并逐个模块自测）：
[把本文件 步骤 0~7 的内容整体粘贴]
技术约束：Python 3.11 + FastAPI + PyMySQL + 原生前端；数据库使用 seekdb（MySQL 协议），
向量列使用 VECTOR(1536) 与 MiniMax embo-01 对齐；全文索引用于关键词匹配；查询同时使用三者并混合打分。
```
