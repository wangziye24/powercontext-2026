"""Pydantic 请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── 请求模型 ──────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    """检索请求。"""
    query: str = Field(..., description="查询文本")
    course: str | None = Field(None, description="课程过滤")
    top_k: int = Field(10, ge=1, le=100, description="返回数量")


class AskRequest(BaseModel):
    """问答请求。"""
    query: str = Field(..., description="问题")
    course: str | None = Field(None, description="课程过滤")
    top_k: int = Field(5, ge=1, le=20, description="检索数量")


# ── 响应模型 ──────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str
    service: str
    db: str
    embed_provider: str


class ChunkResult(BaseModel):
    """检索结果中的单个片段。"""
    chunk_id: int
    doc_id: int
    heading: str
    content: str
    title: str
    source: str
    course: str
    dist: float = Field(description="向量距离（越小越相似）")
    kw_score: float = Field(description="关键词匹配得分")
    kw_score_norm: float = Field(description="归一化后的关键词得分")
    score: float = Field(description="混合检索最终得分")


class SearchResponse(BaseModel):
    """检索响应。"""
    query: str
    results: list[ChunkResult]
    total: int
    used_memory: str = Field("", description="使用的用户记忆上下文")


class SourceItem(BaseModel):
    """引用来源。"""
    title: str
    source: str
    chunk_id: int


class AskResponse(BaseModel):
    """问答响应。"""
    answer: str
    sources: list[SourceItem]
    used_memory: str = Field("", description="使用的用户记忆上下文")


class DocItem(BaseModel):
    """文档信息。"""
    doc_id: int
    title: str
    source: str
    course: str
    tags: str
    chunk_cnt: int
    created_at: str


class DocsResponse(BaseModel):
    """文档列表响应。"""
    docs: list[DocItem]


class StatsResponse(BaseModel):
    """统计响应。"""
    by_course: dict[str, int]
    total_docs: int
    total_chunks: int


class UploadResponse(BaseModel):
    """上传响应。"""
    doc_id: int
    chunk_count: int
    message: str


class DeleteResponse(BaseModel):
    """删除响应。"""
    success: bool
    message: str
