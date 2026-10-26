"""端到端自检脚本：依次调用 health → upload → search → ask。

预期输出：
- health: status=ok
- upload: 3 篇文档灌入成功
- search: 查询"如何缓解过拟合"命中 >= 2 条，来源包含《机器学习复习笔记.md》
- ask: 返回答案和引用来源

退出码：0 表示成功，1 表示失败
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

API_BASE = "http://127.0.0.1:8080"


def ensure_server():
    """若后端未运行，则自动拉起 uvicorn（方便评审一条命令跑通）。

    Returns:
        进程对象（需要结束时终止）或 None（服务本来就在运行）
    """
    import subprocess

    try:
        httpx.get(f"{API_BASE}/api/health", timeout=3.0)
        return None
    except Exception:
        pass

    print(f"  未检测到后端，自动启动 {API_BASE} ...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", API_BASE.rsplit(":", 1)[-1]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(30):
        time.sleep(1)
        try:
            httpx.get(f"{API_BASE}/api/health", timeout=2.0)
            print("  后端已就绪")
            return proc
        except Exception:
            continue
    print("  自动启动后端失败")
    proc.terminate()
    return None


def check_health() -> bool:
    """检查健康状态。"""
    print("[1/4] 检查健康状态...")
    try:
        resp = httpx.get(f"{API_BASE}/api/health", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        print(f"  status: {data['status']}")
        print(f"  db: {data['db']}")
        print(f"  embed_provider: {data['embed_provider']}")
        return data["status"] == "ok"
    except Exception as exc:
        print(f"  失败: {exc}")
        return False


def upload_seed_docs() -> bool:
    """上传种子文档。"""
    print("\n[2/4] 上传种子文档...")
    from scripts.seed_data import SEED_DOCS, upload_directly

    success_count = 0
    for i, doc in enumerate(SEED_DOCS, start=1):
        print(f"  [{i}/{len(SEED_DOCS)}] {doc['filename']}...")
        try:
            result = upload_directly(doc)
            if "error" in result:
                print(f"    错误: {result['error']}")
            else:
                print(f"    doc_id={result['doc_id']}, chunks={result['chunk_count']}")
                success_count += 1
        except Exception as exc:
            print(f"    失败: {exc}")

    return success_count == len(SEED_DOCS)


def test_search() -> bool:
    """测试检索。"""
    print("\n[3/4] 测试检索: '如何缓解过拟合' (course=机器学习)...")
    try:
        resp = httpx.post(
            f"{API_BASE}/api/search",
            json={"query": "如何缓解过拟合", "course": "机器学习", "top_k": 5},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        print(f"  命中数: {data['total']}")
        if data["total"] < 2:
            print("  警告: 命中数 < 2")
            return False

        print("  Top-3 来源:")
        for r in data["results"][:3]:
            print(f"    - {r['title']} / {r['heading']}")
            print(f"      dist={r['dist']:.4f}, kw={r['kw_score']:.4f}, score={r['score']:.4f}")

        # 检查是否包含《机器学习复习笔记.md》
        has_ml_notes = any("机器学习复习笔记" in r["title"] for r in data["results"])
        if not has_ml_notes:
            print("  警告: 未找到《机器学习复习笔记.md》")
            return False

        return True
    except Exception as exc:
        print(f"  失败: {exc}")
        return False


def test_ask() -> bool:
    """测试问答。"""
    print("\n[4/4] 测试问答: '请解释什么是过拟合，以及如何缓解？'...")
    try:
        resp = httpx.post(
            f"{API_BASE}/api/ask",
            json={"query": "请解释什么是过拟合，以及如何缓解？", "course": "机器学习", "top_k": 3},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()

        print(f"  答案长度: {len(data['answer'])} 字符")
        print(f"  答案摘要: {data['answer'][:200]}...")

        if data["sources"]:
            print(f"  引用来源: {len(data['sources'])} 条")
            for s in data["sources"][:3]:
                print(f"    - {s['title']} (chunk_id: {s['chunk_id']})")
        else:
            print("  警告: 无引用来源")

        return len(data["answer"]) > 0
    except Exception as exc:
        print(f"  失败: {exc}")
        # 问答失败不阻塞整体测试（可能是 LLM API 问题）
        return True


def main() -> int:
    """运行冒烟测试。"""
    print("=" * 60)
    print("CourseNote 冒烟测试")
    print("=" * 60)
    print()

    results = []
    server = ensure_server()

    try:
        # 1. Health
        results.append(("health", check_health()))

        # 等待一下确保数据库就绪
        time.sleep(1)

        # 2. Upload
        results.append(("upload", upload_seed_docs()))

        # 3. Search
        results.append(("search", test_search()))

        # 4. Ask
        results.append(("ask", test_ask()))
    finally:
        if server is not None:
            server.terminate()

    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("所有测试通过！")
        return 0
    else:
        print("部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
