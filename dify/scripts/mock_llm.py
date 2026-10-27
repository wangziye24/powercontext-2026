#!/usr/bin/env python3
"""离线自检用的 Mock 模型服务（OpenAI 兼容，零依赖，仅标准库）。

用途：
    评委/复现者如果**没有可用的模型 Key**（或 Key 没余额、被限流），
    仍然可以完整验证「Dify ↔ 侧车 ↔ seekdb ↔ PowerContext」这条链路是否打通。
    本服务实现 Dify 与 PowerContext 需要的两个 OpenAI 兼容端点：
      POST /v1/chat/completions   —— 按 prompt 内容返回符合工作流要求的 JSON
      POST /v1/embeddings         —— 返回确定性 1536 维向量（与 seed 的本地向量同构）

启动：
    python3 dify/scripts/mock_llm.py            # 监听 0.0.0.0:8099
    DIFY_CONSOLE=... DIFY_EMAIL=... DIFY_PASSWORD=... \
        python3 dify/scripts/dify_ops.py mock-credential --endpoint http://host.docker.internal:8099/v1

注意：它**不是**作品的一部分，只是一个离线自检工具；正式体验请接真实模型。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.getenv("MOCK_LLM_HOST", "0.0.0.0")
PORT = int(os.getenv("MOCK_LLM_PORT", "8099"))
DIM = int(os.getenv("MOCK_LLM_DIM", "1536"))
MODEL = os.getenv("MOCK_LLM_MODEL", "MiniMax-M2.5")

# 记忆召回节点会问"玩家喜欢喝什么"之类的查询，这里准备几个可预期的答案
_SNIPPETS = {
    "关东煮": "有，关东煮拼盘18元，萝卜记得蘸酱油。（离线自检模式）",
    "薄荷糖": "薄荷糖2元一盒，就在收银台旁边——最容易被整蛊的那一格。（离线自检模式）",
    "咖啡": "罐装美式5元，周三买二送一。（离线自检模式）",
}


def _local_vector(text: str) -> list[float]:
    """与 backend/app/embed.py 相同的确定性哈希向量（同构，便于离线验证检索链路）。"""
    text = (text or "").lower().strip()
    tokens = re.findall(r"[a-z0-9_]+|[一-鿿]", text)
    bigrams = [a + b for a, b in zip(tokens, tokens[1:]) if len(a) == len(b)]
    vec = [0.0] * DIM
    for tok in tokens + bigrams:
        h = hashlib.md5(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % DIM
        vec[idx] += 1.0 if h[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _chat_reply(messages: list[dict]) -> str:
    prompt = "\n".join(str(m.get("content", "")) for m in messages)
    if "affection_delta" in prompt:  # 阿夜台词节点
        reply = "（离线自检模式）记着呢，你以为我会忘？"
        for key, text in _SNIPPETS.items():
            if key in prompt:
                reply = text
                break
        payload = {
            "reply": reply,
            "affection_delta": 1,
            "balance_delta": 0,
            "memory_item": "[互动] 离线自检模式的一轮对话",
            "hit_event": "",
            "action": "离线自检",
            "prank_done": "0",
        }
        return json.dumps(payload, ensure_ascii=False)
    if '"intent"' in prompt:  # 意图判定节点
        return json.dumps(
            {
                "intent": "chat",
                "target": "离线自检",
                "is_prank": False,
                "is_confront": False,
            },
            ensure_ascii=False,
        )
    return "（离线自检模式）收到。"


class Handler(BaseHTTPRequestHandler):
    server_version = "MockLLM/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 简洁日志
        print(f"[mock-llm] {fmt % args}", flush=True)

    def _send_stream(self, text: str, model: str) -> None:
        """Dify 的 LLM 节点默认用流式请求，这里按 SSE 返回。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        chunk = {
            "id": "mock-chatcmpl",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        done = {
            "id": "mock-chatcmpl",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(done, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path.endswith("/embeddings"):
            raw = body.get("input") or body.get("texts") or []
            if isinstance(raw, str):
                raw = [raw]
            vectors = [_local_vector(str(t)) for t in raw]
            self._send(
                200,
                {
                    "object": "list",
                    "model": body.get("model") or MODEL,
                    "data": [
                        {"object": "embedding", "index": i, "embedding": v}
                        for i, v in enumerate(vectors)
                    ],
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )
            return
        if self.path.endswith("/chat/completions"):
            text = _chat_reply(body.get("messages") or [])
            if body.get("stream"):
                self._send_stream(text, body.get("model") or MODEL)
                return
            self._send(
                200,
                {
                    "id": "mock-chatcmpl",
                    "object": "chat.completion",
                    "model": body.get("model") or MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
            return
        self._send(404, {"error": {"message": f"unsupported path {self.path}"}})

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "ok", "model": MODEL, "dim": DIM})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[mock-llm] listening on http://{HOST}:{PORT}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
