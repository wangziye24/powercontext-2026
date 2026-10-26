-- ============================================================
-- seekdb 建表 + 混合检索（已在本机 seekdb 1.4.0.0 实测通过）
-- 验证时间：2026-10-26 23:5x   实例：127.0.0.1:2882
-- ============================================================

CREATE DATABASE IF NOT EXISTS coursenote;
USE coursenote;

CREATE TABLE IF NOT EXISTS docs (
  doc_id     BIGINT PRIMARY KEY AUTO_INCREMENT,
  title      VARCHAR(512) NOT NULL,
  source     VARCHAR(255) NOT NULL DEFAULT '',
  course     VARCHAR(64)  NOT NULL DEFAULT '未分类',
  tags       VARCHAR(255) NOT NULL DEFAULT '',
  chunk_cnt  INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id  BIGINT PRIMARY KEY AUTO_INCREMENT,
  doc_id    BIGINT NOT NULL,
  seq       INT NOT NULL DEFAULT 0,
  heading   VARCHAR(255) NOT NULL DEFAULT '',
  content   TEXT NOT NULL,
  embedding VECTOR(1536),                 -- 对齐 MiniMax embo-01 的 1536 维
  course    VARCHAR(64) NOT NULL DEFAULT '未分类',
  FULLTEXT INDEX ft_chunks_content (content) WITH PARSER ngram,   -- 中文必须用 ngram
  VECTOR INDEX idx_chunks_vec (embedding) WITH (distance=cosine, type=hnsw)
);

-- ------------------------------------------------------------
-- 插入向量：字面量必须带方括号，实测 '0.1,0.2' 会报 1210 Invalid argument
--   INSERT INTO chunks(doc_id,seq,heading,content,embedding,course)
--   VALUES (1,0,'标题','正文','[0.0123,-0.0456,...]','机器学习');
-- ------------------------------------------------------------

-- ============================================================
-- 混合检索：一条 SQL 同时使用「向量近邻 + 全文匹配 + 结构化过滤」
-- 实测输出（course=机器学习, query=如何缓解模型过拟合, kw=过拟合）：
--   chunk=8  过拟合与正则化  机器学习复习笔记.md  vdist=0.1518 kw=1.4089
--   chunk=9  Dropout 技巧    机器学习复习笔记.md  vdist=0.6077 kw=1.5558
--   chunk=10 模型评估指标    机器学习复习笔记.md  vdist=0.5798 kw=0.0000
-- ============================================================
SELECT c.chunk_id,
       c.heading,
       d.title,
       d.course,
       VECTOR_DISTANCE(c.embedding, %s) AS vdist,
       MATCH(c.content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS kw
FROM chunks c
JOIN docs d ON d.doc_id = c.doc_id
WHERE c.course = %s
ORDER BY (0.7 * (1 - VECTOR_DISTANCE(c.embedding, %s))
        + 0.3 * MATCH(c.content) AGAINST (%s IN NATURAL LANGUAGE MODE)) DESC
LIMIT 10;
-- 参数：query_vector('[...]') , keyword, course, query_vector('[...]'), keyword
