"""灌入示例文档与分块到 seekdb。

内置 3 篇示例资料：
1. 《机器学习复习笔记.md》
2. 《分布式系统讲义.md》
3. 《OceanBase 全文索引优化.md》
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from pathlib import Path

import httpx

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.chunker import parse_and_chunk
from app.config import EMBED_DIM
from app.embed import create_embedder

# ── 示例文档 ──────────────────────────────────────────────────────

ML_NOTES = """# 机器学习复习笔记

## 第1章 机器学习基础

机器学习是人工智能的一个分支，它使计算机系统能够从数据中学习并改进性能，而无需进行明确的编程。根据学习方式的不同，机器学习可以分为监督学习、无监督学习和强化学习。

### 1.1 监督学习

监督学习是最常见的机器学习类型。在监督学习中，算法从标记训练数据中学习，其中每个样本都包含输入特征和对应的目标标签。常见的监督学习任务包括分类和回归。

分类任务的目标是预测离散类别标签，例如判断一封邮件是否为垃圾邮件。回归任务则预测连续数值，例如房价预测。

### 1.2 无监督学习

无监督学习处理的是未标记的数据，算法需要自行发现数据中的结构和模式。聚类是无监督学习的典型应用，它将相似的数据点分到同一组。

## 第2章 模型评估

模型评估是机器学习流程中至关重要的环节。我们需要使用合适的评估指标来衡量模型的性能，并通过交叉验证等方法来估计模型的泛化能力。

### 2.1 过拟合与欠拟合

过拟合是指模型在训练数据上表现很好，但在新数据上表现较差的现象。这通常是因为模型过于复杂，学习到了训练数据中的噪声和细节，而不是真正的规律。

欠拟合则相反，模型无法很好地拟合训练数据，在训练集和测试集上表现都较差。这通常是因为模型过于简单，无法捕捉数据中的模式。

### 2.2 缓解过拟合的方法

缓解过拟合的常用方法包括：

1. **正则化（Regularization）**：通过在损失函数中添加惩罚项来限制模型复杂度。L1 正则化（Lasso）会产生稀疏解，L2 正则化（Ridge）会让权重趋向较小值。

2. **Dropout**：在训练时随机丢弃一部分神经元，防止模型过度依赖某些特定特征。Dropout 可以看作是一种集成学习方法，每次训练的都是不同的子网络。

3. **早停法（Early Stopping）**：在验证集性能不再提升时停止训练，避免模型过度拟合训练数据。

4. **数据增强（Data Augmentation）**：通过对训练数据进行变换（如旋转、翻转、裁剪）来增加数据多样性，提高模型泛化能力。

5. **交叉验证**：使用 K 折交叉验证来更准确地估计模型性能，避免过拟合特定的训练/测试划分。

## 第3章 常用算法

### 3.1 线性回归

线性回归是最基础的回归算法，假设目标值是输入特征的线性组合。模型通过最小化均方误差来学习最优权重。

### 3.2 逻辑回归

逻辑回归用于二分类任务，通过 Sigmoid 函数将线性输出映射到 [0, 1] 区间，表示属于正类的概率。

### 3.3 决策树

决策树通过递归地将特征空间划分为矩形区域来进行预测。每个内部节点表示一个特征上的判断条件，叶节点表示预测结果。

### 3.4 支持向量机

支持向量机（SVM）寻找一个最优超平面来分隔不同类别的数据。对于非线性可分的情况，可以使用核技巧将数据映射到高维空间。
"""

DS_NOTES = """# 分布式系统讲义

## 第1章 分布式系统概述

分布式系统是由多台独立计算机组成的集合，这些计算机通过网络通信和协调来完成共同的任务。对用户而言，整个系统表现为一个单一的整体。

### 1.1 分布式系统的优势

分布式系统相比集中式系统具有以下优势：
- **可扩展性**：可以通过增加节点来提升系统容量
- **可靠性**：部分节点故障不会导致整个系统不可用
- **性能**：可以并行处理请求，降低响应延迟

### 1.2 分布式系统的挑战

分布式系统面临的主要挑战包括：
- 网络延迟和分区
- 节点故障
- 数据一致性
- 时钟同步

## 第2章 一致性模型

一致性是分布式系统中最核心的问题之一。由于网络延迟和节点故障的存在，保证所有节点看到相同的数据视图是非常困难的。

### 2.1 CAP 定理

CAP 定理指出，一个分布式系统最多只能同时满足以下三个特性中的两个：
- **C（Consistency）**：一致性，所有节点看到相同的数据
- **A（Availability）**：可用性，每个请求都能得到响应
- **P（Partition tolerance）**：分区容忍性，网络分区时系统仍能运行

由于网络分区在分布式环境中不可避免，实际上我们需要在一致性和可用性之间做权衡。

### 2.2 BASE 理论

BASE 是对 CAP 中一致性和可用性权衡的结果，其核心思想是：
- **Basically Available**：基本可用
- **Soft state**：软状态
- **Eventually consistent**：最终一致性

### 2.3 向量时钟

向量时钟（Vector Clock）是一种用于检测因果关系和冲突的技术。每个节点维护一个向量，记录从每个其他节点接收到的最新消息的版本号。

向量时钟可以检测事件之间的因果顺序，但无法解决并发写入导致的冲突。冲突需要应用层来处理。

## 第3章 共识算法

共识算法用于在分布式系统中让多个节点就某个值达成一致。

### 3.1 Paxos

Paxos 是最早的共识算法之一，由 Leslie Lamport 提出。它通过提案（Proposal）和投票（Vote）机制来达成一致。

### 3.2 Raft

Raft 是一种更易理解的共识算法，它将共识问题分解为领导者选举、日志复制和安全性三个子问题。
"""

OB_NOTES = """# OceanBase 全文索引优化

## 第1章 全文检索概述

全文检索（Full-Text Search，FTS）是一种在文本数据中查找关键词的技术。与传统的关系型查询不同，全文检索需要处理自然语言的复杂性，如词干提取、同义词、相关性排序等。

### 1.1 全文检索的应用场景

全文检索广泛应用于：
- 搜索引擎
- 文档管理系统
- 知识库
- 日志分析

### 1.2 全文检索的挑战

全文检索面临的主要挑战包括：
- 中文分词
- 索引构建性能
- 查询响应时间
- 索引存储空间

## 第2章 倒排索引

倒排索引（Inverted Index）是全文检索的核心数据结构。它将每个词映射到包含该词的文档列表。

### 2.1 倒排索引结构

倒排索引的基本结构包括：
- **词项（Term）**：分词后的单词
- **文档频率（DF）**：包含该词的文档数量
- **倒排列表（Posting List）**：包含该词的所有文档 ID 及其位置信息

### 2.2 倒排索引的压缩

为了减少存储空间和提高查询效率，倒排列表通常使用压缩编码：
- **变长编码（VByte）**：用变长字节表示整数
- **增量编码**：存储文档 ID 的差值而非绝对值
- **位图压缩**：如 WAH、Roaring Bitmap

## 第3章 索引构建性能优化

索引构建是全文检索系统中的关键环节，其性能直接影响系统的可用性。

### 3.1 批量构建 vs 实时构建

- **批量构建**：适合离线场景，可以充分利用批量 I/O 和并行计算
- **实时构建**：需要支持增量更新，保证新数据立即可检索

### 3.2 并行构建策略

OceanBase 在索引构建时采用以下并行策略：
- 分片并行：将数据按分片分配给不同的构建线程
- 流水线处理：分词、编码、写入三个阶段流水线执行
- 内存管理：使用内存池避免频繁的内存分配和释放

### 3.3 性能调优建议

1. 合理设置分片数，避免单个分片过大
2. 使用批量插入而非单条插入
3. 调整内存参数，保证足够的构建缓冲区
4. 监控磁盘 I/O，避免成为瓶颈
"""

SEED_DOCS = [
    {
        "filename": "机器学习复习笔记.md",
        "content": ML_NOTES,
        "course": "机器学习",
        "tags": "过拟合,正则化,Dropout",
    },
    {
        "filename": "分布式系统讲义.md",
        "content": DS_NOTES,
        "course": "分布式系统",
        "tags": "CAP,一致性,向量时钟",
    },
    {
        "filename": "OceanBase全文索引优化.md",
        "content": OB_NOTES,
        "course": "数据库",
        "tags": "FTS,倒排索引,构建性能",
    },
]


def _cleanup_existing(titles: list[str]) -> None:
    """删除同名的历史文档，避免重复执行 seed 脚本产生重复内容。"""
    for title in titles:
        try:
            db.delete_doc_by_title(title)
        except Exception as exc:  # 清理失败不阻塞灌入
            print(f"  清理旧文档失败({title}): {exc}")


async def upload_via_api(doc: dict) -> dict:
    """通过 API 上传文档。"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": (doc["filename"], doc["content"].encode("utf-8"), "text/markdown")}
        data = {"course": doc["course"], "tags": doc["tags"]}
        resp = await client.post("http://127.0.0.1:8080/api/upload", files=files, data=data)
        resp.raise_for_status()
        return resp.json()


def upload_directly(doc: dict) -> dict:
    """直接写入数据库（不通过 API）。"""
    from app import db

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(doc["content"])
        tmp_path = Path(f.name)

    try:
        # 解析与分块
        chunks = parse_and_chunk(tmp_path)
        if not chunks:
            return {"error": "文档解析后无有效内容"}

        # Embedding
        embedder = create_embedder()
        texts = [chunk["content"] for chunk in chunks]
        vectors = embedder.embed_documents(texts)

        # 合并 chunk 和 vector
        for chunk, vec in zip(chunks, vectors):
            chunk["embedding"] = vec

        # 写入数据库
        doc_id = db.insert_doc(
            title=doc["filename"],
            source=doc["filename"],
            course=doc["course"],
            tags=doc["tags"],
        )
        chunk_count = db.insert_chunks(doc_id, chunks, course=doc["course"])

        return {
            "doc_id": doc_id,
            "chunk_count": chunk_count,
            "embedding_dim": len(vectors[0]) if vectors else 0,
        }
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    """灌入示例文档。"""
    print("=" * 60)
    print("CourseNote 种子数据灌入")
    print("=" * 60)
    print()

    # 先建表
    print("正在建表...")
    _cleanup_existing([d.get("title") or d.get("filename") for d in SEED_DOCS])
    try:
        from app import db
        db.create_tables()
        print("建表完成")
    except Exception as exc:
        print(f"建表失败: {exc}")
        print("请确保 seekdb 已启动且配置正确")
        sys.exit(1)

    print()
    print(f"预期 embedding 维度: {EMBED_DIM}")
    print()

    # 灌入文档
    for i, doc in enumerate(SEED_DOCS, start=1):
        print(f"[{i}/{len(SEED_DOCS)}] 灌入: {doc['filename']} (课程: {doc['course']})")
        try:
            result = upload_directly(doc)
            if "error" in result:
                print(f"  错误: {result['error']}")
            else:
                print(f"  doc_id: {result['doc_id']}")
                print(f"  分块数: {result['chunk_count']}")
                print(f"  embedding 维度: {result['embedding_dim']}")
                if result['embedding_dim'] != EMBED_DIM:
                    print(f"  警告: 维度不匹配！预期 {EMBED_DIM}")
        except Exception as exc:
            print(f"  失败: {exc}")
        print()

    print("=" * 60)
    print("灌入完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
