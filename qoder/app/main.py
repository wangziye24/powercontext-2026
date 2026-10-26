"""FastAPI 入口：健康检查、上传、检索、问答、文档管理。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app import db
from app.chunker import parse_and_chunk
from app.config import DB_NAME, EMBED_PROVIDER, SEEKDB_HOST, SEEKDB_PORT
from app.embed import create_embedder
from app.llm import MiniMaxChat, build_qa_messages
from app.memory import build_memory_context, get_memory
from app.schemas import (
    AskRequest,
    AskResponse,
    DeleteResponse,
    DocsResponse,
    DocItem,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    ChunkResult,
    SourceItem,
    StatsResponse,
    UploadResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="CourseNote", version="0.1.0")

# ── 全局实例（懒初始化） ──────────────────────────────────────────
_embedder = None
_chat = None


def get_embedder():
    """获取 Embedder 实例。"""
    global _embedder
    if _embedder is None:
        _embedder = create_embedder()
    return _embedder


def get_chat():
    """获取 MiniMaxChat 实例。"""
    global _chat
    if _chat is None:
        _chat = MiniMaxChat()
    return _chat


# ── 健康检查 ──────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
def health() -> dict:
    """健康检查：返回服务状态与基本配置信息。"""
    return {
        "status": "ok",
        "service": "coursenote",
        "db": f"{SEEKDB_HOST}:{SEEKDB_PORT}/{DB_NAME}",
        "embed_provider": EMBED_PROVIDER,
    }


# ── 文件上传 ──────────────────────────────────────────────────────

@app.post("/api/upload", response_model=UploadResponse)
async def upload(
    file: UploadFile = File(...),
    course: str = Form("未分类"),
    tags: str = Form(""),
) -> dict:
    """上传文档：解析 → 分块 → embedding → 写入 seekdb。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")

    # 保存临时文件
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".md", ".txt"):
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {suffix}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # 解析与分块
        chunks = parse_and_chunk(tmp_path)
        if not chunks:
            raise HTTPException(status_code=400, detail="文档解析后无有效内容")

        # Embedding
        embedder = get_embedder()
        texts = [chunk["content"] for chunk in chunks]
        vectors = embedder.embed_documents(texts)

        # 合并 chunk 和 vector
        for chunk, vec in zip(chunks, vectors):
            chunk["embedding"] = vec

        # 写入数据库
        doc_id = db.insert_doc(
            title=file.filename,
            source=file.filename,
            course=course,
            tags=tags,
        )
        chunk_count = db.insert_chunks(doc_id, chunks, course=course)

        return {
            "doc_id": doc_id,
            "chunk_count": chunk_count,
            "message": f"上传成功，共 {chunk_count} 个分块",
        }
    except db.DBError as exc:
        logger.error("数据库错误: %s", exc)
        raise HTTPException(status_code=500, detail=f"数据库错误: {exc}")
    except Exception as exc:
        logger.exception("上传失败")
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


# ── 检索 ──────────────────────────────────────────────────────────

@app.post("/api/search", response_model=SearchResponse)
def search_api(req: SearchRequest, user_id: str = "anonymous") -> dict:
    """混合检索：向量 + 全文 + 结构化过滤。"""
    try:
        embedder = get_embedder()
        query_vec = embedder.embed_query(req.query)

        results = db.search(
            query_vec=query_vec,
            query_text=req.query,
            course=req.course,
            top_k=req.top_k,
        )

        # 取回用户记忆
        memory = get_memory()
        memories = memory.recall(user_id, limit=3)
        used_memory = build_memory_context(memories) if memories else ""

        # 记录检索历史
        if memory.enabled and results:
            memory.remember(
                user_id,
                "history",
                f"检索: {req.query} (命中 {len(results)} 条)",
            )

        return {
            "query": req.query,
            "results": results,
            "total": len(results),
            "used_memory": used_memory,
        }
    except db.DBError as exc:
        logger.error("检索失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"检索失败: {exc}")
    except Exception as exc:
        logger.exception("检索异常")
        raise HTTPException(status_code=500, detail=f"检索失败: {exc}")


# ── 问答 ──────────────────────────────────────────────────────────

@app.post("/api/ask", response_model=AskResponse)
def ask_api(req: AskRequest, user_id: str = "anonymous") -> dict:
    """问答：先检索，再用 Top-K 片段作为上下文调用 LLM。"""
    try:
        # 1. 检索
        embedder = get_embedder()
        query_vec = embedder.embed_query(req.query)

        results = db.search(
            query_vec=query_vec,
            query_text=req.query,
            course=req.course,
            top_k=req.top_k,
        )

        if not results:
            return {
                "answer": "未找到相关资料片段，无法回答问题。",
                "sources": [],
                "used_memory": "",
            }

        # 2. 构建上下文
        context_parts = []
        sources = []
        for i, r in enumerate(results, start=1):
            snippet = f"[{i}] 来源：{r['title']}（{r['heading']}）\n{r['content']}"
            context_parts.append(snippet)
            sources.append({
                "title": r["title"],
                "source": r["source"],
                "chunk_id": r["chunk_id"],
            })

        context = "\n\n".join(context_parts)

        # 3. 取回用户记忆并加入上下文
        memory = get_memory()
        memories = memory.recall(user_id, limit=3)
        memory_context = build_memory_context(memories) if memories else ""
        if memory_context:
            context = memory_context + "\n\n" + context

        # 4. 调用 LLM
        chat = get_chat()
        messages = build_qa_messages(context, req.query)
        answer = chat.chat(messages)

        # 5. 记录问答历史
        if memory.enabled:
            memory.remember(
                user_id,
                "history",
                f"问答: {req.query}",
            )

        return {
            "answer": answer,
            "sources": sources,
            "used_memory": memory_context,
        }
    except db.DBError as exc:
        logger.error("问答检索失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"检索失败: {exc}")
    except Exception as exc:
        logger.exception("问答异常")
        raise HTTPException(status_code=500, detail=f"问答失败: {exc}")


# ── 文档管理 ──────────────────────────────────────────────────────

@app.get("/api/docs", response_model=DocsResponse)
def list_docs() -> dict:
    """列出所有文档。"""
    try:
        docs = db.list_docs()
        return {
            "docs": [
                {
                    "doc_id": d["doc_id"],
                    "title": d["title"],
                    "source": d["source"],
                    "course": d["course"],
                    "tags": d["tags"],
                    "chunk_cnt": d["chunk_cnt"],
                    "created_at": str(d["created_at"]),
                }
                for d in docs
            ]
        }
    except db.DBError as exc:
        logger.error("获取文档列表失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"数据库错误: {exc}")


@app.delete("/api/docs/{doc_id}", response_model=DeleteResponse)
def delete_doc(doc_id: int) -> dict:
    """删除文档及其所有分块。"""
    try:
        success = db.delete_doc(doc_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
        return {
            "success": True,
            "message": f"文档 {doc_id} 已删除",
        }
    except HTTPException:
        raise
    except db.DBError as exc:
        logger.error("删除文档失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"数据库错误: {exc}")


@app.get("/api/stats", response_model=StatsResponse)
def stats() -> dict:
    """按课程统计文档数量。"""
    try:
        by_course = db.count_by_course()
        docs = db.list_docs()
        total_chunks = sum(d["chunk_cnt"] for d in docs)
        return {
            "by_course": by_course,
            "total_docs": len(docs),
            "total_chunks": total_chunks,
        }
    except db.DBError as exc:
        logger.error("获取统计失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"数据库错误: {exc}")


# ── 前端页面 ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """返回前端页面。"""
    html_path = Path(__file__).parent.parent / "web" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>CourseNote</h1><p>前端页面未找到。</p>"
