"""chunker 单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.chunker import parse_and_chunk


class TestMarkdownChunker:
    """Markdown 分块测试。"""

    def test_chunk_count_and_size(self) -> None:
        """验证分块数量、每块 <= 600 字符、相邻块有重叠。"""
        md_content = """# 第1章 机器学习基础

机器学习是人工智能的一个分支，它使计算机系统能够从数据中学习并改进性能，而无需进行明确的编程。

## 1.1 监督学习

监督学习是最常见的机器学习类型之一。在监督学习中，算法从标记训练数据中学习，其中每个样本都包含输入特征和对应的目标标签。常见的监督学习任务包括分类和回归。

分类任务的目标是预测离散类别标签，例如判断一封邮件是否为垃圾邮件。回归任务则预测连续数值，例如房价预测。

## 1.2 无监督学习

无监督学习处理的是未标记的数据，算法需要自行发现数据中的结构和模式。聚类是无监督学习的典型应用，它将相似的数据点分到同一组。

降维也是无监督学习的重要技术，它可以将高维数据映射到低维空间，同时保留数据的主要特征。PCA（主成分分析）是最经典的降维算法之一。

# 第2章 模型评估

模型评估是机器学习流程中至关重要的环节。我们需要使用合适的评估指标来衡量模型的性能，并通过交叉验证等方法来估计模型的泛化能力。

## 2.1 过拟合与欠拟合

过拟合是指模型在训练数据上表现很好，但在新数据上表现较差的现象。这通常是因为模型过于复杂，学习到了训练数据中的噪声和细节。

缓解过拟合的常用方法包括：正则化（L1/L2）、Dropout、早停法、数据增强等。正则化通过在损失函数中添加惩罚项来限制模型复杂度。Dropout 则在训练时随机丢弃一部分神经元，防止模型过度依赖某些特定特征。

欠拟合则相反，模型无法很好地拟合训练数据。解决方法包括增加模型复杂度、减少正则化强度、增加训练轮次等。
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md_content)
            f.flush()
            path = Path(f.name)

        try:
            chunks = parse_and_chunk(path, max_chars=600, overlap=80)

            # 验证分块数量 > 1
            assert len(chunks) > 1, f"期望多个分块，实际得到 {len(chunks)}"

            # 验证每块 <= 600 字符
            for chunk in chunks:
                assert len(chunk["content"]) <= 600, (
                    f"分块 {chunk['seq']} 长度为 {len(chunk['content'])}，超过 600"
                )

            # 验证相邻块有重叠（通过检查内容连续性）
            # 如果分块正确实现，相邻块的 heading 应该相同或相关
            for i in range(len(chunks) - 1):
                # 至少有些块应该共享相同的 heading
                if chunks[i]["heading"] == chunks[i + 1]["heading"]:
                    # 检查内容是否有重叠（简单验证：后一块的开头应该出现在前一块中）
                    prev_content = chunks[i]["content"]
                    next_content = chunks[i + 1]["content"]
                    # 如果后一块长度 >= 80，检查前一块的最后 80 字符是否与后一块的开头匹配
                    if len(next_content) >= 80 and len(prev_content) >= 80:
                        overlap_text = prev_content[-80:]
                        assert next_content.startswith(overlap_text) or overlap_text in next_content[:160], (
                            f"分块 {i} 和 {i+1} 之间没有检测到重叠"
                        )
                    break  # 只需要验证一对相邻块即可

            # 验证 seq 编号连续
            for i, chunk in enumerate(chunks):
                assert chunk["seq"] == i, f"期望 seq={i}，实际 {chunk['seq']}"

        finally:
            path.unlink()

    def test_heading_hierarchy(self) -> None:
        """验证标题层级保留。"""
        md_content = """# 第1章 概述

这是第一章的内容。

## 1.1 背景

这是背景介绍。

### 1.1.1 历史

这是历史部分。
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md_content)
            f.flush()
            path = Path(f.name)

        try:
            chunks = parse_and_chunk(path)

            # 验证标题层级
            headings = [chunk["heading"] for chunk in chunks]
            assert any("第1章 概述" in h for h in headings), f"未找到 '第1章 概述'，实际: {headings}"
            assert any("背景" in h for h in headings), f"未找到 '背景'，实际: {headings}"
            assert any("历史" in h for h in headings), f"未找到 '历史'，实际: {headings}"

        finally:
            path.unlink()

    def test_empty_file(self) -> None:
        """空文件返回空列表。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            chunks = parse_and_chunk(path)
            assert chunks == []
        finally:
            path.unlink()

    def test_unsupported_format(self) -> None:
        """不支持的格式抛出异常。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".docx", delete=False, encoding="utf-8") as f:
            f.write("test")
            f.flush()
            path = Path(f.name)

        try:
            import pytest
            with pytest.raises(ValueError, match="不支持的文件格式"):
                parse_and_chunk(path)
        finally:
            path.unlink()
