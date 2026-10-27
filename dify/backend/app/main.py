"""午夜便利店 · 阿夜的账本 —— 侧车服务（sidecar）。

为什么要有侧车：Dify 只有 HTTP Request 节点，无法直接连 MySQL 协议。
由本服务承接对 seekdb 的访问，并把检索结果整理成可直接塞进 prompt 的文本。

端点：
- GET  /health          健康检查（seekdb / PowerContext 连通状态）
- POST /lore/search     混合检索（向量 + 全文 + 结构化过滤）
- POST /lore            新增一条设定（自动 embedding）
- GET  /state           读取玩家状态
- POST /state           写入玩家状态 + 追加事件流水
- GET  /event_log       查询事件流水
- POST /admin/reindex   换 embedding provider 后重算全表向量
- POST /admin/seed      幂等灌入种子数据

只用标准库 + vendor 的 PyMySQL，无需 pip install。
启动：``python3 -m app.main``（默认监听 0.0.0.0:8080）
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import seed as seed_mod
from .config import settings
from .db import cursor, fetch_all, fetch_one
from .embed import embed_texts, provider_name, vec_literal
from .search import search as do_search
from .state import get_state, list_events, save_state

Handler = Callable[[dict], Any]


def _render_context(hits: list[dict]) -> str:
    """把命中结果渲染成可直接塞进 prompt 的文本。"""
    if not hits:
        return "（本次没有检索到相关素材，请用阿夜的人设自由发挥，但不要编造具体商品与价格。）"
    return "\n".join(
        f"- [{h.get('kind')}] {h.get('title')}：{h.get('content')}" for h in hits
    )


# --------------------------------------------------------------------------- 路由实现
def health(_: dict) -> dict:
    info: dict[str, Any] = {"status": "ok", "embed_provider": provider_name()}
    try:
        with cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            info["seekdb"] = "ok"
            row = fetch_one(cur, "SELECT COUNT(*) AS c FROM lore")
            info["lore_rows"] = int(row["c"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        info["seekdb"] = f"error: {exc}"
    try:
        req = urllib.request.Request(
            settings.powercontext_base_url.rstrip("/") + "/api/v1/system/health",
            headers=(
                {"X-API-Key": settings.powercontext_api_key}
                if settings.powercontext_api_key
                else {}
            ),
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            info["powercontext"] = "ok" if resp.status == 200 else f"status={resp.status}"
    except Exception as exc:  # noqa: BLE001
        info["powercontext"] = f"unreachable: {exc}"
    return info


def _as_int(value, default=None):
    """DSL 里变量插值后都是字符串，这里统一转成 int（失败则用默认值）。"""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def lore_search(body: dict) -> dict:
    with cursor() as cur:
        result = do_search(
            cur,
            query=str(body.get("query", "")),
            kinds=body.get("kinds"),
            tags=body.get("tags"),
            min_affection=_as_int(body.get("min_affection")),
            max_affection=_as_int(body.get("max_affection")),
            min_pranks=_as_int(body.get("min_pranks")),
            top_k=_as_int(body.get("top_k")),
        )
    result["context"] = _render_context(result["hits"])
    return result


def lore_create(body: dict) -> dict:
    kind = str(body.get("kind", "")).strip()
    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    if not (kind and title and content):
        return {"ok": False, "error": "kind / title / content 均不能为空"}
    vec = embed_texts([f"{title}\n{content}"])[0]
    with cursor() as cur:
        row = fetch_one(cur, "SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM lore")
        nid = int(row["nid"])
        cur.execute(
            "INSERT INTO lore (id, kind, title, content, tag, weight, cond_min, cond_max, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                nid,
                kind,
                title,
                content,
                str(body.get("tag", "")),
                int(body.get("weight", 0)),
                int(body.get("cond_min", -999)),
                int(body.get("cond_max", 999)),
                vec_literal(vec),
            ),
        )
    return {"ok": True, "id": nid}


def read_state(query: dict) -> dict:
    player_id = (query.get("player_id") or [""])[0]
    if not player_id:
        return {"ok": False, "error": "缺少 player_id"}
    with cursor() as cur:
        return get_state(cur, player_id)


def write_state(body: dict) -> dict:
    player_id = str(body.get("player_id", "")).strip()
    if not player_id:
        return {"ok": False, "error": "缺少 player_id"}
    payload = {
        k: v
        for k, v in body.items()
        if v is not None and k not in ("event", "player_id")
    }
    with cursor() as cur:
        return save_state(cur, player_id, {**payload, "event": body.get("event")})


def event_log(query: dict) -> dict:
    player_id = (query.get("player_id") or [""])[0]
    limit = int((query.get("limit") or ["20"])[0])
    with cursor() as cur:
        return {"player_id": player_id, "events": list_events(cur, player_id, limit)}


def reindex(_: dict) -> dict:
    with cursor() as cur:
        rows = fetch_all(cur, "SELECT id, title, content FROM lore ORDER BY id")
        if not rows:
            return {"ok": True, "updated": 0, "embed_provider": provider_name()}
        vectors = embed_texts([f"{r['title']}\n{r['content']}" for r in rows])
        for r, vec in zip(rows, vectors):
            cur.execute("UPDATE lore SET embedding = %s WHERE id = %s", (vec_literal(vec), r["id"]))
    return {"ok": True, "updated": len(rows), "embed_provider": provider_name()}


def seed(_: dict) -> dict:
    return seed_mod.seed_all()


def openai_embeddings(body: dict) -> dict:
    """OpenAI 兼容的 /v1/embeddings。

    背景：MiniMax 的 embeddings 接口不是 OpenAI 兼容格式（用 ``texts`` 而非 ``input``，
    返回 ``vectors`` 而非 ``data``），而 PowerContext / Dify 的 OpenAI 兼容客户端只认 OpenAI 格式。
    这里做一层薄代理，把 MiniMax embo-01 包成 OpenAI 形状，任何 OpenAI 兼容客户端都能直接用。
    """
    raw = body.get("input")
    if raw is None:
        raw = body.get("texts") or []
    if isinstance(raw, str):
        raw = [raw]
    texts = [str(t) for t in raw]
    if not texts:
        return {"error": {"message": "input 不能为空", "type": "invalid_request_error"}}
    vectors = embed_texts(texts)
    return {
        "object": "list",
        "model": body.get("model") or settings.minimax_embed_model,
        "data": [
            {"object": "embedding", "index": i, "embedding": vec}
            for i, vec in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": sum(len(t) for t in texts), "total_tokens": sum(len(t) for t in texts)},
    }


ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/health"): health,
    ("POST", "/lore/search"): lore_search,
    ("POST", "/lore"): lore_create,
    ("GET", "/state"): read_state,
    ("POST", "/state"): write_state,
    ("GET", "/event_log"): event_log,
    ("POST", "/admin/reindex"): reindex,
    ("POST", "/admin/seed"): seed,
    ("POST", "/v1/embeddings"): openai_embeddings,
}


class Handler_(BaseHTTPRequestHandler):
    server_version = "AyeSidecar/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 简洁日志
        print(f"[sidecar] {self.address_string()} {fmt % args}", flush=True)

    def _respond(self, code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        handler = ROUTES.get((method, parsed.path))
        if handler is None:
            self._respond(404, {"ok": False, "error": f"未知路由 {method} {parsed.path}"})
            return
        body: dict = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            raw = self.rfile.read(length).decode("utf-8")
            try:
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError as exc:
                self._respond(400, {"ok": False, "error": f"JSON 解析失败: {exc}"})
                return
        query = parse_qs(parsed.query)
        try:
            self._respond(200, handler({**query, **body}))
        except Exception as exc:  # noqa: BLE001
            self._respond(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def main() -> None:
    host = os.getenv("SIDECAR_HOST", "0.0.0.0")
    port = int(os.getenv("SIDECAR_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), Handler_)
    print(f"[sidecar] listening on http://{host}:{port}", flush=True)
    print(f"[sidecar] seekdb={settings.db_host}:{settings.db_port}/{settings.db_name} "
          f"embed_provider={settings.embed_provider}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
