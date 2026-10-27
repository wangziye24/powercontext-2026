#!/usr/bin/env python3
"""Dify 控制台 API 运维脚本（导入 / 导出 DSL、跑通会话、拉节点执行记录）。

用途：
1. 无浏览器环境下导入本作品的 DSL；
2. 导入后**用 Dify 自己导出的 YAML 覆盖交付文件**（保证提交物与 Dify 1.18 完全一致）；
3. 建应用 API Key，用 Service API 跑一遍「验证样例」；
4. 拉取 workflow run 的节点执行记录，作为「Dify 承担核心编排」的证据。

用法：
    export DIFY_CONSOLE=http://127.0.0.1:5001
    export DIFY_EMAIL=nightstore@local
    export DIFY_PASSWORD=nightstore
    python3 dify/scripts/dify_ops.py import
    python3 dify/scripts/dify_ops.py export --app-id <id> --out dify/dsl/aye-ledger-chatflow.yml
    python3 dify/scripts/dify_ops.py chat --query "关东煮还有吗" --player-id player_demo
    python3 dify/scripts/dify_ops.py trace --app-id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONSOLE = os.getenv("DIFY_CONSOLE", "http://127.0.0.1:5001").rstrip("/")
EMAIL = os.getenv("DIFY_EMAIL", "nightstore@local")
PASSWORD = os.getenv("DIFY_PASSWORD", "nightstore")
DSL_PATH = pathlib.Path(__file__).resolve().parents[1] / "dsl" / "aye-ledger-chatflow.yml"


class Console:
    """极简 Dify 控制台客户端（cookie + CSRF）。"""

    def __init__(self) -> None:
        import http.cookiejar

        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def _cookies(self) -> dict:
        return {c.name: c.value for c in self.jar}

    def _csrf(self) -> str:
        for name, value in self._cookies().items():
            if "csrf" in name.lower():
                return value
        return ""

    def _req(self, method: str, path: str, data=None, headers=None, raw=False):
        url = CONSOLE + path
        body = None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        csrf = self._csrf()
        if csrf:
            hdrs["X-CSRF-Token"] = csrf
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=300) as resp:
                payload = resp.read()
                if raw:
                    return payload
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype:
                    return json.loads(payload.decode("utf-8"))
                return payload.decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:600]}") from None

    def init_validate(self) -> None:
        """Dify 1.18 的初始化校验（需要 .env 里的 INIT_PASSWORD）。"""
        init_password = os.getenv("DIFY_INIT_PASSWORD", "nightstore")
        try:
            self._req("POST", "/console/api/init", {"password": init_password})
            print("[init] init validation ok")
        except Exception as exc:
            print(f"[init] 跳过（可能已完成初始化）：{exc}")

    def setup(self, name: str = "夜班店长") -> None:
        try:
            resp = self._req(
                "POST",
                "/console/api/setup",
                {"email": EMAIL, "name": name, "password": PASSWORD},
            )
            print(f"[setup] {resp}")
        except Exception as exc:
            print(f"[setup] 跳过（可能已完成初始化）：{exc}")

    def login(self) -> None:
        self.init_validate()
        # Dify 1.18 的登录接口要求 password 字段是 base64 编码（libs/encryption.py）
        import base64

        self._req(
            "POST",
            "/console/api/login",
            {
                "email": EMAIL,
                "password": base64.b64encode(PASSWORD.encode("utf-8")).decode("utf-8"),
                "language": "zh-Hans",
                "timezone": "Asia/Shanghai",
            },
        )
        if not self._cookies():
            raise RuntimeError("登录失败：未拿到会话 cookie")
        print(f"[login] ok as {EMAIL}")

    def import_dsl(self, path: pathlib.Path, name: str | None = None) -> dict:
        content = path.read_text(encoding="utf-8")
        payload = {
            "mode": "yaml-content",
            "yaml_content": content,
            "name": name,
            "description": "午夜便利店·阿夜的账本",
            "icon": "🏪",
            "icon_background": "#FFEAD5",
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        resp = self._req("POST", "/console/api/apps/imports", payload)
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:2000])
        return resp

    def list_apps(self) -> list[dict]:
        resp = self._req("GET", "/console/api/apps?page=1&limit=50")
        return resp.get("data") or []

    def export_dsl(self, app_id: str) -> str:
        """导出应用：Dify 1.18 的 export 返回 .ifpkg（zip），里面 app.yaml 就是 DSL。"""
        import io
        import zipfile

        raw = self._req("GET", f"/console/api/apps/{app_id}/export?mode=yaml", raw=True)
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    if name.endswith(".yaml") and "manifest" not in name:
                        return z.read(name).decode("utf-8")
            raise RuntimeError("导出包里没有 app.yaml")
        return raw.decode("utf-8", "ignore")

    def create_api_key(self, app_id: str) -> str:
        resp = self._req("POST", f"/console/api/apps/{app_id}/api-keys", {})
        return resp.get("token") or ""

    def api_key_list(self, app_id: str) -> list[dict]:
        resp = self._req("GET", f"/console/api/apps/{app_id}/api-keys")
        return resp.get("data") or []


def service_chat(api_key: str, query: str, user: str = "player_demo",
                 conversation_id: str = "", inputs: dict | None = None) -> dict:
    # Service API 走 nginx（宿主 80 端口）或直连 api（5001）
    base = os.getenv("DIFY_SERVICE_BASE", CONSOLE)
    url = base.rstrip("/") + "/v1/chat-messages"
    payload = {
        "query": query,
        "response_mode": "blocking",
        "user": user,
        "inputs": inputs or {},
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_import(args) -> int:
    c = Console()
    c.login()
    resp = c.import_dsl(pathlib.Path(args.path) if args.path else DSL_PATH, args.name)
    app_id = resp.get("app_id") or (resp.get("app") or {}).get("id") or resp.get("id")
    if app_id:
        print(f"[import] app_id={app_id}")
        print(f"[import] 导出地址：{CONSOLE}/console/api/apps/{app_id}/export?mode=yaml")
    return 0


def cmd_export(args) -> int:
    c = Console()
    c.login()
    yaml_text = c.export_dsl(args.app_id)
    out = pathlib.Path(args.out) if args.out else DSL_PATH
    out.write_text(yaml_text, encoding="utf-8")
    print(f"[export] written: {out} ({len(yaml_text)} bytes)")
    return 0


def cmd_chat(args) -> int:
    c = Console()
    c.login()
    app_id = args.app_id
    token = ""
    for item in c.api_key_list(app_id):
        token = item.get("token") or ""
    if not token:
        token = c.create_api_key(app_id)
        print(f"[chat] created api key: {token}")
    resp = service_chat(
        token,
        args.query,
        user=args.user,
        conversation_id=args.conversation_id,
        inputs={"player_id": args.player_id, "player_name": args.player_name},
    )
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_delete(args) -> int:
    """删除应用（清理重复导入）。"""
    c = Console()
    c.login()
    try:
        c._req("DELETE", f"/console/api/apps/{args.app_id}")
        print(f"[delete] {args.app_id} ok")
    except Exception as exc:
        print(f"[delete] {exc}")
    return 0


def cmd_publish(args) -> int:
    """发布应用（发布后才能用 Service API 调用）。"""
    c = Console()
    c.login()
    resp = c._req("POST", f"/console/api/apps/{args.app_id}/workflows/publish", {})
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:800])
    return 0


def cmd_find_app(args) -> int:
    """按名字找应用（返回 app_id）。"""
    c = Console()
    c.login()
    for app in c.list_apps():
        if args.name in str(app.get("name", "")):
            print(f"{app.get('id')}  {app.get('name')}  mode={app.get('mode')}")
        else:
            print(f"{app.get('id')}  {app.get('name')}  mode={app.get('mode')}")
    return 0


def cmd_trace(args) -> int:
    c = Console()
    c.login()
    resp = c._req("GET", f"/console/api/apps/{args.app_id}/workflow-runs?page=1&limit=10")
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:6000])
    return 0


def cmd_setup(args) -> int:
    """初始化 Dify 账号（首次部署时调用一次）。"""
    c = Console()
    c.init_validate()
    payload = {"email": EMAIL, "name": args.name, "password": PASSWORD}
    try:
        resp = c._req("POST", "/console/api/setup", payload)
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:1200])
    except Exception as exc:
        print(f"[setup] {exc}")
        return 1
    return 0


def cmd_plugins(args) -> int:
    """列出已安装插件（确认模型供应商插件是否就绪）。"""
    c = Console()
    c.login()
    resp = c._req("GET", "/console/api/workspaces/current/plugin/list?page=1&limit=100")
    data = resp.get("data") or resp.get("plugins") or []
    if isinstance(data, dict):
        data = data.get("plugins") or []
    for p in data:
        print(f"- {p.get('plugin_id')} | {p.get('name')} | {p.get('latest_version')}")
    return 0


def cmd_marketplace(args) -> int:
    """从插件市场查询可安装的模型供应商插件。"""
    c = Console()
    c.login()
    resp = c._req(
        "GET",
        "/console/api/workspaces/current/plugin/model/list?page=1&page_size=100",
    )
    data = resp.get("data") or {}
    plugins = data.get("plugins") or data.get("list") or []
    if isinstance(data, list):
        plugins = data
    for p in plugins:
        ident = (p.get("latest_package_identifier") or {})
        print(
            f"- {p.get('name')} | id={p.get('plugin_id')} | ver={p.get('latest_version')} "
            f"| unique={ident.get('plugin_unique_identifier')}"
        )
    return 0


def cmd_install_plugin(args) -> int:
    """安装插件（模型供应商）。"""
    c = Console()
    c.login()
    resp = c._req(
        "POST",
        "/console/api/workspaces/current/plugin/install/marketplace",
        {"plugin_unique_identifiers": [args.identifier]},
    )
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:1500])
    return 0


def cmd_apps(args) -> int:
    c = Console()
    c.login()
    for app in c.list_apps():
        print(f"- {app.get('id')}  {app.get('name')}  mode={app.get('mode')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import")
    p.add_argument("--path")
    p.add_argument("--name")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export")
    p.add_argument("--app-id", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("chat")
    p.add_argument("--app-id", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--player-id", default="player_demo")
    p.add_argument("--player-name", default="小李")
    p.add_argument("--user", default="player_demo")
    p.add_argument("--conversation-id", default="")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("delete")
    p.add_argument("--app-id", required=True)
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("publish")
    p.add_argument("--app-id", required=True)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("find-app")
    p.add_argument("--name", default="")
    p.set_defaults(func=cmd_find_app)

    p = sub.add_parser("trace")
    p.add_argument("--app-id", required=True)
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("setup")
    p.add_argument("--name", default="夜班店长")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("plugins")
    p.set_defaults(func=cmd_plugins)

    p = sub.add_parser("marketplace")
    p.set_defaults(func=cmd_marketplace)

    p = sub.add_parser("install-plugin")
    p.add_argument("identifier")
    p.set_defaults(func=cmd_install_plugin)

    p = sub.add_parser("apps")
    p.set_defaults(func=cmd_apps)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
