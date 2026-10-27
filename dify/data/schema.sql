-- ============================================================
-- 午夜便利店 · 阿夜的账本 —— seekdb 建表脚本
-- 目标实例：seekdb 1.4.0.0（MySQL 协议，127.0.0.1:2881）
-- 说明：
--   * lore 表同时挂「向量索引 + 全文索引」，供 DBMS_HYBRID_SEARCH 做混合检索
--     （提示：混合检索的表不要显式定义主键，这里用普通列 id）
--   * 中文全文必须用 ngram 分词器，否则 MATCH ... AGAINST 命中不了
-- ============================================================

CREATE DATABASE IF NOT EXISTS nightstore;
USE nightstore;

-- ------------------------------------------------------------
-- 设定库：人物设定 / 商品 / 整蛊事件 / 结局 / 城市传闻
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore (
  id        BIGINT       NOT NULL,
  kind      VARCHAR(32)  NOT NULL,        -- profile|item|prank|ending|rumor
  title     VARCHAR(255) NOT NULL,
  content   TEXT         NOT NULL,
  tag       VARCHAR(64)  NOT NULL DEFAULT '',
  weight    INT          NOT NULL DEFAULT 0,
  cond_min  INT          NOT NULL DEFAULT -999,  -- 触发所需好感度下限
  cond_max  INT          NOT NULL DEFAULT 999,   -- 触发所需好感度上限
  cond_pranks INT        NOT NULL DEFAULT 0,     -- 触发所需「已完成整蛊数」下限
  embedding VECTOR(1536),
  VECTOR  INDEX idx_lore_vec (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
  FULLTEXT INDEX ft_lore_content (content) WITH PARSER ngram
);

-- ------------------------------------------------------------
-- 玩家状态（一行一个玩家）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_state (
  player_id   VARCHAR(64) PRIMARY KEY,
  player_name VARCHAR(64)  NOT NULL DEFAULT '',
  affection   INT          NOT NULL DEFAULT 0,    -- 好感度 -100..100
  balance     DECIMAL(10,2) NOT NULL DEFAULT 0,    -- 欠账（元）
  blacklist   TINYINT      NOT NULL DEFAULT 0,
  pranks_done INT          NOT NULL DEFAULT 0,    -- 已完成整蛊数
  chapter     VARCHAR(32)  NOT NULL DEFAULT '第一章·熟客',
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- 事件流水：每次互动一行，用于核对"真的写进数据库了"
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_log (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  player_id       VARCHAR(64)  NOT NULL,
  turn_id         VARCHAR(64)  NOT NULL DEFAULT '',
  intent          VARCHAR(32)  NOT NULL DEFAULT '',
  action          VARCHAR(255) NOT NULL DEFAULT '',
  affection_delta INT          NOT NULL DEFAULT 0,
  balance_delta   DECIMAL(10,2) NOT NULL DEFAULT 0,
  hit_event       VARCHAR(64)  NOT NULL DEFAULT '',
  created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
