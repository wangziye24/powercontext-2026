# CourseNote — 学习资料混合检索助手

> 本项目由 **Qoder** 开发，使用 **seekdb**（向量 + 全文 + 结构化条件混合检索）实现核心能力。

## 项目简介

CourseNote 是一个学习资料混合检索助手，解决以下痛点：

1. **学习资料分散**：学生的笔记、课件、论文分散在不同文件和平台，难以统一管理
2. **检索效率低**：传统关键词检索无法理解语义，向量检索又缺乏精确匹配能力
3. **知识关联弱**：无法快速找到跨文档的知识点关联

**解决方案**：结合 seekdb 的三种检索能力：
- **向量检索**：基于 MiniMax embo-01 embedding，捕捉语义相似性
- **全文检索**：基于 ngram 分词的全文索引，精确匹配关键词
- **结构化过滤**：按课程、标签等元数据过滤

最终通过加权融合（默认 0.7:0.3）给出综合排序。

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (HTML/JS)                    │
│  上传 │ 检索 │ 问答 │ 文档管理                                │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP API
┌────────────────────────▼────────────────────────────────────┐
│                    FastAPI Backend                           │
│  /api/upload  /api/search  /api/ask  /api/docs  /api/stats  │
└────┬──────────────┬──────────────┬──────────────────────────┘
     │              │              │
     ▼              ▼              ▼
┌─────────┐  ┌───────────┐  ┌──────────┐
│ chunker │  │  embed.py │  │  llm.py  │
│ 分块解析 │  │ embedding │  │ MiniMax  │
└────┬────┘  └─────┬─────┘  │  Chat    │
     │             │        └──────────┘
     │             │
     ▼             ▼
┌─────────────────────────────────────────┐
│           seekdb (MySQL 协议)            │
│  docs 表  │  chunks 表                  │
│           │  - VECTOR(1536) 向量索引     │
│           │  - FULLTEXT 全文索引         │
│           │  - course/tags 结构化字段    │
└─────────────────────────────────────────┘
```

## 依赖与安装

### 系统要求

- Python 3.11+
- seekdb 1.4.0.0（或兼容的 MySQL 8.0+ 数据库）

### 安装步骤

```bash
# 1. 进入项目目录
cd qoder/

# 2. 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
```

依赖列表（已固定版本）：
- fastapi==0.115.*
- uvicorn==0.30.*
- pymysql==1.1.*
- pydantic==2.*
- python-dotenv==1.*
- python-multipart==0.0.*
- httpx==0.27.*
- numpy==2.*
- pypdf==5.*
- pytest==8.*

## seekdb 准备

### 方式一：Docker 部署（推荐）

```bash
# 拉取 seekdb 镜像（如有）
docker pull oceanbase/seekdb:1.4.0.0

# 启动容器
docker run -d \
  --name seekdb \
  -p 2881:2881 \
  -e OB_ROOT_PASSWORD=your_password \
  oceanbase/seekdb:1.4.0.0

# 等待启动完成（约 30 秒）
docker logs -f seekdb
```

### 方式二：本地二进制部署

如果已有 seekdb 1.4.0.0 本地二进制：

```bash
# 启动 seekdb
./bin/seekdb --port=2882 --data-dir=./data

# 默认连接参数：
# host: 127.0.0.1
# port: 2882（或 2881，取决于配置）
# user: root
# password: （默认空或配置文件中设置）
```

### 验证连接

```bash
# 使用 MySQL 客户端测试连接
mysql -h 127.0.0.1 -P 2882 -u root -p

# 或使用 Python
python -c "import pymysql; conn = pymysql.connect(host='127.0.0.1', port=2882, user='root'); print('连接成功')"
```

## 配置

### 1. 复制配置文件

```bash
cp .env.example .env
```

### 2. 编辑 .env 文件

```bash
# MiniMax API 配置
MINIMAX_API_KEY=sk-your-api-key-here
MINIMAX_BASE_URL=https://api.minimax.chat/v1
MINIMAX_EMBED_MODEL=embo-01
MINIMAX_CHAT_MODEL=MiniMax-M2.5

# seekdb 连接配置
SEEKDB_HOST=127.0.0.1
SEEKDB_PORT=2882
SEEKDB_USER=root
SEEKDB_PASSWORD=
DB_NAME=coursenote

# Embedding 提供方：minimax（云端）或 local（离线）
EMBED_PROVIDER=minimax
```

### 3. 获取 MiniMax API Key

1. 访问 [MiniMax 开放平台](https://www.minimaxi.com/)
2. 注册账号并创建应用
3. 在控制台获取 API Key
4. 将 Key 填入 `.env` 文件的 `MINIMAX_API_KEY`

### 4. 离线模式（可选）

如果无法访问 MiniMax API，可以使用本地 embedding 模型：

```bash
# 修改 .env
EMBED_PROVIDER=local

# 安装 fastembed
pip install fastembed==0.9.*
```

本地模型使用 `BAAI/bge-small-zh-v1.5`（512 维），无需网络请求，但效果略逊于 MiniMax embo-01（1536 维）。

## 启动服务

```bash
# 确保已激活虚拟环境
source .venv/bin/activate

# 建表（幂等操作）
python -c "from app.db import create_tables; create_tables()"

# 灌入示例数据（可选）
python scripts/seed_data.py

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8080

# 访问前端页面
# 浏览器打开 http://localhost:8080
```

### API 文档

启动服务后访问：
- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc

## 验证样例

### 冒烟测试

```bash
python scripts/smoke_test.py
```

**真实运行输出**（2026-10-27 于 seekdb 1.4.0.0 + MiniMax embo-01 实测）：

```
============================================================
CourseNote 冒烟测试
============================================================

  未检测到后端，自动启动 http://127.0.0.1:8080 ...
  后端已就绪
[1/4] 检查健康状态...
  status: ok
  db: 127.0.0.1:2882/coursenote
  embed_provider: minimax

[2/4] 上传种子文档...
  [1/3] 机器学习复习笔记.md...
    doc_id=25, chunks=10
  [2/3] 分布式系统讲义.md...
    doc_id=26, chunks=10
  [3/3] OceanBase全文索引优化.md...
    doc_id=27, chunks=10

[3/4] 测试检索: '如何缓解过拟合' (course=机器学习)...
  命中数: 3
  Top-3 来源:
    - 机器学习复习笔记.md / 机器学习复习笔记 / 第2章 模型评估 / 2.2 缓解过拟合的方法
      dist=0.0927, kw=1.1118, score=0.9351
    - 机器学习复习笔记.md / 机器学习复习笔记 / 第2章 模型评估 / 2.1 过拟合与欠拟合
      dist=0.1915, kw=1.0938, score=0.8611
    - 机器学习复习笔记.md / 机器学习复习笔记 / 第3章 常用算法 / 3.2 逻辑回归
      dist=0.4577, kw=0.0000, score=0.3796

[4/4] 测试问答: '请解释什么是过拟合，以及如何缓解？'...
  答案长度: 582 字符
  答案摘要: ### 什么是过拟合

过拟合是指模型在**训练数据上表现很好，但在新数据上表现较差**的现象。其主要原因通常是模型过于复杂，学到了训练数据中的噪声和细节，而不是数据背后的真正规律。[1]

### 如何缓解过拟合

资料中列出了以下常用方法：[2]

1. **正则化**
   - 在损失函数中加入惩罚项，限制模型复杂度。
   - **L1 正则化（Lasso）**：产生稀疏解。
   - *...
  引用来源: 2 条
    - 机器学习复习笔记.md (chunk_id: 245)
    - 机器学习复习笔记.md (chunk_id: 216)

============================================================
测试结果汇总
============================================================
  health: PASS
  upload: PASS
  search: PASS
  ask: PASS

所有测试通过！
```

### 手动检索示例

```bash
# 查询"如何缓解过拟合"
curl -X POST http://localhost:8080/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "如何缓解过拟合", "course": "机器学习", "top_k": 3}'
```

**返回示例**：

```json
{
  "query": "如何缓解过拟合",
  "results": [
    {
      "chunk_id": 8,
      "doc_id": 1,
      "heading": "第2章 模型评估 / 2.2 缓解过拟合的方法",
      "content": "缓解过拟合的常用方法包括：1. 正则化（Regularization）...",
      "title": "机器学习复习笔记.md",
      "source": "机器学习复习笔记.md",
      "course": "机器学习",
      "dist": 0.1518,
      "kw_score": 1.4089,
      "kw_score_norm": 1.0,
      "score": 0.8562
    }
  ],
  "total": 3
}
```

**混合检索 SQL**（实际执行）：

```sql
SELECT c.chunk_id, c.heading, d.title,
       VECTOR_DISTANCE(c.embedding, '[0.012,-0.034,...]') AS dist,
       MATCH(c.content) AGAINST ('如何缓解过拟合' IN NATURAL LANGUAGE MODE) AS kw_score
FROM chunks c
JOIN docs d ON d.doc_id = c.doc_id
WHERE c.course = '机器学习'
ORDER BY (0.7 * (1 - dist) + 0.3 * kw_score) DESC
LIMIT 3;
```

## 目录结构

```
qoder/
├── README.md              # 本文档
├── .env.example           # 环境变量示例
├── .gitignore             # Git 忽略规则
├── requirements.txt       # Python 依赖
├── 02-seekdb-schema.sql   # seekdb 建表 SQL（参考）
│
├── app/                   # 后端代码
│   ├── __init__.py
│   ├── main.py            # FastAPI 入口，路由定义
│   ├── config.py          # 配置管理（从 .env 读取）
│   ├── db.py              # seekdb 连接池、建表、混合检索
│   ├── embed.py           # Embedding 封装（MiniMax / local）
│   ├── chunker.py         # 文档解析与分块
│   ├── llm.py             # MiniMax Chat 调用
│   └── schemas.py         # Pydantic 请求/响应模型
│
├── web/                   # 前端
│   └── index.html         # 单页应用（HTML + CSS + JS）
│
├── scripts/               # 脚本
│   ├── seed_data.py       # 灌入示例文档
│   └── smoke_test.py      # 端到端冒烟测试
│
├── tests/                 # 单元测试
│   ├── __init__.py
│   ├── test_chunker.py    # 分块测试
│   └── test_embed.py      # Embedding 测试
│
└── docs/                  # 设计文档
    ├── 00-项目设计.md
    ├── 01-Qoder提示词.md
    ├── 02-seekdb-schema.sql
    └── 03-验证样例.md
```

## 常见问题

### 1. seekdb 连接失败

**错误信息**：`pymysql.err.OperationalError: (2003, "Can't connect to MySQL server"）`

**解决方案**：
1. 检查 seekdb 是否启动：`docker ps` 或 `ps aux | grep seekdb`
2. 检查连接参数：确认 `.env` 中的 `SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD` 正确
3. 检查防火墙：确保端口 2881/2882 未被阻止
4. 测试连接：`mysql -h 127.0.0.1 -P 2882 -u root -p`

### 2. Embedding 维度不匹配

**错误信息**：`向量维度不匹配：期望 1536，实际 512`

**原因**：`.env` 中的 `EMBED_PROVIDER` 与实际使用的模型不一致

**解决方案**：
- 如果使用 MiniMax：`EMBED_PROVIDER=minimax`（1536 维）
- 如果使用本地模型：`EMBED_PROVIDER=local`（512 维）
- 切换后需要重建表：`DROP TABLE chunks; DROP TABLE docs;` 然后重新运行建表

### 3. 中文全文检索效果不佳

**现象**：关键词匹配得分很低或为 0

**可能原因**：
1. **ngram 分词器未启用**：seekdb 默认使用 ngram 分词，如果建表时报错退化为普通分词，中文效果会较差
2. **查询词过短**：单字查询难以匹配，建议使用 2 字以上的关键词
3. **数据量太少**：全文检索在大数据量下效果更好

**解决方案**：
- 检查建表日志，确认 ngram 分词器可用
- 增加查询词长度，或使用多个关键词
- 调高向量检索权重：在 `.env` 中设置 `SCORE_WEIGHT_VEC=0.8`、`SCORE_WEIGHT_KW=0.2`

### 4. MiniMax API 调用失败

**错误信息**：`MiniMax embedding 调用失败（已重试 2 次）`

**解决方案**：
1. 检查 API Key 是否正确：`MINIMAX_API_KEY` 是否填入 `.env`
2. 检查网络连接：`curl https://api.minimax.chat/v1/models`
3. 检查配额：登录 MiniMax 控制台查看 API 调用额度
4. 使用离线模式：设置 `EMBED_PROVIDER=local`

### 5. 前端页面无法加载

**现象**：访问 http://localhost:8080 显示空白或 404

**解决方案**：
1. 确认服务已启动：`curl http://localhost:8080/api/health`
2. 检查 `web/index.html` 是否存在
3. 查看浏览器控制台错误信息
4. 尝试直接访问 API：http://localhost:8080/docs

## 开发相关

### 运行单元测试

```bash
python -m pytest tests/ -v
```

### 代码结构说明

- **config.py**：所有配置集中管理，从 `.env` 读取
- **db.py**：连接池复用，混合检索 SQL 已优化
- **embed.py**：支持 MiniMax 和 local 两种 provider，带重试机制
- **chunker.py**：支持 PDF/Markdown/TXT，按标题层级分块
- **main.py**：FastAPI 路由，所有异常都有明确错误信息

### 扩展建议

1. **增加更多 embedding 模型**：在 `embed.py` 中添加新的 provider
2. **支持更多文件格式**：在 `chunker.py` 中添加 Word/PPT 解析
3. **增加用户认证**：使用 FastAPI 的 OAuth2 或 JWT
4. **缓存优化**：对频繁查询使用 Redis 缓存

## 许可证

本项目为 OceanBase 2026 竞赛参赛作品。

## 联系方式

如有问题，请提交 Issue 或联系开发团队。
