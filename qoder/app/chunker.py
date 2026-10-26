"""文档解析与分块：支持 PDF / Markdown / TXT。

分块策略：
1. 按 heading 分组（PDF 按页，Markdown/TXT 按标题层级）
2. 每组内按最大长度切分，相邻块保留重叠
3. 每个 chunk 返回 {seq, heading, content}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict


class Chunk(TypedDict):
    """分块结果。"""
    seq: int
    heading: str
    content: str


# ── 常量 ──────────────────────────────────────────────────────────
DEFAULT_MAX_CHARS: int = 600
DEFAULT_OVERLAP: int = 80


def parse_and_chunk(
    file_path: str | Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """解析文档并分块。

    Args:
        file_path: 文档路径（支持 .pdf / .md / .txt）
        max_chars: 每块最大字符数
        overlap: 相邻块重叠字符数

    Returns:
        分块列表，每项包含 {seq, heading, content}
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        sections = _parse_pdf(path)
    elif suffix in (".md", ".txt"):
        sections = _parse_markdown(path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}（仅支持 .pdf / .md / .txt）")

    return _chunk_sections(sections, max_chars, overlap)


# ── PDF 解析 ──────────────────────────────────────────────────────

def _parse_pdf(path: Path) -> list[tuple[str, str]]:
    """解析 PDF，每页作为一个 section。

    Returns:
        [(heading, content), ...]，heading 为 "第 N 页"
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    sections: list[tuple[str, str]] = []

    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            sections.append((f"第 {i} 页", text))

    return sections


# ── Markdown 解析 ─────────────────────────────────────────────────

def _parse_markdown(path: Path) -> list[tuple[str, str]]:
    """解析 Markdown/TXT，按标题切段。

    保留标题层级，heading 形如 "第1章 概述 / 1.1 背景"。

    Returns:
        [(heading, content), ...]
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # 匹配 Markdown 标题（# 开头）或数字编号标题（如 "第1章" "1.1"）
    heading_pattern = re.compile(r"^(#{1,6}\s+.+|第[一二三四五六七八九十\d]+[章节].+|\d+(?:\.\d+)*\s+.+)$")

    sections: list[tuple[str, str]] = []
    current_heading = ""
    heading_stack: list[str] = []
    current_lines: list[str] = []

    def flush_section() -> None:
        """将当前段落加入 sections。"""
        nonlocal current_lines
        if current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                full_heading = " / ".join(heading_stack) if heading_stack else current_heading
                sections.append((full_heading, content))
            current_lines = []

    for line in lines:
        stripped = line.strip()
        if heading_pattern.match(stripped):
            flush_section()
            # 清理标题：去掉 # 前缀
            clean_heading = re.sub(r"^#+\s*", "", stripped)
            current_heading = clean_heading

            # 维护标题层级
            level = len(re.match(r"^(#+)", stripped).group(1)) if stripped.startswith("#") else 1
            # 截断栈到当前层级
            while len(heading_stack) >= level:
                heading_stack.pop()
            heading_stack.append(clean_heading)
        else:
            current_lines.append(line)

    flush_section()
    return sections


# ── 分块逻辑 ──────────────────────────────────────────────────────

def _chunk_sections(
    sections: list[tuple[str, str]],
    max_chars: int,
    overlap: int,
) -> list[Chunk]:
    """将 sections 按 max_chars 切分，保留 overlap。

    Args:
        sections: [(heading, content), ...]
        max_chars: 每块最大字符数
        overlap: 相邻块重叠字符数

    Returns:
        分块列表
    """
    chunks: list[Chunk] = []
    seq = 0

    for heading, content in sections:
        if len(content) <= max_chars:
            chunks.append({"seq": seq, "heading": heading, "content": content})
            seq += 1
        else:
            # 按 max_chars 切分，保留 overlap
            start = 0
            while start < len(content):
                end = start + max_chars
                chunk_text = content[start:end]
                chunks.append({"seq": seq, "heading": heading, "content": chunk_text})
                seq += 1
                # 下一块从 end - overlap 开始
                start = end - overlap
                if start >= len(content):
                    break

    # 重新编号 seq
    for i, chunk in enumerate(chunks):
        chunk["seq"] = i

    return chunks
